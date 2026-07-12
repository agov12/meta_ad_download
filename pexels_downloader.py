"""
Download stock videos from Pexels for use as clean (text-free) source clips.

Setup:
  1. Get a free API key at https://www.pexels.com/api/ (instant, no approval wait)
  2. export PEXELS_API_KEY="your_key_here"
  3. pip install requests
  4. python pexels_downloader.py --num_videos 5 --output_dir ./clean_clips

Options:
  --queries "cooking,workout"   comma-separated search terms (default: ad-style mix)
  --num_videos 5                total videos to download (spread across queries)
  --max_duration 15             skip clips longer than this (seconds)
  --target_height 720           prefer this resolution (falls back to closest)
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "https://api.pexels.com/videos/search"

# Ad-flavored default queries - the kinds of scenes real ad creative uses.
# Weighted: talking-to-camera terms appear multiple times so they dominate
# the downloaded mix (that's the most important category for UGC-style ads).
DEFAULT_QUERIES = [
    # talking to camera (UGC / influencer style) - most important, so 4 slots
    "woman talking to camera",
    "man talking to camera",
    "vlogger talking selfie video",
    "influencer speaking phone camera",
    # skincare
    "skincare routine",
    "applying face cream",
    # sunscreen / UV products
    "applying sunscreen",
    "sunscreen skin protection",
    # food products
    "food product kitchen",
    "healthy food preparation",
    # beverage products
    "drink beverage pouring",
    "smoothie juice drink",
]


def pick_video_file(video: dict, target_height: int) -> dict | None:
    """From a Pexels video's available renditions, pick the one closest to target_height."""
    files = video.get("video_files", [])
    mp4s = [f for f in files if f.get("file_type") == "video/mp4" and f.get("height")]
    if not mp4s:
        return None
    return min(mp4s, key=lambda f: abs(f["height"] - target_height))


def download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return True
    except requests.RequestException as e:
        print(f"    download failed: {e}")
        if dest.exists():
            dest.unlink()
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num_videos", type=int, default=5)
    ap.add_argument("--output_dir", default="./clean_clips")
    ap.add_argument("--queries", default=None,
                    help="Comma-separated search terms; defaults to an ad-style mix")
    ap.add_argument("--max_duration", type=int, default=15,
                    help="Skip videos longer than this many seconds")
    ap.add_argument("--target_height", type=int, default=720)
    args = ap.parse_args()

    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        sys.exit("ERROR: set the PEXELS_API_KEY environment variable first.\n"
                 "Get a free key at https://www.pexels.com/api/")

    queries = ([q.strip() for q in args.queries.split(",") if q.strip()]
               if args.queries else DEFAULT_QUERIES)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": api_key}

    downloaded = 0
    qi = 0
    seen_ids = set()

    while downloaded < args.num_videos and qi < len(queries) * 3:
        query = queries[qi % len(queries)]
        qi += 1
        page = (qi // len(queries)) + 1  # walk deeper into results on later passes

        print(f"Searching: '{query}' (page {page})")
        try:
            resp = requests.get(
                API_URL, headers=headers, timeout=30,
                params={"query": query, "per_page": 10, "page": page,
                        "orientation": "portrait"},  # ads are mostly vertical; change if needed
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  search failed: {e}")
            continue

        for video in resp.json().get("videos", []):
            if downloaded >= args.num_videos:
                break
            vid = video["id"]
            if vid in seen_ids:
                continue
            seen_ids.add(vid)

            if video.get("duration", 999) > args.max_duration:
                continue

            vf = pick_video_file(video, args.target_height)
            if not vf:
                continue

            dest = out_dir / f"pexels_{vid}.mp4"
            if dest.exists():
                continue

            print(f"  downloading {dest.name} "
                  f"({video['duration']}s, {vf['width']}x{vf['height']}, by {video['user']['name']})")
            if download(vf["link"], dest):
                downloaded += 1
            time.sleep(0.5)  # be polite to the API

    print(f"\nDone: {downloaded} videos in {out_dir.resolve()}")
    if downloaded < args.num_videos:
        print("(Fewer than requested - try more/different --queries or raise --max_duration)")


if __name__ == "__main__":
    main()
