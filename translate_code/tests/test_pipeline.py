"""End-to-end pipeline tests using only fake providers (no network, no spend).

Skips cleanly until the integrator lands src/ad_localizer/pipeline.py.
"""

from pathlib import Path

import pytest

pipeline_module = pytest.importorskip("ad_localizer.pipeline")
Pipeline = pipeline_module.Pipeline

from ad_localizer.models import (  # noqa: E402
    AudioTrack,
    LocalizationJob,
    Transcript,
    TranslatedScript,
)
from ad_localizer.providers.fakes import (  # noqa: E402
    FakeLipsyncProvider,
    FakeOnScreenTextProvider,
    FakeTranscriptionProvider,
    FakeTranslationProvider,
    FakeVoiceProvider,
)


@pytest.fixture(autouse=True)
def no_real_ffmpeg(monkeypatch):
    """Stub every ffmpeg_utils entry point so no real ffmpeg/ffprobe runs."""

    def fake_extract_reference_clip(video, out_path, **kwargs):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00fake reference mp3")
        return out_path

    def fake_extract_audio(video, out_wav, **kwargs):
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(b"RIFF fake wav")
        return out_wav

    def fake_mux(video, audio, out_video):
        out_video.parent.mkdir(parents=True, exist_ok=True)
        out_video.write_bytes(Path(video).read_bytes())
        return out_video

    ff = "ad_localizer.ffmpeg_utils"
    monkeypatch.setattr(f"{ff}.ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(f"{ff}.probe_duration", lambda media: 10.0)
    monkeypatch.setattr(f"{ff}.has_audio_stream", lambda media: True)
    monkeypatch.setattr(f"{ff}.extract_reference_clip", fake_extract_reference_clip)
    monkeypatch.setattr(f"{ff}.extract_audio", fake_extract_audio)
    monkeypatch.setattr(f"{ff}.mux_replace_audio", fake_mux)
    monkeypatch.setattr(f"{ff}.extract_frames", lambda video, out_dir, **kw: [])

    # Make the OCR "does this ad have burned-in text?" pre-check deterministic
    # (it may otherwise shell out to ffmpeg / OCR): always report text present
    # so the on-screen stage runs unless explicitly skipped.
    if hasattr(Pipeline, "_needs_onscreen_text"):
        async def always_needs_text(self, job):
            return True

        monkeypatch.setattr(Pipeline, "_needs_onscreen_text", always_needs_text)


@pytest.fixture
def job(tmp_path: Path) -> LocalizationJob:
    source = tmp_path / "source_ad.mp4"
    source.write_bytes(b"\x00\x01\x02 fake mp4 bytes " * 32)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return LocalizationJob(source_video=source, target_language="es", work_dir=work_dir)


def make_fakes():
    return {
        "transcription": FakeTranscriptionProvider(),
        "translation": FakeTranslationProvider(),
        "voice": FakeVoiceProvider(),
        "lipsync": FakeLipsyncProvider(),
        "onscreen_text": FakeOnScreenTextProvider(),
    }


async def test_run_populates_all_fields(job):
    fakes = make_fakes()
    result = await Pipeline(**fakes).run(job)

    assert result is job  # mutated and returned

    assert isinstance(job.transcript, Transcript)
    assert job.transcript.language == "en"

    assert isinstance(job.translated, TranslatedScript)
    assert job.translated.target_language == "es"
    assert job.translated.text.startswith("[es]")

    assert isinstance(job.dubbed_audio, AudioTrack)
    assert job.dubbed_audio.path.exists()
    assert job.cloned_voice_id == "fake-voice"
    assert job.dubbed_audio.voice_id == "fake-voice"

    assert isinstance(job.lipsynced_video, Path)
    assert job.lipsynced_video.exists()

    assert isinstance(job.final_video, Path)
    assert job.final_video.exists()

    assert isinstance(job.warnings, list)


async def test_run_calls_stages_in_order(job):
    order: list[str] = []
    fakes = make_fakes()

    class OrderedTranscription(FakeTranscriptionProvider):
        async def transcribe(self, video_or_audio):
            order.append("transcribe")
            return await super().transcribe(video_or_audio)

    class OrderedTranslation(FakeTranslationProvider):
        async def translate(self, transcript, target_language, context=None):
            order.append("translate")
            return await super().translate(transcript, target_language, context)

    class OrderedVoice(FakeVoiceProvider):
        async def clone_and_synthesize(self, script, reference_audio, work_dir):
            order.append("voice")
            return await super().clone_and_synthesize(script, reference_audio, work_dir)

    class OrderedLipsync(FakeLipsyncProvider):
        async def lipsync(self, video, audio, work_dir):
            order.append("lipsync")
            return await super().lipsync(video, audio, work_dir)

    class OrderedOnScreen(FakeOnScreenTextProvider):
        async def localize_text(self, video, target_language, work_dir):
            order.append("onscreen_text")
            return await super().localize_text(video, target_language, work_dir)

    fakes = {
        "transcription": OrderedTranscription(),
        "translation": OrderedTranslation(),
        "voice": OrderedVoice(),
        "lipsync": OrderedLipsync(),
        "onscreen_text": OrderedOnScreen(),
    }
    await Pipeline(**fakes).run(job)

    core = [s for s in order if s != "onscreen_text"]
    assert core == ["transcribe", "translate", "voice", "lipsync"]
    assert order.count("onscreen_text") <= 1


async def test_artifacts_written_under_work_dir(job):
    fakes = make_fakes()
    await Pipeline(**fakes).run(job)

    for artifact in (job.dubbed_audio.path, job.lipsynced_video, job.final_video):
        assert job.work_dir in artifact.parents or artifact.parent == job.work_dir, (
            f"{artifact} not under work_dir {job.work_dir}"
        )


async def test_skip_flag_skips_onscreen_text(job):
    fakes = make_fakes()
    onscreen = fakes["onscreen_text"]
    await Pipeline(**fakes, skip_onscreen_text=True).run(job)

    assert onscreen.calls == [], "onscreen_text stage ran despite skip_onscreen_text=True"
    assert job.final_video is not None
    assert job.final_video.exists()


async def test_no_onscreen_provider_skips_stage(job):
    fakes = make_fakes()
    fakes["onscreen_text"] = None
    result = await Pipeline(**fakes).run(job)

    assert result.final_video is not None
    assert result.final_video.exists()


async def test_force_onscreen_text_runs_stage(job):
    fakes = make_fakes()
    onscreen = fakes["onscreen_text"]
    await Pipeline(**fakes, force_onscreen_text=True).run(job)

    assert len(onscreen.calls) == 1
    assert onscreen.calls[0][1] == "es"
