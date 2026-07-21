"""ffmpeg helpers: probe duration, extract audio, extract reference clip, mux.

ffmpeg is assumed installed; call ensure_ffmpeg() at startup to fail clearly
if it is missing. All functions shell out via subprocess (run in a thread
from async code with asyncio.to_thread if needed).
"""

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def ensure_ffmpeg() -> None:
    """Raise with a clear message if ffmpeg/ffprobe are not on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(
                f"{tool} not found on PATH. Install it (e.g. `brew install ffmpeg`) and retry."
            )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}"
        )
    return proc


def probe_duration(media: Path) -> float:
    """Duration of a media file in seconds."""
    proc = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(media),
        ]
    )
    data = json.loads(proc.stdout)
    return float(data["format"]["duration"])


def has_audio_stream(media: Path) -> bool:
    proc = _run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json", str(media),
        ]
    )
    return bool(json.loads(proc.stdout).get("streams"))


def extract_audio(video: Path, out_wav: Path, *, sample_rate: int = 16000) -> Path:
    """Extract the full audio track as mono WAV (16 kHz default, Whisper-friendly)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-c:a", "pcm_s16le", str(out_wav),
        ]
    )
    return out_wav


def extract_reference_clip(
    video: Path, out_path: Path, *, start_s: float = 0.0, duration_s: float = 60.0
) -> Path:
    """Extract a clean speech clip (mp3) to use as ElevenLabs cloning reference."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-ss", str(start_s), "-i", str(video),
            "-t", str(duration_s), "-vn", "-ac", "1",
            "-c:a", "libmp3lame", "-q:a", "2", str(out_path),
        ]
    )
    return out_path


def mux_replace_audio(video: Path, audio: Path, out_video: Path) -> Path:
    """Replace the video's audio track with `audio` (video stream copied, no re-encode)."""
    out_video.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_video),
        ]
    )
    return out_video


def extract_frames(video: Path, out_dir: Path, *, fps: float = 0.5) -> list[Path]:
    """Sample frames (default 1 every 2s) as PNGs — used for the OCR pre-check."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "frame_%04d.png"
    _run(["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={fps}", str(pattern)])
    return sorted(out_dir.glob("frame_*.png"))
