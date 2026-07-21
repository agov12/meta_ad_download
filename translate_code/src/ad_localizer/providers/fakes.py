"""Fake providers for tests and --dry-run mode.

Every fake implements one of the ABCs in providers/base.py, touches no
network, spends no money, and finishes instantly (sleep 0). Each fake also
records its calls in ``self.calls`` so tests can spy on which stages ran.
"""

import asyncio
import shutil
import wave
from pathlib import Path

from ..models import AudioTrack, Transcript, TranslatedScript, WordTiming
from .base import (
    LipsyncProvider,
    OnScreenTextProvider,
    TranscriptionProvider,
    TranslationProvider,
    VoiceProvider,
)


class FakeTranscriptionProvider(TranscriptionProvider):
    """Returns a canned English transcript with a few word timings."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def transcribe(self, video_or_audio: Path) -> Transcript:
        await asyncio.sleep(0)
        self.calls.append(video_or_audio)
        return Transcript(
            text="Buy our amazing product today",
            language="en",
            words=[
                WordTiming(word="Buy", start=0.0, end=0.3),
                WordTiming(word="our", start=0.3, end=0.5),
                WordTiming(word="amazing", start=0.5, end=1.0),
                WordTiming(word="product", start=1.0, end=1.5),
                WordTiming(word="today", start=1.5, end=2.0),
            ],
        )


class FakeTranslationProvider(TranslationProvider):
    """Wraps the source text in "[<target_language>] ..."."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def translate(
        self,
        transcript: Transcript,
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript:
        await asyncio.sleep(0)
        self.calls.append((transcript.text, target_language))
        return TranslatedScript(
            text=f"[{target_language}] {transcript.text}",
            source_language=transcript.language,
            target_language=target_language,
        )


class FakeVoiceProvider(VoiceProvider):
    """Writes a tiny valid silent WAV into work_dir via the wave stdlib module."""

    SAMPLE_RATE = 16000
    DURATION_S = 0.1

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    async def clone_and_synthesize(
        self,
        script: TranslatedScript,
        reference_audio: Path,
        work_dir: Path,
    ) -> AudioTrack:
        await asyncio.sleep(0)
        self.calls.append((script.text, reference_audio))
        work_dir.mkdir(parents=True, exist_ok=True)
        out_wav = work_dir / "fake_dubbed.wav"
        n_frames = int(self.SAMPLE_RATE * self.DURATION_S)
        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(b"\x00\x00" * n_frames)
        return AudioTrack(path=out_wav, duration_s=self.DURATION_S, voice_id="fake-voice")


class FakeLipsyncProvider(LipsyncProvider):
    """Copies the input video into work_dir as the 'lipsynced' result."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    async def lipsync(self, video: Path, audio: AudioTrack, work_dir: Path) -> Path:
        await asyncio.sleep(0)
        self.calls.append((video, audio.path))
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / f"fake_lipsynced{video.suffix or '.mp4'}"
        shutil.copyfile(video, out)
        return out


class FakeOnScreenTextProvider(OnScreenTextProvider):
    """Copies the input video into work_dir as the 'text-localized' result."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    async def localize_text(
        self, video: Path, target_language: str, work_dir: Path
    ) -> Path:
        await asyncio.sleep(0)
        self.calls.append((video, target_language))
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / f"fake_onscreen{video.suffix or '.mp4'}"
        shutil.copyfile(video, out)
        return out
