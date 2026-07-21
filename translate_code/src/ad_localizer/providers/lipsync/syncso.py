"""sync.so lip-sync provider.

Submits the original video plus OUR dubbed audio to sync.so's Generate API
(lipsync only — never their built-in TTS/dubbing modes), polls until the
generation reaches a terminal state, and downloads the result.

Input handling: sync.so's generation API takes URLs, but the Assets API
provides a presigned-upload flow (POST /v2/assets/upload → PUT bytes →
POST /v2/assets → reference by assetId). Local paths are uploaded through
that flow automatically; http(s) inputs are passed through as URLs.

Known model constraints (surfaced as warnings at submit time):
- long videos are auto-chunked into ~30-40s segments by sync.so;
- rapid scene changes or faceless scenes can cause failures/rejections.
"""

import asyncio
import logging
import mimetypes
import re
from pathlib import Path

import httpx

from ...models import AudioTrack
from ..base import LipsyncProvider
from ..polling import poll_until_complete

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "REJECTED"}
_URL_RE = re.compile(r"^https?:/{1,2}", re.IGNORECASE)
# Above ~this many seconds sync.so splits the job into segments internally.
_AUTO_CHUNK_THRESHOLD_S = 40.0
# Generous timeouts: uploads and downloads are large media files.
_HTTP_TIMEOUT = httpx.Timeout(30.0, read=600.0, write=600.0)


def _as_url(path: Path) -> str | None:
    """Return a normalized http(s) URL if ``path`` is one, else None.

    Path() collapses ``https://`` to ``https:/``, so restore the scheme.
    """
    raw = str(path)
    if not _URL_RE.match(raw):
        return None
    scheme, rest = raw.split(":", 1)
    return f"{scheme}://{rest.lstrip('/')}"


