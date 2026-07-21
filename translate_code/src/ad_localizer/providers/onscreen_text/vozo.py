"""Vozo on-screen text stage: OCR pre-check + Visual-Translation provider.

Two pieces:

1. ``has_significant_onscreen_text`` — a cheap local OCR pre-check so the
   pipeline can auto-skip this (paid) stage for ordinary talking-head ads.
   Primary OCR backend is Apple's Vision framework driven through
   ``osascript`` (zero extra Python deps on macOS); if that is unavailable
   it falls back to ``pytesseract`` when installed, and otherwise returns
   True with a warning so the stage is never silently skipped.

2. ``VozoOnScreenTextProvider`` — submits the video to Vozo's public API
   (https://api.vozo.ai, bearer-auth) and downloads the localized result.

Vozo API reality check (researched 2026-07 from vozo.ai/docs/api_reference,
openapi.json and vozo.ai/docs/llms.txt):

- The public API exposes only Translate & Dub (``POST /v1/media/translate``,
  ``GET /v1/media/translate/{task_id}``) and LipSync endpoints. The Visual
  Translate product (detect/erase/translate/rebuild on-screen text preserving
  layout, style and animation) exists only in the web dashboard today — it has
  NO public endpoint. This provider therefore uses the closest documented
  mechanism: a Translate & Dub job with a full-frame ``ocr_text_box``, which
  is the only API knob that touches on-screen text. See
  ``VozoOnScreenTextProvider._build_payload`` for the flagged uncertainty.
- Jobs reference media by publicly accessible URL; there is NO upload
  endpoint ("Host your assets at a publicly accessible URL"). A local file
  must be published by the caller via ``media_url_resolver``.
- Pricing is points-based, deducted on successful completion, and NOT
  reported by the API: Translate & Dub is 3 points/min, Visual Translate
  (dashboard) is 10 points/min. ``last_cost_points`` is an estimate computed
  from the documented per-minute rate.
"""

import asyncio
import inspect
import json
import logging
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ...ffmpeg_utils import extract_frames, probe_duration
from ..base import OnScreenTextProvider
from ..polling import poll_until_complete

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OCR pre-check
# ---------------------------------------------------------------------------

_OCR_FPS = 0.5                     # 1 frame every 2 seconds
_MIN_FRAGMENT_CHARS = 3            # ignore tiny fragments like "OK", "|-"
_SIGNIFICANT_FRAME_FRACTION = 0.2  # text in >20% of sampled frames

# Apple Vision OCR via JXA. Reads image paths from argv, prints one JSON
# array of recognized strings per input image. Verified against macOS 15.
_VISION_OCR_JXA = """\
ObjC.import('Vision');
ObjC.import('Foundation');
function run(argv) {
  const lines = [];
  for (const path of argv) {
    let texts = [];
    try {
      const url = $.NSURL.fileURLWithPath(path);
      const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $.NSDictionary.dictionary);
      const request = $.VNRecognizeTextRequest.alloc.init;
      request.recognitionLevel = $.VNRequestTextRecognitionLevelAccurate;
      handler.performRequestsError($.NSArray.arrayWithObject(request), null);
      const results = request.results;
      if (results && !results.isNil()) {
        for (let i = 0; i < results.count; i++) {
          const cands = results.objectAtIndex(i).topCandidates(1);
          if (cands.count > 0) texts.push(ObjC.unwrap(cands.objectAtIndex(0).string));
        }
      }
    } catch (e) {
      texts = [];
    }
    lines.push(JSON.stringify(texts));
  }
  return lines.join('\\n');
}
"""


async def has_significant_onscreen_text(video: Path, work_dir: Path) -> bool:
    """Cheap local OCR pre-check: does `video` carry meaningful on-screen text?

    Samples ~1 frame per 2s via ffmpeg and OCRs the frames. "Significant"
    means readable text (fragments of >= 3 chars) appears in more than ~20%
    of sampled frames. If no OCR backend is available, returns True with a
    logged warning so the Vozo stage is never silently skipped.
    """
    frames_dir = work_dir / "ocr_precheck_frames"
    frames = await asyncio.to_thread(extract_frames, video, frames_dir, fps=_OCR_FPS)
    if not frames:
        logger.warning(
            "OCR pre-check: no frames extracted from %s; assuming on-screen text is present",
            video,
        )
        return True

    texts_per_frame = await asyncio.to_thread(_ocr_frames, frames)
    if texts_per_frame is None:
        logger.warning(
            "OCR pre-check: no OCR backend available (tried Apple Vision via osascript, "
            "then pytesseract); conservatively assuming on-screen text is present"
        )
        return True

    frames_with_text = sum(1 for texts in texts_per_frame if _has_readable_text(texts))
    fraction = frames_with_text / len(frames)
    logger.info(
        "OCR pre-check: readable text in %d/%d sampled frames (%.0f%%)",
        frames_with_text, len(frames), fraction * 100,
    )
    return fraction > _SIGNIFICANT_FRAME_FRACTION


