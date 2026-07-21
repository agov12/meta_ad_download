# Ad Localizer — Build Plan for Claude Code


## 1. What we're building

A command-line tool (with a clean internal API so it can later become a web service) that takes an English ad video and produces a localized version in a target language. Localization means three layers, done in this exact order and with these exact tools:

1. **Transcribe** the English speech (with word-level timings).
2. **Translate** the English transcript into the target language (Spanish first) using an AI/MT tool.
3. **Voice:** on ElevenLabs, create a voice from the original speaker (clone it from the English audio), then use *that same voice* to speak the translated (Spanish) script aloud.
4. **Lip-sync:** overlay the new audio onto the original video via sync.so, which does the lip-sync.
5. **On-screen text (only if present):** use Vozo to detect on-screen text and translate/rebuild it in the target language.
6. **Mux & deliver:** assemble the final video, run QA checks, output the file.

**Target first milestone:** English → Spanish, single-speaker talking-head ad, with optional on-screen text. Everything else (more languages, multi-speaker) is a later iteration.

## 2. Architecture: one pipeline, one provider per stage

Each stage is defined by a small interface (abstract base class) with exactly one concrete implementation for now. The interface exists only so a vendor can be replaced later without rewriting the pipeline — not to support parallel vendor choices. There is no all-in-one path and no vendor-selection config.

```
input video (English)
   │
   ▼
[Transcription]  Whisper           → English transcript + word timings
   │
   ▼
[Translation]    LLM / DeepL       → translated script (target lang)
   │
   ▼
[Voice]          ElevenLabs        → clone speaker's voice from English audio,
   │                                  then speak the translated script in that voice
   ▼
[Lipsync]        sync.so           → original video + new audio → lip-synced video
   │
   ▼
[On-screen text] Vozo (optional)   → detect + translate + rebuild burned-in text
   │
   ▼
[Muxer + QA]                       → final localized ad
```

The pipeline code calls each stage in sequence and does not branch between vendors.

## 3. Tech stack

- **Language:** Python 3.11+ (async where the APIs are async/polling).
- **Package manager:** `uv` (`uv init`, `uv add`, inline script metadata where useful).
- **Video/audio muxing:** `ffmpeg` via `ffmpeg-python` or subprocess. Assume ffmpeg is installed; check for it at startup and error clearly if missing.
- **HTTP:** `httpx` (async-capable).
- **Config:** `pydantic-settings` reading from `.env` for keys; a minimal `config.yaml` only for things like default model names and target language.
- **CLI:** `typer`.
- **Testing:** `pytest`, with all external API calls mocked in unit tests.
- **Secrets:** all API keys from environment variables, never hardcoded. Ship a `.env.example`.

## 4. Repository layout (create this exact structure)

```
ad-localizer/
├── pyproject.toml
├── .env.example
├── config.yaml
├── README.md
├── src/ad_localizer/
│   ├── __init__.py
│   ├── cli.py                    # typer entrypoint
│   ├── pipeline.py               # orchestrates the 6 stages in order
│   ├── models.py                 # shared dataclasses (THE CONTRACTS)
│   ├── config.py                 # settings + key loading
│   ├── ffmpeg_utils.py           # extract audio, mux, probe duration, etc.
│   └── providers/
│       ├── __init__.py
│       ├── base.py               # abstract base classes (THE CONTRACTS)
│       ├── transcription/
│       │   └── whisper.py        # local faster-whisper (or hosted Whisper)
│       ├── translation/
│       │   └── translator.py     # LLM-based, context-aware (DeepL as fallback)
│       ├── voice/
│       │   └── elevenlabs.py     # clone from reference audio, then TTS the target script
│       ├── lipsync/
│       │   └── syncso.py         # lipsync ONLY (not sync.so's dubbing mode)
│       └── onscreen_text/
│           └── vozo.py           # Visual Translation for burned-in text
└── tests/
    ├── test_contracts.py
    ├── test_pipeline.py
    └── providers/...
```

## 5. The contracts (build these FIRST, before any subagents fan out)

These shared data models and interfaces are what every stage depends on. **Nothing else can be built correctly until these are frozen.** Claude Code should write `models.py` and `providers/base.py` completely first, get them reviewed, and only then dispatch stage subagents.

### `models.py` — data passed between stages

