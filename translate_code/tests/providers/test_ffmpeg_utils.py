"""Tests for ffmpeg_utils error paths. No real ffmpeg/ffprobe invocations."""

import json
import subprocess
from pathlib import Path

import pytest

from ad_localizer import ffmpeg_utils
from ad_localizer.ffmpeg_utils import (
    FFmpegError,
    _run,
    ensure_ffmpeg,
    has_audio_stream,
    probe_duration,
)


def make_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestEnsureFfmpeg:
    def test_raises_when_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda tool: None)
        with pytest.raises(FFmpegError, match="ffmpeg not found on PATH"):
            ensure_ffmpeg()

    def test_raises_when_only_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_utils.shutil,
            "which",
            lambda tool: "/usr/local/bin/ffmpeg" if tool == "ffmpeg" else None,
        )
        with pytest.raises(FFmpegError, match="ffprobe not found on PATH"):
            ensure_ffmpeg()

    def test_passes_when_both_present(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_utils.shutil, "which", lambda tool: f"/usr/local/bin/{tool}"
        )
        ensure_ffmpeg()  # must not raise


class TestRun:
    def test_raises_ffmpeg_error_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_utils.subprocess,
            "run",
            lambda cmd, **kw: make_completed(returncode=1, stderr="boom: bad input"),
        )
        with pytest.raises(FFmpegError) as excinfo:
            _run(["ffprobe", "-v", "error", "nonexistent.mp4"])
        msg = str(excinfo.value)
        assert "Command failed (1)" in msg
        assert "boom: bad input" in msg
        assert "ffprobe" in msg

    def test_returns_process_on_success(self, monkeypatch):
        proc = make_completed(returncode=0, stdout="ok")
        monkeypatch.setattr(ffmpeg_utils.subprocess, "run", lambda cmd, **kw: proc)
        assert _run(["ffmpeg", "-version"]) is proc

    def test_stderr_truncated_to_last_2000_chars(self, monkeypatch):
        long_stderr = "x" * 5000 + "TAIL"
        monkeypatch.setattr(
            ffmpeg_utils.subprocess,
            "run",
            lambda cmd, **kw: make_completed(returncode=1, stderr=long_stderr),
        )
        with pytest.raises(FFmpegError) as excinfo:
            _run(["ffmpeg"])
        msg = str(excinfo.value)
        assert "TAIL" in msg
        assert "x" * 2001 not in msg


class TestProbeDuration:
    def test_parses_duration_from_json(self, monkeypatch):
        payload = json.dumps({"format": {"duration": "12.34"}})
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return make_completed(returncode=0, stdout=payload)

        monkeypatch.setattr(ffmpeg_utils.subprocess, "run", fake_run)
        assert probe_duration(Path("ad.mp4")) == pytest.approx(12.34)
        assert captured["cmd"][0] == "ffprobe"
        assert "ad.mp4" in captured["cmd"]

    def test_raises_when_ffprobe_fails(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_utils.subprocess,
            "run",
            lambda cmd, **kw: make_completed(returncode=1, stderr="No such file"),
        )
        with pytest.raises(FFmpegError, match="No such file"):
            probe_duration(Path("missing.mp4"))


class TestHasAudioStream:
    def test_true_when_streams_present(self, monkeypatch):
        payload = json.dumps({"streams": [{"index": 1}]})
        monkeypatch.setattr(
            ffmpeg_utils.subprocess,
            "run",
            lambda cmd, **kw: make_completed(returncode=0, stdout=payload),
        )
        assert has_audio_stream(Path("ad.mp4")) is True

    def test_false_when_no_streams(self, monkeypatch):
        payload = json.dumps({"streams": []})
        monkeypatch.setattr(
            ffmpeg_utils.subprocess,
            "run",
            lambda cmd, **kw: make_completed(returncode=0, stdout=payload),
        )
        assert has_audio_stream(Path("ad.mp4")) is False
