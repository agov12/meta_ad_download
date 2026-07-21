"""Vocal / background separation via Demucs (htdemucs, two-stem mode).

Splits an audio file into a clean vocal stem and a background stem (music +
SFX). The vocal stem feeds VAD and transcription; the background stem is
kept and mixed back under the dubbed voice so the original music survives.

Demucs is run as a subprocess (``python -m demucs``) so its heavy torch
import never lands in our process. First run downloads model weights
(~80 MB) to ~/.cache.
"""

import subprocess
import sys
from pathlib import Path

_MODEL = "htdemucs"


class SeparationError(RuntimeError):
    pass


def separate_vocals(audio: Path, work_dir: Path) -> tuple[Path, Path]:
    """Split ``audio`` into (vocals, background) WAVs under ``work_dir``.

    Cached: if both stems already exist, Demucs is not re-run.
    """
    out_dir = work_dir / "stems"
    vocals = out_dir / _MODEL / audio.stem / "vocals.wav"
    background = out_dir / _MODEL / audio.stem / "no_vocals.wav"
    if vocals.exists() and background.exists():
        return vocals, background

    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable, "-m", "demucs",
            "-n", _MODEL,
            "--two-stems", "vocals",
            "-o", str(out_dir),
            str(audio),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SeparationError(
            f"demucs failed ({proc.returncode}):\n{proc.stderr[-2000:]}"
        )
    if not vocals.exists() or not background.exists():
        raise SeparationError(
            f"demucs completed but stems not found under {out_dir / _MODEL / audio.stem}"
        )
    return vocals, background
