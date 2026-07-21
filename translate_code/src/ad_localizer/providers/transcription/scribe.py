"""ElevenLabs Scribe transcription: verbatim, punctuated, acoustic timestamps.

Scribe returns full sentence punctuation, keeps filler words, and its word
timestamps are acoustic (real gaps between words survive), so downstream
segmentation can trust sentences and derive pauses directly from the word
gaps - no separate VAD or LLM re-punctuation needed.

Billing: by audio duration (roughly 330 credits/minute on subscription
plans), unlike TTS which bills per character.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from ...ffmpeg_utils import extract_audio, has_audio_stream
from ...models import Transcript, WordTiming
from ..base import TranscriptionProvider

_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class ScribeTranscriptionProvider(TranscriptionProvider):
    def __init__(self, api_key: str, model_id: str = "scribe_v1") -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from elevenlabs.client import AsyncElevenLabs  # lazy import

            self._client = AsyncElevenLabs(api_key=self._api_key)
        return self._client

    async def transcribe(self, video_or_audio: Path) -> Transcript:
        if not video_or_audio.exists():
            raise FileNotFoundError(video_or_audio)
        if video_or_audio.suffix.lower() in _AUDIO_SUFFIXES:
            return await self._transcribe_audio(video_or_audio)
        if not await asyncio.to_thread(has_audio_stream, video_or_audio):
            raise ValueError(f"{video_or_audio} has no audio stream; nothing to transcribe.")
        with tempfile.TemporaryDirectory() as tmp:
            wav = await asyncio.to_thread(
                extract_audio, video_or_audio, Path(tmp) / f"{video_or_audio.stem}.wav"
            )
            return await self._transcribe_audio(wav)

    async def _transcribe_audio(self, audio: Path) -> Transcript:
        client = self._get_client()
        with audio.open("rb") as f:
            result = await client.speech_to_text.convert(file=f, model_id=self._model_id)
        words = [
            WordTiming(word=w.text, start=float(w.start), end=float(w.end))
            for w in (result.words or [])
            if getattr(w, "type", "word") == "word"
        ]
        language = (getattr(result, "language_code", None) or "en").split("-")[0]
        return Transcript(text=(result.text or "").strip(), language=language, words=words)