class SyncSoLipsyncProvider(LipsyncProvider):
    """Lip-sync via sync.so (https://sync.so), using the ``syncsdk`` package."""

    def __init__(self, api_key: str, model: str = "lipsync-2"):
        # "lipsync-2" is the cheap default; "sync-3" is higher quality.
        self._api_key = api_key
        self._model = model
        self._client = None  # lazily constructed AsyncSync
        # estimated USD cost of the most recent submission (None if the
        # estimate endpoint was unavailable); the pipeline accumulates it.
        self.last_cost_estimate: float | None = None

    # -- SDK plumbing ------------------------------------------------------

    def _sdk(self):
        """Lazily import the syncsdk modules (import name: ``sync``)."""
        try:
            import sync  # noqa: F401
            from sync import AsyncSync
            from sync.common import Audio, Video
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "The 'syncsdk' package is required for SyncSoLipsyncProvider "
                "(pip install syncsdk)"
            ) from exc
        return AsyncSync, Video, Audio

    def _get_client(self):
        if self._client is None:
            AsyncSync, _, _ = self._sdk()
            self._client = AsyncSync(api_key=self._api_key)
        return self._client

    @staticmethod
    async def _call(func, /, *args, **kwargs):
        """Await the SDK call, or run it in a thread if it is blocking."""
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        result = await asyncio.to_thread(func, *args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    # -- input resolution --------------------------------------------------

    # syncsdk 0.3.0 has no asset-upload API (assets are read-only: get/list),
    # so local files go through generations.create_with_files - a direct
    # multipart submit capped at ~20 MB per request by the API.
    _MULTIPART_LIMIT_BYTES = 20 * 1024 * 1024

    def _file_part(self, path: Path, kind: str):
        if not path.exists():
            raise FileNotFoundError(
                f"sync.so {kind} input not found: {path}. Provide a local "
                "file or an http(s) URL."
            )
        size = path.stat().st_size
        if size > self._MULTIPART_LIMIT_BYTES:
            raise ValueError(
                f"sync.so direct upload caps at 20 MB and {path.name} is "
                f"{size / 1e6:.0f} MB - host the file at an http(s) URL instead"
            )
        content_type = mimetypes.guess_type(path.name)[0] or (
            "video/mp4" if kind == "video" else "audio/wav"
        )
        return (path.name, path.read_bytes(), content_type)

    async def _submit_with_files(self, video: Path, audio: Path):
        """Direct multipart submit for local inputs (no pre-estimate: the
        estimate endpoint needs URL/asset inputs, which we don't have)."""
        client = self._get_client()
        self.last_cost_estimate = None
        logger.info(
            "sync.so: uploading %s + %s via create_with_files (cost estimate "
            "unavailable for direct file upload)",
            video.name,
            audio.name,
        )
        return await self._call(
            client.generations.create_with_files,
            model=self._model,
            video=self._file_part(video, "video"),
            audio=self._file_part(audio, "audio"),
            # multi-MB multipart bodies exceed the SDK's default timeout
            request_options={"timeout_in_seconds": 600},
        )

    # -- cost estimation ---------------------------------------------------

    async def _estimate_cost(self, inputs: list) -> None:
        """POST /v2/analyze/cost; best-effort — never blocks submission."""
        client = self._get_client()
        self.last_cost_estimate = None
        try:
            estimates = await self._call(
                client.generations.estimate_cost,
                input=inputs,
                model=self._model,
            )
        except Exception as exc:
            logger.warning("sync.so cost estimation unavailable: %s", exc)
            return
        if not isinstance(estimates, list):
            estimates = [estimates]
        total = 0.0
        for est in estimates:
            cost = getattr(est, "estimated_generation_cost", None)
            if cost is None:
                cost = getattr(est, "estimatedGenerationCost", None)
            if cost is not None:
                total += float(cost)
        self.last_cost_estimate = total
        logger.info(
            "sync.so estimated cost for model %s: $%.4f", self._model, total
        )

    # -- main entry point ----------------------------------------------------

    async def lipsync(self, video: Path, audio: AudioTrack, work_dir: Path) -> Path:
        client = self._get_client()

        logger.warning(
            "sync.so constraints: rapid scene changes or faceless scenes can "
            "cause the generation to fail or be rejected."
        )
        if audio.duration_s > _AUTO_CHUNK_THRESHOLD_S:
            logger.warning(
                "Audio is %.0fs long; sync.so auto-chunks long videos into "
                "~30-40s segments, which may show seams at segment boundaries.",
                audio.duration_s,
            )

        # Lipsync only: our own audio track, no TTS/dubbing parameters.
        video_url, audio_url = _as_url(video), _as_url(audio.path)
        if video_url and audio_url:
            _, Video, Audio = self._sdk()
            inputs = [Video(url=video_url), Audio(url=audio_url)]
            await self._estimate_cost(inputs)
            job = await self._call(
                client.generations.create,
                input=inputs,
                model=self._model,
            )
        else:
            job = await self._submit_with_files(video, audio.path)
        job_id = job.id
        logger.info("sync.so generation %s submitted (model=%s)", job_id, self._model)

        async def check():
            return await self._call(client.generations.get, id=job_id)

        final = await poll_until_complete(
            check,
            lambda g: str(g.status).upper() in _TERMINAL_STATUSES,
            timeout_s=1800.0,
            describe=f"sync.so lipsync generation {job_id}",
        )

        status = str(final.status).upper()
        if status != "COMPLETED":
            error = getattr(final, "error", None) or "no error detail provided"
            error_code = getattr(final, "error_code", None) or getattr(
                final, "errorCode", None
            )
            detail = f"{error} (code: {error_code})" if error_code else str(error)
            raise RuntimeError(
                f"sync.so lipsync generation {job_id} ended {status}: {detail}"
            )

        output_url = getattr(final, "output_url", None) or getattr(
            final, "outputUrl", None
        )
        if not output_url:
            raise RuntimeError(
                f"sync.so generation {job_id} COMPLETED but returned no output URL"
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        out_path = work_dir / f"lipsynced_{self._model}_{job_id}.mp4"
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, follow_redirects=True
        ) as http:
            async with http.stream("GET", output_url) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        logger.info("sync.so output downloaded to %s", out_path)
        return out_path
