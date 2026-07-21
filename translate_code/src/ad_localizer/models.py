"""Shared dataclasses passed between pipeline stages. These are THE CONTRACTS.

Every provider implementation and the pipeline itself code against these
types. Do not change them without updating providers/base.py and all
implementations together.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WordTiming:
    word: str
    start: float  # seconds
    end: float


@dataclass
class Transcript:
    text: str
    language: str
    words: list[WordTiming] = field(default_factory=list)


@dataclass
class TranslatedScript:
    text: str
    source_language: str
    target_language: str
    # optional segment-level alignment for timing-aware dubbing
    segments: list[dict] = field(default_factory=list)


@dataclass
class AudioTrack:
    path: Path
    duration_s: float
    voice_id: str | None = None  # the ElevenLabs cloned-voice id used


@dataclass
class LocalizationJob:
    source_video: Path
    target_language: str
    work_dir: Path
    transcript: Transcript | None = None
    translated: TranslatedScript | None = None
    cloned_voice_id: str | None = None
    dubbed_audio: AudioTrack | None = None
    lipsynced_video: Path | None = None
    final_video: Path | None = None
    warnings: list[str] = field(default_factory=list)
    # accumulated estimated spend across paid API calls, in USD
    estimated_cost_usd: float = 0.0

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def add_cost(self, usd: float) -> None:
        self.estimated_cost_usd += usd
