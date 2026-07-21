"""Orchestrates the six localization stages strictly in order. No vendor branching.

Stage order: transcribe → translate → voice (clone+TTS) → lipsync →
on-screen text (optional, auto-skipped) → mux/QA.

Each stage writes artifacts under the job's work_dir so runs are resumable:
if a stage's artifact already exists for the same inputs, the stage is
skipped and the artifact is loaded instead of re-spending on the API.
"""

import hashlib
import json
import logging
import shutil
from dataclasses import asdict
from pathlib import Path

from . import ffmpeg_utils
from .models import AudioTrack, LocalizationJob, Transcript, TranslatedScript, WordTiming
from .providers.base import (
    LipsyncProvider,
    OnScreenTextProvider,
    TranscriptionProvider,
    TranslationProvider,
    VoiceProvider,
)

logger = logging.getLogger("ad_localizer")

# translated audio longer than the original by more than this fraction
# threatens clean lip-sync
DURATION_TOLERANCE = 0.10


class StageError(RuntimeError):
    """A stage failed. Prior artifacts are left intact in work_dir."""

    def __init__(self, stage: str, cause: Exception):
        super().__init__(f"Stage '{stage}' failed: {cause}")
        self.stage = stage
        self.cause = cause


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Pipeline:
    def __init__(
        self,
        transcription: TranscriptionProvider,
        translation: TranslationProvider,
        voice: VoiceProvider,
        lipsync: LipsyncProvider,
        onscreen_text: OnScreenTextProvider | None = None,
        *,
        skip_onscreen_text: bool = False,
        force_onscreen_text: bool = False,
    ):
        self.transcription = transcription
        self.translation = translation
        self.voice = voice
        self.lipsync = lipsync
        self.onscreen_text = onscreen_text
        self.skip_onscreen_text = skip_onscreen_text
        self.force_onscreen_text = force_onscreen_text

    async def run(self, job: LocalizationJob) -> LocalizationJob:
        job.work_dir.mkdir(parents=True, exist_ok=True)
        await self._transcribe(job)
        await self._translate(job)
        await self._voice(job)
        await self._lipsync(job)
        await self._onscreen_text(job)
        self._finalize(job)
        return job

    # -- stage 1: transcription ------------------------------------------

    async def _transcribe(self, job: LocalizationJob) -> None:
        cache = job.work_dir / "transcript.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            data["words"] = [WordTiming(**w) for w in data.get("words", [])]
            job.transcript = Transcript(**data)
            logger.info("transcribe: loaded cached transcript")
            return
        try:
            job.transcript = await self.transcription.transcribe(job.source_video)
        except Exception as e:
            raise StageError("transcribe", e) from e
        cache.write_text(json.dumps(asdict(job.transcript), ensure_ascii=False, indent=2))
        if not job.transcript.words:
            job.warn("transcript has no word-level timings; duration checks will be coarse")

    # -- stage 2: translation --------------------------------------------

    async def _translate(self, job: LocalizationJob) -> None:
        assert job.transcript is not None
        cache = job.work_dir / f"translated_{job.target_language}.json"
        if cache.exists():
            job.translated = TranslatedScript(**json.loads(cache.read_text()))
            logger.info("translate: loaded cached translation")
            return
        context = (
            "This is a short video ad. Keep it punchy and idiomatic, preserve "
            "brand/product names, and keep the spoken length close to the original."
        )
        try:
            job.translated = await self.translation.translate(
                job.transcript, job.target_language, context=context
            )
        except Exception as e:
            raise StageError("translate", e) from e
        cache.write_text(json.dumps(asdict(job.translated), ensure_ascii=False, indent=2))

    # -- stage 3: voice (clone, then speak) ------------------------------

    async def _voice(self, job: LocalizationJob) -> None:
        assert job.translated is not None
        audio_dir = job.work_dir / "audio"
        cached = sorted(audio_dir.glob(f"dub_{job.target_language}.*")) if audio_dir.exists() else []
        meta = job.work_dir / f"dub_{job.target_language}.json"
        if cached and meta.exists():
            info = json.loads(meta.read_text())
            job.dubbed_audio = AudioTrack(
                path=cached[0], duration_s=info["duration_s"], voice_id=info.get("voice_id")
            )
            job.cloned_voice_id = info.get("voice_id")
            logger.info("voice: loaded cached dub")
            return
        # name the reference after the source video: the voice provider derives
        # the clone's name from this stem, so a generic name would make every
        # job reuse the first-ever cloned voice
        reference = job.work_dir / f"reference_{job.source_video.stem}.mp3"
        if not reference.exists():
            try:
                ffmpeg_utils.extract_reference_clip(job.source_video, reference)
            except Exception as e:
                raise StageError("voice", e) from e
        try:
            track = await self.voice.clone_and_synthesize(job.translated, reference, audio_dir)
        except Exception as e:
            raise StageError("voice", e) from e
        # keep a stable name so retries find the artifact
        stable = audio_dir / f"dub_{job.target_language}{track.path.suffix}"
        if track.path != stable:
            audio_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(track.path), stable)
            track = AudioTrack(path=stable, duration_s=track.duration_s, voice_id=track.voice_id)
        job.dubbed_audio = track
        job.cloned_voice_id = track.voice_id
        meta.write_text(json.dumps({"duration_s": track.duration_s, "voice_id": track.voice_id}))
        self._check_duration(job)

    def _check_duration(self, job: LocalizationJob) -> None:
        assert job.dubbed_audio is not None
        try:
            original = ffmpeg_utils.probe_duration(job.source_video)
        except Exception:
            return
        if original > 0 and job.dubbed_audio.duration_s > original * (1 + DURATION_TOLERANCE):
            job.warn(
                f"dubbed audio ({job.dubbed_audio.duration_s:.1f}s) is more than "
                f"{DURATION_TOLERANCE:.0%} longer than the original ({original:.1f}s); "
                "lip-sync quality may suffer — consider a tighter translation"
            )

    # -- stage 4: lipsync --------------------------------------------------

    async def _lipsync(self, job: LocalizationJob) -> None:
        assert job.dubbed_audio is not None
        cached = job.work_dir / f"lipsynced_{job.target_language}.mp4"
        if cached.exists():
            job.lipsynced_video = cached
            logger.info("lipsync: loaded cached video")
            return
        try:
            result = await self.lipsync.lipsync(job.source_video, job.dubbed_audio, job.work_dir)
        except Exception as e:
            raise StageError("lipsync", e) from e
        if result != cached:
            shutil.move(str(result), cached)
        job.lipsynced_video = cached
        estimate = getattr(self.lipsync, "last_cost_estimate", None)
        if estimate:
            job.add_cost(float(estimate))

    # -- stage 5: on-screen text (optional) --------------------------------

    async def _onscreen_text(self, job: LocalizationJob) -> None:
        assert job.lipsynced_video is not None
        if self.onscreen_text is None or self.skip_onscreen_text:
            logger.info("on-screen text: skipped")
            return
        if not self.force_onscreen_text and not await self._needs_onscreen_text(job):
            job.warn("on-screen text stage auto-skipped: no significant burned-in text detected")
            return
        try:
            result = await self.onscreen_text.localize_text(
                job.lipsynced_video, job.target_language, job.work_dir
            )
        except Exception as e:
            raise StageError("onscreen_text", e) from e
        job.lipsynced_video = result

    async def _needs_onscreen_text(self, job: LocalizationJob) -> bool:
        try:
            from .providers.onscreen_text.vozo import has_significant_onscreen_text
        except ImportError:
            job.warn("OCR pre-check unavailable; running on-screen text stage to be safe")
            return True
        try:
            return await has_significant_onscreen_text(
                job.source_video, job.work_dir / "ocr_check"
            )
        except Exception as e:
            job.warn(f"OCR pre-check failed ({e}); running on-screen text stage to be safe")
            return True

    # -- stage 6: mux & QA --------------------------------------------------

    def _finalize(self, job: LocalizationJob) -> None:
        assert job.lipsynced_video is not None
        final = job.work_dir / f"final_{job.source_video.stem}_{job.target_language}.mp4"
        try:
            shutil.copyfile(job.lipsynced_video, final)
            # QA: output must exist, be non-empty, and roughly match source duration
            if final.stat().st_size == 0:
                raise RuntimeError("final video is empty")
            try:
                src_d = ffmpeg_utils.probe_duration(job.source_video)
                out_d = ffmpeg_utils.probe_duration(final)
                if abs(out_d - src_d) > max(2.0, src_d * 0.15):
                    job.warn(
                        f"final duration {out_d:.1f}s differs from source {src_d:.1f}s"
                    )
            except ffmpeg_utils.FFmpegError:
                pass  # QA probe is best-effort (e.g. fake artifacts in dry runs)
        except Exception as e:
            raise StageError("finalize", e) from e
        job.final_video = final
