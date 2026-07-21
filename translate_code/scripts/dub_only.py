"""Dub-only run: transcribe → translate → ElevenLabs clone+TTS → ffmpeg mux.

Skips sync.so (lipsync) and Vozo (on-screen text). The 'lipsync' stage is
replaced by a plain ffmpeg audio overlay, so lips will NOT match the new
audio — this validates the transcribe/translate/voice stages end to end.

Usage: uv run python scripts/dub_only.py <video> [target_lang]
"""

import asyncio
import sys
from pathlib import Path

from ad_localizer import ffmpeg_utils
from ad_localizer.config import load_config, load_settings
from ad_localizer.models import AudioTrack, LocalizationJob
from ad_localizer.pipeline import Pipeline
from ad_localizer.providers.base import LipsyncProvider
from ad_localizer.providers.transcription.whisper import WhisperTranscriptionProvider
from ad_localizer.providers.translation.translator import OpenAITranslationProvider
from ad_localizer.providers.voice.elevenlabs import ElevenLabsVoiceProvider


class FfmpegMuxLipsyncProvider(LipsyncProvider):
    """No-lipsync stand-in: overlays the dubbed audio onto the original video.

    If the dubbed audio runs longer than the video, it is time-compressed
    (pitch-preserving atempo) to fit, so the end of the script is never
    truncated by the mux.
    """

    async def lipsync(self, video: Path, audio: AudioTrack, work_dir: Path) -> Path:
        video_dur = await asyncio.to_thread(ffmpeg_utils.probe_duration, video)
        audio_path = audio.path
        if audio.duration_s > video_dur:
            tempo = audio.duration_s / (video_dur - 0.1)
            fitted = work_dir / "audio" / f"{audio.path.stem}_fit.wav"
            await asyncio.to_thread(
                ffmpeg_utils._run,
                [
                    "ffmpeg", "-y", "-i", str(audio.path),
                    "-filter:a", f"atempo={tempo:.4f}", str(fitted),
                ],
            )
            print(
                f"note: dubbed audio ({audio.duration_s:.1f}s) compressed "
                f"{tempo:.2f}x to fit the {video_dur:.1f}s video"
            )
            audio_path = fitted
        out = work_dir / f"muxed_{video.stem}.mp4"
        await asyncio.to_thread(ffmpeg_utils.mux_replace_audio, video, audio_path, out)
        return out


async def main() -> None:
    video = Path(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else "es"
    settings = load_settings()
    config = load_config()
    assert settings.openai_api_key, "OPENAI_API_KEY missing in .env"
    assert settings.elevenlabs_api_key, "ELEVENLABS_API_KEY missing in .env"

    ffmpeg_utils.ensure_ffmpeg()
    job = LocalizationJob(
        source_video=video,
        target_language=target,
        work_dir=Path("work") / f"{video.stem}_{target}",
    )
    pipeline = Pipeline(
        WhisperTranscriptionProvider(model_size=config.whisper_model),
        OpenAITranslationProvider(api_key=settings.openai_api_key),
        ElevenLabsVoiceProvider(
            api_key=settings.elevenlabs_api_key, tts_model=config.elevenlabs_tts_model
        ),
        FfmpegMuxLipsyncProvider(),
        onscreen_text=None,
    )
    job = await pipeline.run(job)

    print(f"\nTranscript ({job.transcript.language}): {job.transcript.text}")
    print(f"\nTranslation ({target}): {job.translated.text}")
    print(f"\nCloned voice id: {job.cloned_voice_id}")
    print(f"Dubbed audio: {job.dubbed_audio.path} ({job.dubbed_audio.duration_s:.1f}s)")
    for w in job.warnings:
        print(f"warning: {w}")
    print(f"\nFinal video: {job.final_video}")


if __name__ == "__main__":
    asyncio.run(main())