```python
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
    voice_id: str | None = None   # the ElevenLabs cloned-voice id used

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
```

### `providers/base.py` — the interfaces (one implementation each)

```python
from abc import ABC, abstractmethod
from pathlib import Path
from .models import Transcript, TranslatedScript, AudioTrack

class TranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(self, video_or_audio: Path) -> Transcript: ...

class TranslationProvider(ABC):
    @abstractmethod
    async def translate(self, transcript: Transcript, target_language: str,
                        context: str | None = None) -> TranslatedScript: ...

class VoiceProvider(ABC):
    # Must (1) clone/obtain a voice from reference_audio, then
    # (2) synthesize the target-language script in THAT voice.
    @abstractmethod
    async def clone_and_synthesize(self, script: TranslatedScript,
                                   reference_audio: Path,
                                   work_dir: Path) -> AudioTrack: ...

class LipsyncProvider(ABC):
    @abstractmethod
    async def lipsync(self, video: Path, audio: AudioTrack, work_dir: Path) -> Path: ...

class OnScreenTextProvider(ABC):
    @abstractmethod
    async def localize_text(self, video: Path, target_language: str, work_dir: Path) -> Path: ...
```

Every long-running API (sync.so, Vozo) is **async with polling**. Implement a shared `poll_until_complete()` helper with sensible backoff and a timeout.

## 6. Vendor integration notes (what the subagents need to know)

> Verify every endpoint against current official docs before coding — these services iterate. Start from each vendor's own documentation and their `/llms.txt` index where available.

### Transcription — Whisper
- Local `faster-whisper` for cost, or OpenAI's hosted Whisper for convenience. Need **word-level timings** so the dub can be timing-aware and so we can detect over-length translations.
- Output an English `Transcript` with `words` populated.

### Translation — LLM (context-aware), DeepL as fallback
- Primary: an LLM-based translator (GPT/Claude) that takes **context** ("this is an ad, keep it punchy, match idiom, preserve brand/product names, keep it close to the original spoken duration"). Ad copy localization is cultural adaptation, not literal translation, so the LLM path is the default.
- Fallback: DeepL for a fast literal baseline.
- The user's current manual step uses Google Translate or an AI tool — this stage is the automated equivalent, defaulting to the higher-quality AI path.

### Voice — ElevenLabs (clone, then speak target script)
- This mirrors the manual step precisely: create a voice from the **original speaker** (instant voice cloning from a reference audio clip extracted from the source video's English audio via ffmpeg), then run **text-to-speech of the translated script using that cloned voice**, so the Spanish audio sounds like the same person.
- Auth via `ELEVENLABS_API_KEY`. Respect ElevenLabs' consent/ToS rules for voice cloning.
- Return an `AudioTrack` (with the cloned `voice_id` recorded on the job for reuse across languages).
- Watch duration: if the synthesized target audio is materially longer than the original, add a warning (and optionally let ElevenLabs settings / a re-translation pass tighten it) so lip-sync stays clean.

### Lipsync — sync.so (LIPSYNC ONLY)
- Use sync.so purely for lip-sync: POST a generation with the **original video** + the **ElevenLabs audio** we generated, poll status until `COMPLETED` (terminal statuses: `COMPLETED`, `FAILED`, `REJECTED`), then download the output URL. Python SDK: `pip install syncsdk`; auth via `SYNC_API_KEY`.
- **Do NOT use sync.so's built-in ElevenLabs dubbing mode.** We supply our own cloned-voice audio and only want the lip-sync step, matching the manual workflow. Pass the audio as an input; do not enable dubbing parameters.
- Model name is a config field (e.g. lipsync-2 as the cheap default; sync-3 for highest quality / better handling of profiles and occlusion).
- Call the cost-estimation endpoint before submitting so the tool can print an estimated cost per job.
- Constraints to handle and surface as warnings: script/audio limits, long videos auto-chunked into ~30–40s segments, and rapid scene changes / faceless scenes can cause failures.
- Replicate (`sync/lipsync-2`) is an acceptable fallback integration path if needed.

### On-screen text — Vozo (only if burned-in text exists)
- Vozo's **Visual Translation** detects, erases, translates, and rebuilds on-screen text while preserving layout, style, and animations — used only for the burned-in-text layer, nothing else.
- This stage is **optional**: run a quick OCR check first; if there's no significant burned-in text, skip Vozo entirely (most talking-head ads won't need it).
- Confirm current Vozo API surface, auth, and points-based pricing in their docs (vozo.ai/api) before building; surface point cost per job if exposed.

