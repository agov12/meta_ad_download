"""Assemble segment-aligned dub audio onto the original background stem.

Each synthesized clip is (a) time-fitted to its slot if it overruns —
pitch-preserving atempo, mild by construction because translations are
budgeted per segment — and (b) placed at its unit's original start time
over the Demucs background stem, so the music bed runs untouched and the
Spanish starts and stops with the English.
"""

import logging
from pathlib import Path

from .ffmpeg_utils import _run, probe_duration

logger = logging.getLogger("ad_localizer")

# every clip is sped up into the same narrow band so the fastest and slowest
# segments differ by at most 0.04x - a uniform brisk pace instead of some
# clips at 1.0x and others audibly faster
BASE_TEMPO = 1.04
MAX_TEMPO = 1.08


def fit_clip(
    clip: Path,
    max_final_s: float,
    out_path: Path,
    *,
    base_tempo: float = BASE_TEMPO,
    max_tempo: float = MAX_TEMPO,
) -> tuple[Path, float]:
    """Speed ``clip`` up into the [base_tempo, max_tempo] band.

    Everything gets at least base_tempo (uniform pace); a clip whose
    base-tempo duration would exceed ``max_final_s`` is compressed further,
    up to max_tempo. Returns (path, tempo). If even max_tempo can't bring it
    under max_final_s a warning is logged and max_tempo is used anyway - the
    caller's retry loop should have prevented this.
    """
    duration = probe_duration(clip)
    needed = duration / max_final_s
    tempo = min(max(base_tempo, needed), max_tempo)
    if needed > max_tempo:
        logger.warning(
            "segment %s needs %.2fx > %.2fx band max (speech %.1fs, cap %.1fs); "
            "clamping - translation should have been shortened",
            clip.name, needed, max_tempo, duration, max_final_s,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-i", str(clip),
            "-filter:a", f"atempo={tempo:.4f}",
            str(out_path),
        ]
    )
    return out_path, tempo


def assemble(
    placed_clips: list[tuple[Path, float]],
    background: Path,
    out_path: Path,
) -> Path:
    """Mix clips (each placed at its start time in seconds) over ``background``.

    Output length follows the background stem, which has the original
    video's full duration.
    """
    if not placed_clips:
        raise ValueError("no clips to assemble")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-i", str(background)]
    for clip, _start in placed_clips:
        cmd += ["-i", str(clip)]

    filters = []
    labels = []
    for i, (_clip, start) in enumerate(placed_clips):
        delay_ms = max(0, int(round(start * 1000)))
        filters.append(f"[{i + 1}:a]adelay={delay_ms}:all=1[v{i}]")
        labels.append(f"[v{i}]")
    n = len(placed_clips) + 1
    filters.append(
        f"[0:a]{''.join(labels)}amix=inputs={n}:duration=first:normalize=0[out]"
    )
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    _run(cmd)
    return out_path
