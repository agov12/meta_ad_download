"""ElevenLabs voice stage: instant voice clone (IVC) + multilingual TTS.

Clones the original speaker's voice from a reference clip, then synthesizes
the translated script in that voice. The cloned ``voice_id`` is exposed on
the provider (and on the returned AudioTrack) so the pipeline can cache it
across target languages and skip re-cloning.

Consent / ToS note: ElevenLabs' terms require explicit permission from the
speaker to clone their voice. The caller is responsible for ensuring they
hold the rights to the reference audio's voice before invoking this stage.

SDK surface (elevenlabs-python v2.x): ``AsyncElevenLabs``,
``voices.ivc.create`` for instant cloning (older 1.x releases exposed this
as ``voices.add``, handled as a fallback), ``voices.search`` for rerun
lookup, and ``text_to_speech.convert`` for synthesis.
"""

import asyncio
import inspect
from pathlib import Path
from typing import Any

from ...ffmpeg_utils import probe_duration
from ...models import AudioTrack, TranslatedScript
from ..base import VoiceProvider

# Deterministic prefix so reruns can find (and reuse) a previously cloned voice.
_VOICE_NAME_PREFIX = "ad-localizer-"
_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsVoiceProvider(VoiceProvider):
    """Clone-then-speak via ElevenLabs.

    If ``voice_id`` is provided up front (e.g. from the pipeline's cache of
    voices cloned for earlier target languages), cloning is skipped and that
    voice is reused. After a run, read ``self.voice_id`` (also mirrored on
    the returned ``AudioTrack.voice_id``) to cache it.
    """

    def __init__(
        self,
        api_key: str,
        tts_model: str = "eleven_multilingual_v2",
        voice_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._tts_model = tts_model
        self.voice_id = voice_id
        self._client: Any = None

    @staticmethod
    def voice_name_for(reference_audio: Path) -> str:
        """Deterministic clone name for a reference clip (stable across reruns)."""
        return f"{_VOICE_NAME_PREFIX}{reference_audio.stem}"

    def _get_client(self) -> Any:
        """Lazily construct the async SDK client (keeps module import cheap)."""
        if self._client is None:
            from elevenlabs.client import AsyncElevenLabs

            self._client = AsyncElevenLabs(api_key=self._api_key)
        return self._client

    async def clone_and_synthesize(
        self,
        script: TranslatedScript,
        reference_audio: Path,
        work_dir: Path,
    ) -> AudioTrack:
        client = self._get_client()
        if self.voice_id is None:
            self.voice_id = await self._obtain_voice(client, reference_audio)

        out_path = work_dir / f"dubbed_{script.target_language}.mp3"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await self._synthesize(client, script.text, out_path)

        duration_s = await asyncio.to_thread(probe_duration, out_path)
        return AudioTrack(path=out_path, duration_s=duration_s, voice_id=self.voice_id)

    async def ensure_voice(self, reference_audio: Path) -> str:
        """Clone (or find) the voice without synthesizing; sets/returns voice_id.

        Used by segment-aligned dubbing, which synthesizes many small clips
        with `synthesize_text` after securing the voice once.
        """
        if self.voice_id is None:
            self.voice_id = await self._obtain_voice(self._get_client(), reference_audio)
        return self.voice_id

    async def synthesize_text(
        self,
        text: str,
        out_path: Path,
        *,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AudioTrack:
        """TTS one piece of text in the already-secured voice (see ensure_voice).

        previous_text/next_text enable ElevenLabs request stitching: when a
        script is synthesized as many segments, passing each segment's
        surrounding lines keeps prosody continuous so all clips sound like
        one read instead of isolated takes.
        """
        if self.voice_id is None:
            raise RuntimeError("no voice_id set - call ensure_voice() first")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await self._synthesize(
            self._get_client(), text, out_path,
            previous_text=previous_text, next_text=next_text,
        )
        duration_s = await asyncio.to_thread(probe_duration, out_path)
        return AudioTrack(path=out_path, duration_s=duration_s, voice_id=self.voice_id)

    async def _obtain_voice(self, client: Any, reference_audio: Path) -> str:
        """Find a previously cloned voice by its deterministic name, else IVC-clone."""
        name = self.voice_name_for(reference_audio)
        existing = await self._find_voice_by_name(client, name)
        if existing is not None:
            return existing
        with reference_audio.open("rb") as sample:
            ivc = getattr(client.voices, "ivc", None)
            if ivc is not None:
                created = await ivc.create(name=name, files=[sample])
            else:
                # elevenlabs < 2.0 exposed instant cloning as voices.add
                created = await client.voices.add(name=name, files=[sample])
        return created.voice_id

    @staticmethod
    async def _find_voice_by_name(client: Any, name: str) -> str | None:
        search = getattr(client.voices, "search", None)
        if search is None:
            return None
        response = await search(search=name)
        for voice in response.voices or []:
            if voice.name == name:
                return voice.voice_id
        return None

    async def _synthesize(
        self,
        client: Any,
        text: str,
        out_path: Path,
        *,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> None:
        # constant settings on every request so multi-segment dubs keep one
        # consistent voice character (speed stays 1.0; timing fit is handled
        # downstream within a narrow uniform tempo band)
        kwargs: dict[str, Any] = {
            "voice_id": self.voice_id,
            "text": text,
            "model_id": self._tts_model,
            "output_format": _OUTPUT_FORMAT,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.0,
            },
        }
        # request stitching (not supported by eleven_v3)
        if previous_text:
            kwargs["previous_text"] = previous_text
        if next_text:
            kwargs["next_text"] = next_text
        try:
            audio = client.text_to_speech.convert(**kwargs)
        except TypeError:
            # older SDKs without voice_settings/stitching kwargs
            audio = client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                model_id=self._tts_model,
                output_format=_OUTPUT_FORMAT,
            )
        # The SDK has returned bytes, an iterator, or an async iterator of
        # chunks across versions; normalize all three.
        if inspect.isawaitable(audio):
            audio = await audio
        with out_path.open("wb") as out:
            if isinstance(audio, (bytes, bytearray)):
                out.write(audio)
            elif hasattr(audio, "__aiter__"):
                async for chunk in audio:
                    if chunk:
                        out.write(chunk)
            else:
                for chunk in audio:
                    if chunk:
                        out.write(chunk)
