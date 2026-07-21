"""Local transcription via faster-whisper.

Runs Whisper on-device (no API key, no per-minute cost). The model download
and inference are blocking, so both happen inside asyncio.to_thread; the
faster_whisper import is deferred so importing this module never triggers a
model download or pulls in ctranslate2.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from ...config import AppConfig
from ...ffmpeg_utils import extract_audio, has_audio_stream
from ...models import Transcript, WordTiming
from ..base import TranscriptionProvider

logger = logging.getLogger(__name__)

# Inputs with these suffixes are fed to Whisper directly; anything else is
# treated as a video and has its audio track extracted first.
_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class WhisperTranscriptionProvider(TranscriptionProvider):
    """TranscriptionProvider backed by a local faster-whisper model.

    ``model_size`` is a faster-whisper size/name string ("tiny" … "large-v3");
    it defaults to AppConfig.whisper_model. ``work_dir`` is where extracted
    WAVs are written; when omitted a temporary directory is used and cleaned
    up after each call.
    """

    def __init__(self, model_size: str | None = None, work_dir: Path | None = None):
        self._model_size = model_size or AppConfig().whisper_model
        self._work_dir = work_dir
        self._model = None  # loaded lazily on first transcribe()

    async def transcribe(self, video_or_audio: Path) -> Transcript:
        if not video_or_audio.exists():
            raise FileNotFoundError(f"Input file not found: {video_or_audio}")
        if not await asyncio.to_thread(has_audio_stream, video_or_audio):
            raise ValueError(
                f"{video_or_audio} has no audio stream; nothing to transcribe."
            )

        if video_or_audio.suffix.lower() in _AUDIO_SUFFIXES:
            return await asyncio.to_thread(self._transcribe_blocking, video_or_audio)

        if self._work_dir is not None:
            wav = self._work_dir / f"{video_or_audio.stem}.16k.wav"
            await asyncio.to_thread(extract_audio, video_or_audio, wav)
            return await asyncio.to_thread(self._transcribe_blocking, wav)

        with tempfile.TemporaryDirectory(prefix="ad_localizer_asr_") as tmp:
            wav = Path(tmp) / f"{video_or_audio.stem}.16k.wav"
            await asyncio.to_thread(extract_audio, video_or_audio, wav)
            return await asyncio.to_thread(self._transcribe_blocking, wav)

    def _load_model(self):
        """Import faster_whisper and build the model on first use (blocking)."""
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self._model_size)
        return self._model

    def _transcribe_blocking(self, audio: Path) -> Transcript:
        """Run the model and drain its lazy segment generator (blocking).

        faster-whisper transcribes during iteration, so the whole loop must
        stay in the worker thread — not just the transcribe() call.
        """
        model = self._load_model()
        segments, info = model.transcribe(str(audio), word_timestamps=True)

        parts: list[str] = []
        words: list[WordTiming] = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                parts.append(text)
            for w in segment.words or []:
                words.append(
                    WordTiming(word=w.word.strip(), start=float(w.start), end=float(w.end))
                )

        if not words:
            logger.warning(
                "No speech detected in %s; returning an empty transcript.", audio
            )
        return Transcript(text=" ".join(parts), language=info.language or "", words=words)
