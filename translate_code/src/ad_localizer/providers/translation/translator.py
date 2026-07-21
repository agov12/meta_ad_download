"""Translation providers: LLM ad localization (primary) and DeepL (fallback).

LLMTranslationProvider treats the job as AD LOCALIZATION, not literal
translation: it keeps copy punchy, matches idiom and cultural register,
preserves brand/product names, and stays close to the original spoken
duration so the dubbed audio can fit the video.

DeepLTranslationProvider is a literal-translation baseline used when the
LLM path is unavailable.

Vendor SDKs are imported lazily so importing this module stays cheap.
"""

import asyncio

from ...models import Transcript, TranslatedScript
from ..base import TranslationProvider

DEFAULT_LLM_MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = (
    "You are an expert advertising localizer. You adapt ad voiceover scripts "
    "from one language to another for dubbing. This is localization, not "
    "literal translation: keep the copy punchy and persuasive, match the "
    "idiom and cultural register of the target audience, and preserve the "
    "rhythm of spoken ad copy. Keep brand names, product names, and "
    "trademarks verbatim. The translated script will be spoken over the "
    "original video, so its spoken duration must stay within about 10% of "
    "the original. Return ONLY the translated script text - no preamble, no "
    "quotes, no explanations, no markdown."
)

# DeepL requires regional variants for some target languages.
_DEEPL_TARGET_ALIASES = {
    "EN": "EN-US",
    "PT": "PT-BR",
    "ZH": "ZH-HANS",
}


class LLMTranslationProvider(TranslationProvider):
    """Primary path: ad-aware localization via the Anthropic API."""

    def __init__(self, api_key: str, model: str = DEFAULT_LLM_MODEL) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None  # lazily constructed AsyncAnthropic

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic  # lazy: keep module import cheap

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def translate(
        self,
        transcript: Transcript,
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript:
        text = transcript.text.strip()
        if not text:
            return TranslatedScript(
                text="",
                source_language=transcript.language,
                target_language=target_language,
            )

        client = self._get_client()
        response = await client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": self._build_prompt(transcript, target_language, context),
                }
            ],
        )
        translated = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        return TranslatedScript(
            text=translated,
            source_language=transcript.language,
            target_language=target_language,
        )

    @staticmethod
    def _build_prompt(
        transcript: Transcript, target_language: str, context: str | None
    ) -> str:
        word_count = len(transcript.text.split())
        parts = [
            f"Localize this ad script from '{transcript.language}' "
            f"to '{target_language}'.",
            f"The original script is {word_count} words long.",
        ]
        if transcript.words:
            duration_s = transcript.words[-1].end - transcript.words[0].start
            if duration_s > 0:
                # a natural TTS voice speaks ~2.2 words/sec; an explicit word
                # budget keeps the dub from overrunning the video
                budget = int(duration_s * 2.2)
                parts.append(
                    f"It is spoken over {duration_s:.1f} seconds of video. The "
                    "dubbed voice speaks at a natural pace (~2.2 words/second), "
                    f"so your translation must be AT MOST {budget} words. Cut "
                    "filler and redundancy aggressively to hit that budget while "
                    "keeping every selling point."
                )
        else:
            parts.append(
                "Keep the translation within about 10% of the original length "
                "so it can be spoken over the same video."
            )
        if context:
            parts.append(f"Additional context from the client: {context}")
        parts.append("Script:\n" + transcript.text.strip())
        parts.append("Respond with ONLY the translated script text.")
        return "\n\n".join(parts)