def _has_readable_text(texts: list[str]) -> bool:
    return any(len(t.strip()) >= _MIN_FRAGMENT_CHARS for t in texts)


def _ocr_frames(frames: list[Path]) -> list[list[str]] | None:
    """OCR each frame; one list of recognized strings per frame.

    Tries Apple Vision (macOS, no extra deps), then pytesseract.
    Returns None if no backend is usable.
    """
    if shutil.which("osascript") is not None:
        try:
            return _ocr_frames_vision(frames)
        except Exception:
            logger.warning("Apple Vision OCR failed; trying pytesseract", exc_info=True)
    try:
        return _ocr_frames_pytesseract(frames)
    except Exception:
        return None


def _ocr_frames_vision(frames: list[Path]) -> list[list[str]]:
    """OCR via Apple's Vision framework, driven through osascript (JXA)."""
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", _VISION_OCR_JXA]
        + [str(f) for f in frames],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"osascript Vision OCR failed: {proc.stderr[-500:]}")
    lines = proc.stdout.strip().splitlines()
    if len(lines) != len(frames):
        raise RuntimeError(
            f"osascript Vision OCR returned {len(lines)} results for {len(frames)} frames"
        )
    return [json.loads(line) for line in lines]


def _ocr_frames_pytesseract(frames: list[Path]) -> list[list[str]]:
    """OCR via pytesseract (imported lazily; optional fallback backend)."""
    import pytesseract  # noqa: PLC0415 — optional, imported lazily
    from PIL import Image  # noqa: PLC0415

    results: list[list[str]] = []
    for frame in frames:
        with Image.open(frame) as img:
            text = pytesseract.image_to_string(img)
        results.append([line for line in text.splitlines() if line.strip()])
    return results


# ---------------------------------------------------------------------------
# Vozo provider
# ---------------------------------------------------------------------------


class VozoError(RuntimeError):
    """A Vozo API request or job failed."""


