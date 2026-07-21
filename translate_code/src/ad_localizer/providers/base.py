"""Abstract base classes for each pipeline stage. These are THE CONTRACTS.

One concrete implementation per stage. The interfaces exist so a vendor can
be swapped later without rewriting the pipeline — not to support parallel
vendor choices. Do not add methods here without updating every
implementation and the pipeline together.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import AudioTrack, Transcript, TranslatedScript


class TranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(self, video_or_audio: Path) -> Transcript: ...


class TranslationProvider(ABC):
    @abstractmethod
    async def translate(
        self,
        transcript: Transcript,
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript: ...


class VoiceProvider(ABC):
    # Must (1) clone/obtain a voice from reference_audio, then
    # (2) synthesize the target-language script in THAT voice.
    @abstractmethod
    async def clone_and_synthesize(
        self,
        script: TranslatedScript,
        reference_audio: Path,
        work_dir: Path,
    ) -> AudioTrack: ...


class LipsyncProvider(ABC):
    @abstractmethod
    async def lipsync(self, video: Path, audio: AudioTrack, work_dir: Path) -> Path: ...


class OnScreenTextProvider(ABC):
    @abstractmethod
    async def localize_text(
        self, video: Path, target_language: str, work_dir: Path
    ) -> Path: ...
