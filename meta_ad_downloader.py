#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "playwright>=1.45",
#     "requests>=2.31",
# ]
# ///
"""
Meta Ad Library Video Downloader
---------------------------------
Automates the "dev tools" method: loads an Ad Library page in a headless
browser, captures video URLs from network traffic (and page HTML), strips
byte-range parameters, and downloads the full media file.

Usage:
    # Single ad
    python meta_ad_downloader.py "https://www.facebook.com/ads/library/?id=2069087177342944"

    # Just the ad ID also works
    python meta_ad_downloader.py 2069087177342944

    # Batch mode: a text file with one URL or ID per line
    python meta_ad_downloader.py --batch ads.txt

    # Custom output folder / visible browser for debugging
    python meta_ad_downloader.py 2069087177342944 -o ./downloads --headed

Requirements:
    pip install playwright requests
    playwright install chromium
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AD_LIBRARY_URL = "https://www.facebook.com/ads/library/?id={ad_id}"

VIDEO_URL_PATTERN = re.compile(
    r'https://video[^"\'\\\s]+\.fbcdn\.net[^"\'\\\s]+', re.IGNORECASE
)
# Video URLs embedded in page JSON, e.g. "video_sd_url":"https:\/\/video..."
EMBEDDED_VIDEO_KEYS = ("video_hd_url", "video_sd_url", "playable_url_quality_hd", "playable_url")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def extract_ad_id(url_or_id: str) -> str:
    """Accepts a full Ad Library URL or a bare numeric ID."""
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return url_or_id
    parsed = urlparse(url_or_id)
    qs = parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]
    raise ValueError(f"Could not find an ad ID in: {url_or_id}")


def strip_byte_range(url: str) -> str:
    """Remove bytestart/byteend params so we get the whole file."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("bytestart", None)
    qs.pop("byteend", None)
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def unescape_json_url(url: str) -> str:
    """URLs found inside page JSON have escaped slashes: https:\\/\\/..."""
    return url.replace("\\/", "/").replace("\\u0025", "%").replace("\\u002F", "/")


def base_video_key(url: str) -> str:
    """Key to deduplicate chunked requests of the same video (path w/o query)."""
    p = urlparse(url)
    return p.netloc + p.path


# ---------------------------------------------------------------------------
# Core scraping logic
# ---------------------------------------------------------------------------

async def collect_media_urls(page, ad_url: str, wait_seconds: float = 8.0):
    """
    Load the ad page and gather candidate media URLs from:
      1. Network responses (the automated 'dev tools Network tab')
      2. Video URLs embedded in the page's JSON payload
      3. <video> elements' src attributes
    Returns (video_urls, image_urls) as de-duplicated lists.
    """
    video_urls: dict[str, str] = {}   # base_key -> full url
    image_urls: dict[str, str] = {}

    def on_response(response):
        url = response.url
        if "fbcdn.net" not in url:
            return
        ctype = (response.headers or {}).get("content-type", "")
        if "video" in ctype or ".mp4" in url:
            video_urls.setdefault(base_video_key(url), url)
        elif "image" in ctype and "scontent" in url:
            # Ad images (also catches thumbnails; we filter by size later)
            image_urls.setdefault(base_video_key(url), url)

    page.on("response", on_response)

    await page.goto(ad_url, wait_until="domcontentloaded", timeout=60_000)

    # Try to dismiss the cookie banner if one appears (region dependent)
    for label in ("Decline optional cookies", "Only allow essential cookies", "Allow all cookies"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click(timeout=2_000)
                break
        except Exception:
            pass

    # Give the page time to render and start streaming the video
    await page.wait_for_timeout(int(wait_seconds * 1000))

    # Nudge any <video> elements to play, in case autoplay didn't fire
    try:
        await page.evaluate(
            """() => {
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true;
                    const p = v.play();
                    if (p && p.catch) p.catch(() => {});
                });
            }"""
        )
        await page.wait_for_timeout(4_000)
    except Exception:
        pass

    # Source 2: video URLs embedded in the page HTML/JSON
    try:
        html = await page.content()
        for key in EMBEDDED_VIDEO_KEYS:
            for m in re.finditer(rf'"{key}"\s*:\s*"([^"]+)"', html):
                candidate = unescape_json_url(m.group(1))
                if "fbcdn.net" in candidate:
                    video_urls.setdefault(base_video_key(candidate), candidate)
        # Any raw video fbcdn URLs in the page
        for m in VIDEO_URL_PATTERN.finditer(html):
            candidate = unescape_json_url(m.group(0))
            video_urls.setdefault(base_video_key(candidate), candidate)
    except Exception:
        pass

    # Source 3: <video src=...>
    try:
        srcs = await page.eval_on_selector_all(
            "video", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)"
        )
        for s in srcs:
            if s.startswith("http") and "fbcdn.net" in s:
                video_urls.setdefault(base_video_key(s), s)
    except Exception:
        pass

    return list(video_urls.values()), list(image_urls.values())


