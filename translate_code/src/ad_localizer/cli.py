"""Typer CLI: run / estimate / batch, with --dry-run for the fake-provider walk."""

import asyncio
import logging
from pathlib import Path

import typer

from . import ffmpeg_utils
from .config import AppConfig, Settings, load_config, load_settings
from .models import LocalizationJob
from .pipeline import Pipeline, StageError

app = typer.Typer(help="Localize English ad videos into a target language.")

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _build_pipeline(
    settings: Settings,
    config: AppConfig,
    *,
    dry_run: bool,
    skip_onscreen_text: bool,
    force_onscreen_text: bool,
) -> Pipeline:
    if dry_run:
        from .providers.fakes import (
            FakeLipsyncProvider,
            FakeOnScreenTextProvider,
            FakeTranscriptionProvider,
            FakeTranslationProvider,
            FakeVoiceProvider,
        )

        return Pipeline(
            FakeTranscriptionProvider(),
            FakeTranslationProvider(),
            FakeVoiceProvider(),
            FakeLipsyncProvider(),
            FakeOnScreenTextProvider(),
            skip_onscreen_text=skip_onscreen_text,
            force_onscreen_text=force_onscreen_text,
        )

    from .providers.lipsync.syncso import SyncSoLipsyncProvider
    from .providers.transcription.whisper import WhisperTranscriptionProvider
    from .providers.translation.translator import (
        DeepLTranslationProvider,
        LLMTranslationProvider,
    )
    from .providers.voice.elevenlabs import ElevenLabsVoiceProvider

    missing = [
        name
        for name, key in [
            ("ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
            ("SYNC_API_KEY", settings.sync_api_key),
        ]
        if not key
    ]
    if not settings.anthropic_api_key and not settings.deepl_api_key:
        missing.append("ANTHROPIC_API_KEY (or DEEPL_API_KEY)")
    if missing:
        raise typer.BadParameter(
            f"Missing API keys in environment/.env: {', '.join(missing)}. "
            "See .env.example. (Use --dry-run to test without keys.)"
        )

    if settings.anthropic_api_key:
        translation = LLMTranslationProvider(
            api_key=settings.anthropic_api_key, model=config.translation_model
        )
    else:
        translation = DeepLTranslationProvider(api_key=settings.deepl_api_key)

    onscreen = None
    if settings.vozo_api_key and not skip_onscreen_text:
        from .providers.onscreen_text.vozo import VozoOnScreenTextProvider

        onscreen = VozoOnScreenTextProvider(api_key=settings.vozo_api_key)
    elif force_onscreen_text and not settings.vozo_api_key:
        raise typer.BadParameter("--force-onscreen-text requires VOZO_API_KEY")

    return Pipeline(
        WhisperTranscriptionProvider(model_size=config.whisper_model),
        translation,
        ElevenLabsVoiceProvider(
            api_key=settings.elevenlabs_api_key, tts_model=config.elevenlabs_tts_model
        ),
        SyncSoLipsyncProvider(api_key=settings.sync_api_key, model=config.lipsync_model),
        onscreen,
        skip_onscreen_text=skip_onscreen_text,
        force_onscreen_text=force_onscreen_text,
    )


def _run_one(
    video: Path,
    target: str,
    out_dir: Path,
    *,
    dry_run: bool,
    skip_onscreen_text: bool,
    force_onscreen_text: bool,
) -> LocalizationJob:
    ffmpeg_utils.ensure_ffmpeg()
    settings = load_settings()
    config = load_config()
    work_dir = out_dir / f"{video.stem}_{target}"
    job = LocalizationJob(source_video=video, target_language=target, work_dir=work_dir)
    pipeline = _build_pipeline(
        settings,
        config,
        dry_run=dry_run,
        skip_onscreen_text=skip_onscreen_text,
        force_onscreen_text=force_onscreen_text,
    )
    try:
        job = asyncio.run(pipeline.run(job))
    except StageError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        typer.secho(
            f"Artifacts up to the failed stage are intact in {work_dir}; rerun to resume.",
            err=True,
        )
        raise typer.Exit(code=1)
    for w in job.warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW, err=True)
    if job.estimated_cost_usd:
        typer.echo(f"Estimated API spend: ${job.estimated_cost_usd:.2f}")
    typer.secho(f"Done: {job.final_video}", fg=typer.colors.GREEN)
    return job


@app.command()
def run(
    video: Path = typer.Argument(..., exists=True, readable=True),
    to: str = typer.Option(None, "--to", help="Target language code, e.g. es"),
    out: Path = typer.Option(Path("./work"), "--out", help="Output/work directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fake providers, no API spend"),
    no_onscreen_text: bool = typer.Option(False, "--no-onscreen-text"),
    force_onscreen_text: bool = typer.Option(False, "--force-onscreen-text"),
):
    """Localize one ad video."""
    target = to or load_config().default_target_language
    if no_onscreen_text and force_onscreen_text:
        raise typer.BadParameter("--no-onscreen-text and --force-onscreen-text conflict")
    _run_one(
        video,
        target,
        out,
        dry_run=dry_run,
        skip_onscreen_text=no_onscreen_text,
        force_onscreen_text=force_onscreen_text,
    )


@app.command()
def estimate(
    video: Path = typer.Argument(..., exists=True, readable=True),
    to: str = typer.Option(None, "--to"),
):
    """Estimate cost for one job without spending on APIs."""
    ffmpeg_utils.ensure_ffmpeg()
    target = to or load_config().default_target_language
    duration = ffmpeg_utils.probe_duration(video)
    minutes = duration / 60
    # rough public-pricing heuristics; refined per-vendor at submit time
    elevenlabs = minutes * 0.30
    lipsync = minutes * 1.00
    typer.echo(f"{video.name} → {target}: {duration:.0f}s of video")
    typer.echo(f"  ElevenLabs TTS (approx): ${elevenlabs:.2f}")
    typer.echo(f"  sync.so lipsync (approx): ${lipsync:.2f}")
    typer.echo(f"  LLM translation: < $0.05")
    typer.echo(f"  Total (approx): ${elevenlabs + lipsync + 0.05:.2f}")
    typer.echo("Note: sync.so's own estimate endpoint is called before any real submit.")


@app.command()
def batch(
    folder: Path = typer.Argument(..., exists=True, file_okay=False),
    to: str = typer.Option(..., "--to", help="Comma-separated language codes: es,fr,de"),
    out: Path = typer.Option(Path("./localized"), "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Localize every video in a folder into one or more languages."""
    videos = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
    )
    if not videos:
        raise typer.BadParameter(f"No videos found in {folder}")
    targets = [t.strip() for t in to.split(",") if t.strip()]
    failures = 0
    for video in videos:
        for target in targets:
            typer.echo(f"--- {video.name} → {target} ---")
            try:
                _run_one(
                    video, target, out,
                    dry_run=dry_run, skip_onscreen_text=False, force_onscreen_text=False,
                )
            except typer.Exit:
                failures += 1
    if failures:
        typer.secho(f"{failures} job(s) failed", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