## 7. Pipeline behavior

- `pipeline.py` runs the six stages strictly in order. No vendor branching.
- Each stage writes intermediate artifacts into a per-job `work_dir` so runs are resumable and debuggable — cache by input hash and don't re-call an expensive API if the artifact already exists (e.g. don't re-clone the voice or re-run lip-sync on a retry).
- Every stage that calls a paid API logs an estimated cost and accumulates a per-job total.
- The on-screen-text stage is skipped automatically when OCR finds no significant burned-in text.
- Robust error handling: any stage failure leaves prior artifacts intact and prints exactly which stage failed and why.

## 8. CLI surface (target)

```bash
# one ad, English -> Spanish
ad-localizer run input.mp4 --to es

# skip or force the on-screen-text stage
ad-localizer run input.mp4 --to es --no-onscreen-text
ad-localizer run input.mp4 --to es --force-onscreen-text

# estimate cost only, no API spend
ad-localizer estimate input.mp4 --to es

# batch a folder into several languages
ad-localizer batch ./ads --to es,fr,de --out ./localized
```

## 9. Testing & quality bar

- Unit tests mock ALL external APIs — no test should spend money or hit the network.
- A `--dry-run` mode walks the whole pipeline with fake providers end to end, to prove wiring.
- Contract tests assert every provider implements its base class and returns the right dataclass types.
- Provide a tiny sample clip + a `make demo` / `just demo` task that runs the fake-provider pipeline so anyone can see it work without keys.

## 10. Deliverables checklist

- [ ] `models.py` + `providers/base.py` frozen and reviewed (DO FIRST)
- [ ] ffmpeg utils (extract reference audio clip, probe duration, mux, etc.)
- [ ] config + settings + `.env.example`
- [ ] transcription provider (Whisper, word-level timings)
- [ ] translation provider (LLM primary, DeepL fallback)
- [ ] voice provider (ElevenLabs: clone from reference audio, then TTS target script)
- [ ] lipsync provider (sync.so, lipsync-only — no dubbing mode)
- [ ] on-screen-text provider (Vozo, optional/auto-skipped)
- [ ] pipeline orchestration + caching + cost tracking
- [ ] CLI (run/estimate/batch/dry-run)
- [ ] tests + fake providers + demo task
- [ ] README with setup, keys needed, and a "swap a provider" guide

---

## How to run this with subagents in Claude Code

Do it in this order — the ordering matters because subagents run with isolated context and can't coordinate mid-flight, so shared contracts must exist before parallel work starts.

**Phase 0 — Scaffold (single agent, no fan-out).**
Create the repo structure, `pyproject.toml`, config, ffmpeg utils, and — most importantly — write `models.py` and `providers/base.py` completely. These are the contracts every subagent codes against. Do not proceed until they're stable.

**Phase 1 — Fan out one subagent per stage (parallel).**
Each subagent owns one directory under `providers/` and implements against the frozen base classes. Give each the relevant vendor notes from section 6. They don't need to talk to each other because the interface is fixed. Assign:
- Subagent A: transcription (Whisper)
- Subagent B: translation (LLM + DeepL fallback)
- Subagent C: voice (ElevenLabs clone-then-speak)
- Subagent D: lipsync (sync.so, lipsync-only)
- Subagent E: on-screen text (Vozo, optional)
- Subagent F: tests + fake providers, coding to the same base classes

**Phase 2 — Integrate (single agent).**
Wire `pipeline.py` and `cli.py` to run the six stages in order, add caching + cost tracking, make `--dry-run` pass end to end with fake providers.

**Phase 3 — Real-run hardening (single agent + you).**
Run one real English→Spanish ad through the pipeline. Fix whatever the real APIs do differently from the docs. Tune: which Whisper model, LLM translation prompt for ad tone, ElevenLabs cloning settings, sync.so model choice.

**A note on scope discipline:** resist building the web UI, auth, billing, or a queue until one ad goes cleanly through the CLI. The CLI + this fixed pipeline IS the product core; everything else is packaging.
