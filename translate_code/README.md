# ad-localizer

Localize an English ad video into another language (Spanish first). Input: an
English video. Output: a localized version where the same speaker (voice-cloned)
speaks the translated script, lips re-synced, with optional on-screen text
translation.

## Pipeline

One provider per stage, run strictly in order:

| Stage | Provider | What it does |
|---|---|---|
| 1. Transcribe | Whisper (local faster-whisper) | English transcript + word timings |
| 2. Translate | Claude LLM (DeepL fallback) | Ad-aware, duration-conscious translation |
| 3. Voice | ElevenLabs | Clone the original speaker, TTS the translated script in that voice |
| 4. Lip-sync | sync.so | Original video + new audio → lip-synced video (lipsync only, no dubbing mode) |
| 5. On-screen text | Vozo (optional) | Detect/translate burned-in text; auto-skipped when OCR finds none |
| 6. Mux & QA | ffmpeg | Assemble final video, sanity checks |

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and ffmpeg on PATH.

```bash
uv sync --extra dev
cp .env.example .env   # fill in the keys you need
```

Keys (in `.env`, never hardcoded):

- `ANTHROPIC_API_KEY` — translation (or `DEEPL_API_KEY` for the literal fallback)
- `ELEVENLABS_API_KEY` — voice clone + TTS (you must have rights to clone the speaker's voice)
- `SYNC_API_KEY` — sync.so lip-sync
- `VOZO_API_KEY` — on-screen text (optional stage)
- `OPENAI_API_KEY` — only if switching to hosted Whisper

Non-secret defaults (model names, default target language) live in `config.yaml`.

## Usage

```bash
# one ad, English -> Spanish
uv run ad-localizer run input.mp4 --to es

# walk the whole pipeline with fake providers — no keys, no spend
uv run ad-localizer run input.mp4 --to es --dry-run

# skip or force the on-screen-text stage
uv run ad-localizer run input.mp4 --to es --no-onscreen-text
uv run ad-localizer run input.mp4 --to es --force-onscreen-text

# estimate cost only
uv run ad-localizer estimate input.mp4 --to es

# batch a folder into several languages
uv run ad-localizer batch ./ads --to es,fr,de --out ./localized
```

Each job writes intermediate artifacts to `<out>/<video>_<lang>/`. Reruns
resume from cached artifacts — a retry never re-clones the voice or re-runs
lip-sync if the artifact already exists. Warnings (over-length dub, skipped
stages, vendor constraints) print at the end; stage failures name the stage
and leave prior artifacts intact.

## Demo (no keys needed)

```bash
make demo   # generates a tiny sample clip and runs the fake-provider pipeline
make test   # unit tests; all external APIs mocked
```

## Swap a provider

Each stage is an abstract base class in `src/ad_localizer/providers/base.py`
with exactly one implementation. To swap a vendor:

1. Implement the stage's ABC in a new module under `src/ad_localizer/providers/<stage>/`
   (return the dataclasses from `models.py` — those are the contracts).
2. Construct your class instead of the old one in `cli.py::_build_pipeline`.
3. Run `tests/test_contracts.py` to verify the contract, and `--dry-run` end to end.

Nothing else changes: `pipeline.py` only knows the interfaces.