def download_file(url: str, dest: Path) -> int:
    """Download a URL to dest. Returns bytes written."""
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.facebook.com/"}
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        written = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                written += len(chunk)
    return written


def pick_best(urls: list[str], out_dir: Path, ad_id: str) -> Path | None:
    """
    Download candidates (byte-ranges stripped) and keep the largest file —
    the same 'sort by size' trick used manually in dev tools.
    """
    best_path, best_size = None, 0
    for i, raw_url in enumerate(urls):
        url = strip_byte_range(raw_url)
        tmp = out_dir / f".{ad_id}_candidate_{i}.mp4"
        try:
            size = download_file(url, tmp)
            print(f"    candidate {i}: {size/1e6:.2f} MB")
            if size > best_size:
                if best_path and best_path.exists():
                    best_path.unlink()
                best_path, best_size = tmp, size
            else:
                tmp.unlink(missing_ok=True)
        except Exception as e:
            print(f"    candidate {i}: failed ({e})")
            tmp.unlink(missing_ok=True)

    if best_path:
        final = out_dir / f"{ad_id}.mp4"
        best_path.rename(final)
        return final
    return None


async def download_ad(ad_input: str, out_dir: Path, headed: bool = False) -> bool:
    ad_id = extract_ad_id(ad_input)
    ad_url = AD_LIBRARY_URL.format(ad_id=ad_id)
    print(f"[{ad_id}] loading {ad_url}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not headed)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1400, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        try:
            videos, images = await collect_media_urls(page, ad_url)
        finally:
            await browser.close()

    print(f"[{ad_id}] found {len(videos)} video candidate(s), {len(images)} image candidate(s)")

    if videos:
        result = pick_best(videos, out_dir, ad_id)
        if result:
            print(f"[{ad_id}] ✅ saved video -> {result}")
            return True
        print(f"[{ad_id}] ⚠️ video candidates found but all downloads failed")

    # Fallback: image ad — keep the largest image
    if images:
        best_path, best_size = None, 0
        for i, url in enumerate(images):
            tmp = out_dir / f".{ad_id}_img_{i}"
            try:
                size = download_file(strip_byte_range(url), tmp)
                if size > best_size:
                    if best_path:
                        best_path.unlink(missing_ok=True)
                    best_path, best_size = tmp, size
                else:
                    tmp.unlink(missing_ok=True)
            except Exception:
                tmp.unlink(missing_ok=True)
        if best_path and best_size > 20_000:  # ignore tiny icons/thumbnails
            final = out_dir / f"{ad_id}.jpg"
            best_path.rename(final)
            print(f"[{ad_id}] ✅ saved image -> {final} ({best_size/1e6:.2f} MB)")
            return True
        if best_path:
            best_path.unlink(missing_ok=True)

    print(f"[{ad_id}] ❌ no media captured. Try --headed to watch what the page does, "
          f"or increase the wait time.")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Download video/image creatives from Meta Ad Library URLs")
    ap.add_argument("ad", nargs="?", help="Ad Library URL or numeric ad ID")
    ap.add_argument("--batch", help="Path to a text file with one URL/ID per line")
    ap.add_argument("-o", "--output", default="downloads", help="Output directory (default: ./downloads)")
    ap.add_argument("--headed", action="store_true", help="Show the browser window (debugging)")
    ap.add_argument("--min-delay", type=float, default=5.0, help="Min seconds between ads in batch mode")
    ap.add_argument("--max-delay", type=float, default=15.0, help="Max seconds between ads in batch mode")
    args = ap.parse_args()

    if not args.ad and not args.batch:
        ap.error("Provide an ad URL/ID or --batch file")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = []
    if args.batch:
        targets += [l.strip() for l in Path(args.batch).read_text().splitlines()
                    if l.strip() and not l.strip().startswith("#")]
    if args.ad:
        targets.append(args.ad)

    print(f"{len(targets)} ad(s) to download -> {out_dir.resolve()}\n")

    ok = 0
    for i, target in enumerate(targets):
        try:
            if asyncio.run(download_ad(target, out_dir, headed=args.headed)):
                ok += 1
        except Exception as e:
            print(f"[{target}] error: {e}")
        # Polite randomized delay between requests in batch mode
        if i < len(targets) - 1:
            delay = random.uniform(args.min_delay, args.max_delay)
            print(f"  waiting {delay:.0f}s...\n")
            time.sleep(delay)

    print(f"\nDone: {ok}/{len(targets)} succeeded.")


if __name__ == "__main__":
    main()