class OpenAITranslationProvider(TranslationProvider):
    """Ad-aware localization via the OpenAI API (same prompt as the LLM path).

    Uses httpx directly against the chat completions endpoint so no extra
    SDK dependency is needed.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._model = model

    async def translate(
        self,
        transcript: Transcript,
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript:
        text = transcript.text.strip()
        if not text:
            return TranslatedScript(
                text="",
                source_language=transcript.language,
                target_language=target_language,
            )

        import httpx  # lazy: keep module import cheap

        prompt = LLMTranslationProvider._build_prompt(
            transcript, target_language, context
        )
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        translated = data["choices"][0]["message"]["content"].strip()
        return TranslatedScript(
            text=translated,
            source_language=transcript.language,
            target_language=target_language,
        )

    async def refit_segment(
        self,
        source: str,
        translation: str,
        target_words: int,
        target_language: str,
    ) -> str:
        """Rewrite one segment's translation to ~target_words words.

        Used by the synthesis fit loop: after measuring the spoken duration
        of a translation, the caller computes how many words would fill the
        slot and asks for a version of that length - same meaning, same
        register, same language.
        """
        import json as _json

        import httpx  # lazy: keep module import cheap

        current = len(translation.split())
        direction = "shorter" if target_words < current else "longer"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"This '{target_language}' dub line must be {direction} "
                                f"to fit its video slot: rewrite it to about "
                                f"{target_words} words (currently {current}). Keep the "
                                "meaning, energy and register; it dubs this original "
                                f'line: "{source}".\n\nCurrent translation: '
                                f'"{translation}"\n\n'
                                'Respond with ONLY JSON: {"text": "<rewritten translation>"}'
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        payload = _json.loads(data["choices"][0]["message"]["content"])
        rewritten = str(payload.get("text", "")).strip()
        return rewritten or translation

    async def split_sentences(self, text: str) -> list[str]:
        """Split run-on transcript text into sentences, words preserved verbatim.

        Used by segmentation when a stretch of speech has no usable pauses
        (jump-cut ad reads): the sentence boundaries are then mapped back to
        word timestamps to form dub units.
        """
        import json as _json

        import httpx  # lazy: keep module import cheap

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Split this ad voiceover transcript into natural "
                                "spoken sentences. Copy the words EXACTLY as they "
                                "appear - do not add, remove, correct, or reorder "
                                "a single word; only decide where sentences break. "
                                'Respond with ONLY JSON: {"sentences": ["...", ...]}\n\n'
                                + text.strip()
                            ),
                        }
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        payload = _json.loads(data["choices"][0]["message"]["content"])
        return [str(s).strip() for s in payload.get("sentences", []) if str(s).strip()]

    async def translate_segments(
        self,
        transcript: Transcript,
        units: list,  # list[segmentation.DubUnit]
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript:
        """Segment-aligned translation: one target sentence per dub unit.

        Each unit is translated in place with its own time/word budget so the
        dubbed audio for unit i can start and stop when the original unit i
        did. Returns a TranslatedScript whose ``segments`` list carries the
        per-unit alignment: {index, start, end, source, target}.
        """
        if not units:
            return TranslatedScript(
                text="",
                source_language=transcript.language,
                target_language=target_language,
            )

        import json as _json

        import httpx  # lazy: keep module import cheap

        lines = []
        for i, unit in enumerate(units):
            # ~2.5 words/sec spoken pace: give the model each sentence's time
            # budget and an upper word bound as a concrete proxy for it
            hi = max(4, round(unit.duration * 2.5))
            lines.append(
                f'{i}. [spoken in {unit.duration:.1f}s, at most {hi} words] "{unit.text}"'
            )
        prompt_parts = [
            f"Translate this ad voiceover from '{transcript.language}' to "
            f"'{target_language}' sentence by sentence, so that it sounds "
            f"natural as a '{target_language}' ad - idiomatic, punchy spoken "
            "ad copy, not a literal rendering.",
            "Full script for context:\n" + transcript.text.strip(),
            "Sentences to translate. Each translation will be dubbed over "
            "the original video, so it must be speakable in the SAME amount "
            "of time as the original sentence or LESS - never longer. "
            "Slightly shorter is fine; over is not. The word maximum for "
            "each sentence is a hard cap:\n" + "\n".join(lines),
            'Respond with ONLY a JSON object: {"segments": ["<translation of '
            'segment 0>", "<translation of segment 1>", ...]} - exactly '
            f"{len(units)} strings, in order, no other keys.",
        ]
        if context:
            prompt_parts.insert(1, f"Additional context from the client: {context}")

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": "\n\n".join(prompt_parts)},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        payload = _json.loads(data["choices"][0]["message"]["content"])
        targets = payload.get("segments", [])
        if len(targets) != len(units):
            raise ValueError(
                f"segment count mismatch: sent {len(units)} segments, "
                f"got {len(targets)} translations back"
            )
        segments = [
            {
                "index": i,
                "start": unit.start,
                "end": unit.end,
                "source": unit.text,
                "target": str(target).strip(),
            }
            for i, (unit, target) in enumerate(zip(units, targets))
        ]
        return TranslatedScript(
            text=" ".join(s["target"] for s in segments),
            source_language=transcript.language,
            target_language=target_language,
            segments=segments,
        )


class DeepLTranslationProvider(TranslationProvider):
    """Fallback: literal baseline translation via DeepL."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._translator = None  # lazily constructed deepl.Translator

    def _get_translator(self):
        if self._translator is None:
            import deepl  # lazy: keep module import cheap

            self._translator = deepl.Translator(self._api_key)
        return self._translator

    async def translate(
        self,
        transcript: Transcript,
        target_language: str,
        context: str | None = None,
    ) -> TranslatedScript:
        # DeepL is a literal baseline; `context` is accepted for interface
        # compatibility but not used.
        text = transcript.text.strip()
        if not text:
            return TranslatedScript(
                text="",
                source_language=transcript.language,
                target_language=target_language,
            )

        translator = self._get_translator()
        target = target_language.upper()
        target = _DEEPL_TARGET_ALIASES.get(target, target)
        source = transcript.language.split("-")[0].upper() or None

        result = await asyncio.to_thread(
            translator.translate_text,
            text,
            target_lang=target,
            source_lang=source,
        )
        return TranslatedScript(
            text=result.text.strip(),
            source_language=transcript.language,
            target_language=target_language,
        )
