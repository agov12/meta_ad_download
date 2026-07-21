"""Contract tests: every provider (real or fake) subclasses its ABC.

Real providers are being implemented concurrently; each one (and its vendor
SDK) is imported with pytest.importorskip so a missing module skips instead
of erroring.
"""

import inspect
import wave
from pathlib import Path

import pytest

from ad_localizer.models import AudioTrack, Transcript, TranslatedScript, WordTiming
from ad_localizer.providers.base import (
    LipsyncProvider,
    OnScreenTextProvider,
    TranscriptionProvider,
    TranslationProvider,
    VoiceProvider,
)
from ad_localizer.providers.fakes import (
    FakeLipsyncProvider,
    FakeOnScreenTextProvider,
    FakeTranscriptionProvider,
    FakeTranslationProvider,
    FakeVoiceProvider,
)

# (vendor SDK modules required at *import time*, provider module, class name, ABC)
#
# The real providers lazily import their heavy vendor SDKs (faster_whisper,
# anthropic, deepl, elevenlabs, sync) inside methods, so those SDKs are not
# needed to import the module or to verify the class contract. Only
# import-time dependencies are listed; pytest.importorskip on the provider
# module itself also skips (not errors) if the implementation has not landed
# yet or fails to import for a missing dep.
REAL_PROVIDERS = [
    (
        [],
        "ad_localizer.providers.transcription.whisper",
        "WhisperTranscriptionProvider",
        TranscriptionProvider,
    ),
    (
        [],
        "ad_localizer.providers.translation.translator",
        "LLMTranslationProvider",
        TranslationProvider,
    ),
    (
        [],
        "ad_localizer.providers.translation.translator",
        "DeepLTranslationProvider",
        TranslationProvider,
    ),
    (
        [],
        "ad_localizer.providers.voice.elevenlabs",
        "ElevenLabsVoiceProvider",
        VoiceProvider,
    ),
    (
        ["httpx"],
        "ad_localizer.providers.lipsync.syncso",
        "SyncSoLipsyncProvider",
        LipsyncProvider,
    ),
    (
        ["httpx"],
        "ad_localizer.providers.onscreen_text.vozo",
        "VozoOnScreenTextProvider",
        OnScreenTextProvider,
    ),
]


@pytest.mark.parametrize(
    "sdk_modules,provider_module,class_name,abc",
    REAL_PROVIDERS,
    ids=[p[2] for p in REAL_PROVIDERS],
)
def test_real_provider_implements_contract(sdk_modules, provider_module, class_name, abc):
    for sdk in sdk_modules:
        pytest.importorskip(sdk)
    module = pytest.importorskip(provider_module)
    cls = getattr(module, class_name, None)
    assert cls is not None, f"{provider_module} does not define {class_name}"
    assert issubclass(cls, abc)
    assert not inspect.isabstract(cls), f"{class_name} still has abstract methods"


FAKE_PROVIDERS = [
    (FakeTranscriptionProvider, TranscriptionProvider),
    (FakeTranslationProvider, TranslationProvider),
    (FakeVoiceProvider, VoiceProvider),
    (FakeLipsyncProvider, LipsyncProvider),
    (FakeOnScreenTextProvider, OnScreenTextProvider),
]


@pytest.mark.parametrize(
    "fake_cls,abc", FAKE_PROVIDERS, ids=[f[0].__name__ for f in FAKE_PROVIDERS]
)
def test_fake_provider_implements_contract(fake_cls, abc):
    assert issubclass(fake_cls, abc)
    assert fake_cls is not abc
    assert not inspect.isabstract(fake_cls)
    fake_cls()  # instantiable without arguments


async def test_fake_transcription_returns_transcript(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"\x00fake video bytes")
    result = await FakeTranscriptionProvider().transcribe(video)
    assert isinstance(result, Transcript)
    assert result.language == "en"
    assert result.text
    assert len(result.words) >= 3
    assert all(isinstance(w, WordTiming) for w in result.words)
    assert all(w.end >= w.start for w in result.words)


async def test_fake_translation_returns_translated_script():
    transcript = Transcript(text="Hello world", language="en")
    result = await FakeTranslationProvider().translate(transcript, "es")
    assert isinstance(result, TranslatedScript)
    assert result.text == "[es] Hello world"
    assert result.source_language == "en"
    assert result.target_language == "es"


async def test_fake_voice_writes_valid_wav(tmp_path):
    script = TranslatedScript(text="[es] Hola", source_language="en", target_language="es")
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"\x00fake audio")
    work_dir = tmp_path / "work"

    result = await FakeVoiceProvider().clone_and_synthesize(script, reference, work_dir)

    assert isinstance(result, AudioTrack)
    assert result.voice_id == "fake-voice"
    assert result.duration_s > 0
    assert result.path.exists()
    assert result.path.parent == work_dir
    # tiny but genuinely valid WAV, no ffmpeg involved
    with wave.open(str(result.path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() > 0


async def test_fake_lipsync_copies_video(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"\x00fake video bytes")
    audio = AudioTrack(path=tmp_path / "dub.wav", duration_s=1.0, voice_id="fake-voice")
    audio.path.write_bytes(b"RIFFfake")
    work_dir = tmp_path / "work"

    result = await FakeLipsyncProvider().lipsync(video, audio, work_dir)

    assert isinstance(result, Path)
    assert result.exists()
    assert result != video
    assert result.parent == work_dir
    assert result.read_bytes() == video.read_bytes()


async def test_fake_onscreen_text_copies_video(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"\x00fake video bytes")
    work_dir = tmp_path / "work"

    result = await FakeOnScreenTextProvider().localize_text(video, "es", work_dir)

    assert isinstance(result, Path)
    assert result.exists()
    assert result != video
    assert result.parent == work_dir
    assert result.read_bytes() == video.read_bytes()