class VozoOnScreenTextProvider(OnScreenTextProvider):
    """Localize burned-in on-screen text using Vozo's public API.

    Flow: publish media URL -> POST /v1/media/translate -> poll
    GET /v1/media/translate/{task_id} -> download result.video_url.

    Known limitations of Vozo's public API (as documented in 2026-07):

    - Visual Translate (the dashboard product that erases and rebuilds
      on-screen text preserving layout/style/animation) has no public
      endpoint; the closest documented mechanism is a Translate & Dub job
      with an ``ocr_text_box`` (see ``_build_payload``).
    - There is no upload endpoint: the API downloads media from a publicly
      accessible URL. Provide ``media_url_resolver`` (sync or async
      ``Path -> str``, e.g. a presigned-S3 uploader) to publish local files.

    ``last_cost_points`` holds the estimated point cost of the most recent
    successful job (documented per-minute rate x video duration); the API
    itself does not report actual point consumption.
    """

    BASE_URL = "https://api.vozo.ai"
    TERMINAL_STATUSES = frozenset({"done", "failed"})
    # Documented rates (points/minute of uploaded video). The endpoint used
    # here is Translate & Dub; Visual Translate's dashboard rate is kept for
    # reference in case the visual endpoint becomes public.
    TRANSLATE_DUB_POINTS_PER_MINUTE = 3.0
    VISUAL_TRANSLATE_POINTS_PER_MINUTE = 10.0

    def __init__(
        self,
        api_key: str,
        *,
        media_url_resolver: Callable[[Path], str | Awaitable[str]] | None = None,
        source_language: str = "auto",
        ocr_text_box: dict[str, float] | None = None,
        timeout_s: float = 1800.0,
    ) -> None:
        self.api_key = api_key
        self.source_language = source_language
        self.last_cost_points: float | None = None
        self._media_url_resolver = media_url_resolver
        # Default to full-frame OCR — the only documented on-screen-text knob.
        self._ocr_text_box = (
            ocr_text_box
            if ocr_text_box is not None
            else {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        )
        self._timeout_s = timeout_s

    async def localize_text(
        self, video: Path, target_language: str, work_dir: Path
    ) -> Path:
        work_dir.mkdir(parents=True, exist_ok=True)
        self.last_cost_points = None
        target = self._normalize_language(target_language)
        media_url = await self._resolve_media_url(video)
        estimated_points = await self._estimate_cost_points(video)

        async with httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(120.0, connect=15.0),
        ) as client:
            task_id = await self._submit_job(client, media_url, target)
            if estimated_points is not None:
                logger.info(
                    "Vozo on-screen text job %s submitted (estimated cost ~%.1f points; "
                    "deducted by Vozo on successful completion)",
                    task_id, estimated_points,
                )
            status = await poll_until_complete(
                lambda: self._get_status(client, task_id),
                lambda s: s.get("status") in self.TERMINAL_STATUSES,
                timeout_s=self._timeout_s,
                describe=f"Vozo on-screen text job {task_id}",
            )

        if status.get("status") == "failed":
            raise VozoError(
                f"Vozo job {task_id} failed "
                f"(err_code={status.get('err_code')}): {status.get('err_message')}"
            )
        video_url = (status.get("result") or {}).get("video_url")
        if not video_url:
            raise VozoError(f"Vozo job {task_id} finished without a result video_url")

        out_path = work_dir / f"{video.stem}_onscreen_{target}.mp4"
        await self._download(video_url, out_path)
        self.last_cost_points = estimated_points
        return out_path

    # -- API calls ----------------------------------------------------------

    async def _submit_job(
        self, client: httpx.AsyncClient, media_url: str, target_language: str
    ) -> str:
        resp = await client.post(
            "/v1/media/translate", json=self._build_payload(media_url, target_language)
        )
        data = self._parse_response(resp, context="create job")
        task_id = data.get("task_id")
        if not task_id:
            raise VozoError(f"Vozo create-job response missing task_id: {data}")
        return task_id

    async def _get_status(self, client: httpx.AsyncClient, task_id: str) -> dict:
        resp = await client.get(f"/v1/media/translate/{task_id}")
        return self._parse_response(resp, context=f"job {task_id} status")

    def _build_payload(self, media_url: str, target_language: str) -> dict:
        """Request body for POST /v1/media/translate.

        UNCERTAIN: Vozo's Visual Translate has no public endpoint, so this
        submits a Translate & Dub job with a full-frame ``ocr_text_box`` —
        the only documented API parameter concerning on-screen text (docs
        describe it as an OCR source-text hint, not a full erase-and-rebuild
        visual translation). The output video/audio are fully localized, but
        burned-in text may not be visually rebuilt the way the dashboard
        product does. Pass ``ocr_text_box={...}`` in __init__ to target a
        specific region, or ``ocr_text_box={}`` to omit the box entirely;
        swap this method once Vozo publishes a visual-translate endpoint.
        """
        payload: dict = {
            "media_type": "video",
            "media_url": media_url,
            "source_language": self.source_language,
            "target_language": target_language,
            "export_type": "video",
            "project_mode": "none",
        }
        if self._ocr_text_box:
            payload["ocr_text_box"] = self._ocr_text_box
        return payload

    @staticmethod
    def _parse_response(resp: httpx.Response, *, context: str) -> dict:
        if resp.status_code != 200:
            try:
                err = resp.json()
                detail = f"code={err.get('code')} message={err.get('message')}"
            except ValueError:
                detail = resp.text[:500]
            raise VozoError(f"Vozo API error during {context} (HTTP {resp.status_code}): {detail}")
        return resp.json()

    @staticmethod
    async def _download(url: str, dest: Path) -> Path:
        # Fresh unauthenticated client: result URLs point at Vozo's CDN and
        # must not receive our API key.
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=15.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        return dest

    # -- helpers -------------------------------------------------------------

    async def _resolve_media_url(self, video: Path) -> str:
        """Publish `video` at a publicly accessible URL.

        UNCERTAIN/limited by Vozo: the public API has no upload endpoint —
        its documented flow starts with "Host your assets at a publicly
        accessible URL". Callers must supply ``media_url_resolver`` (e.g. a
        presigned-S3/GCS uploader) until Vozo ships an upload API.
        """
        if self._media_url_resolver is not None:
            result = self._media_url_resolver(video)
            if inspect.isawaitable(result):
                result = await result
            return str(result)
        raise VozoError(
            "Vozo's public API has no upload endpoint; it downloads media from a "
            "publicly accessible URL. Construct VozoOnScreenTextProvider with "
            "media_url_resolver=<callable publishing a local Path and returning its "
            "public URL> (e.g. a presigned-S3 upload)."
        )

    async def _estimate_cost_points(self, video: Path) -> float | None:
        """Estimated point cost (documented rate x duration); None if unknown.

        The API does not return actual point consumption; points are deducted
        by Vozo when a job completes successfully.
        """
        try:
            duration_s = await asyncio.to_thread(probe_duration, video)
        except Exception:
            logger.warning("Could not probe %s to estimate Vozo point cost", video)
            return None
        return self.TRANSLATE_DUB_POINTS_PER_MINUTE * duration_s / 60.0

    @staticmethod
    def _normalize_language(code: str) -> str:
        """Normalize to Vozo's expected form: 'es', 'es-MX', 'zh-CN', ..."""
        parts = code.strip().replace("_", "-").split("-")
        lang = parts[0].lower()
        if len(parts) > 1 and parts[1]:
            return f"{lang}-{parts[1].upper()}"
        return lang
