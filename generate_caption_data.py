"""
Synthetic training data generator for video caption removal.

Emulates spoken-word captions (TikTok/Reels style): ONE caption track per clip,
showing 3-4 words at a time, cycling to the next chunk every few seconds,
running throughout the whole clip.

Outputs per variant:
  <name>_clean.mp4   - original clip (ground truth)
  <name>_overlay.mp4 - clip with captions burned in (model input)
  <name>_mask.mp4    - binary mask of caption region per frame (white = text)

Usage:
  pip install opencv-python pillow numpy
  python generate_caption_data.py --input_dir ./clean_clips --output_dir ./dataset \
      --variants_per_clip 3 --fonts_dir ./fonts
"""

import argparse
import os
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Spoken-style ad lines. Each gets chunked into 3-4 word caption groups,
# mimicking text that appears as the speaker says it.
# ---------------------------------------------------------------------------
SPOKEN_LINES = [
    "this product completely changed my morning routine and I honestly cannot imagine going back",
    "I was skeptical at first but after two weeks the results really speak for themselves",
    "stop scrolling because this is the deal you have been waiting for all year",
    "we shipped to over thirty countries last month and the reviews keep pouring in",
    "here is why thousands of people are switching to this app every single day",
    "you only need five minutes a day to see a real difference in your skin",
    "the best part is it works right out of the box with no setup required",
    "I tried every option on the market and nothing comes close to this one",
    "our customers save an average of two hundred dollars in their first month alone",
    "download the app today and get your first month completely free no strings attached",
    "this is hands down the easiest way to learn a new language from home",
    "watch what happens when I apply just one drop of this to my hair",
    "if you struggle with sleep you need to hear about this right now",
    "the secret is in the ingredients which are sourced from small farms worldwide",
    "join over a million happy customers who already made the switch this year",
]

# Caption visual styles (weighted by realism: white bold with shadow dominates)
CAPTION_STYLES = [
    {"color": (255, 255, 255), "shadow": True,  "box": False},  # classic white + shadow
    {"color": (255, 255, 255), "shadow": True,  "box": False},
    {"color": (255, 255, 255), "shadow": False, "box": True},   # white on dark box
    {"color": (255, 214, 0),   "shadow": True,  "box": False},  # yellow (CapCut-style)
    {"color": (0, 0, 0),       "shadow": False, "box": True},   # black on light box
]


def load_fonts(fonts_dir: str) -> list:
    fonts = []
    if fonts_dir and os.path.isdir(fonts_dir):
        for f in Path(fonts_dir).rglob("*"):
            if f.suffix.lower() in (".ttf", ".otf"):
                fonts.append(str(f))
    return fonts


def chunk_words(line: str) -> list:
    """Split a spoken line into caption chunks of 3-4 words."""
    words = line.split()
    chunks, i = [], 0
    while i < len(words):
        n = random.choice([3, 4])
        # avoid a trailing 1-word orphan chunk
        if len(words) - i <= 5 and len(words) - i != n:
            n = len(words) - i if len(words) - i <= 4 else 3
        chunks.append(" ".join(words[i:i + n]))
        i += n
    return chunks


def build_caption_track(duration_s: float) -> list:
    """
    Build a full-clip caption schedule: list of (text, t_start_s, t_end_s).
    Chunks cycle back-to-back every ~1.2-2.5s until the clip ends,
    pulling new spoken lines as needed.
    """
    track, t = [], 0.15  # tiny lead-in before first caption
    while t < duration_s - 0.3:
        for chunk in chunk_words(random.choice(SPOKEN_LINES)):
            dur = random.uniform(1.2, 2.5)
            end = min(t + dur, duration_s)
            track.append((chunk, t, end))
            t = end + random.uniform(0.0, 0.15)  # tiny gap sometimes, often none
            if t >= duration_s - 0.3:
                break
    return track


def sample_clip_config(width: int, height: int, fonts: list) -> dict:
    """One consistent caption style + position for the whole clip (like real captions)."""
    style = random.choice(CAPTION_STYLES)
    return {
        "font_path": random.choice(fonts) if fonts else None,
        "font_size": random.randint(int(height * 0.045), int(height * 0.075)),
        "y_frac": random.uniform(0.68, 0.85),  # captions live in the lower band
        "pop_frames": random.choice([0, 2, 3]),  # quick scale-pop on chunk entry
        **style,
    }


def get_font(path, size):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_caption(cfg, text, width, height, frames_since_start):
    """Render one caption chunk frame as RGBA layer + uint8 mask."""
    # pop-in: start ~85% size, reach 100% over pop_frames
    size = cfg["font_size"]
    if cfg["pop_frames"] and frames_since_start < cfg["pop_frames"]:
        scale = 0.85 + 0.15 * (frames_since_start / cfg["pop_frames"])
        size = max(8, int(size * scale))
    font = get_font(cfg["font_path"], size)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = int(cfg["y_frac"] * height) - th // 2

    pad = int(size * 0.3)
    if cfg["box"]:
        box_fill = (20, 20, 20, 210) if cfg["color"] != (0, 0, 0) else (245, 245, 245, 220)
        draw.rounded_rectangle(
            [x - pad, y - pad // 2, x + tw + pad, y + th + pad],
            radius=pad // 2, fill=box_fill,
        )
    if cfg["shadow"]:
        off = max(2, size // 16)
        draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, 180))

    draw.text((x, y), text, font=font, fill=(*cfg["color"], 255))

    mask = np.array(layer.split()[-1])
    mask = (mask > 20).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
    return layer, mask


def process_clip(clip_path, out_dir, variant_idx, fonts, target_height=720):
    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"  !! could not open {clip_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = n_frames / fps
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    scale = target_height / src_h if src_h > target_height else 1.0
    width = int(src_w * scale) // 2 * 2
    height = int(src_h * scale) // 2 * 2

    cfg = sample_clip_config(width, height, fonts)
    track = build_caption_track(duration_s)

    stem = Path(clip_path).stem
    name = f"{stem}_v{variant_idx}"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    w_clean = cv2.VideoWriter(os.path.join(out_dir, f"{name}_clean.mp4"), fourcc, fps, (width, height))
    w_over = cv2.VideoWriter(os.path.join(out_dir, f"{name}_overlay.mp4"), fourcc, fps, (width, height))
    w_mask = cv2.VideoWriter(os.path.join(out_dir, f"{name}_mask.mp4"), fourcc, fps, (width, height), isColor=False)

    chunk_start_frame = {}  # track when each chunk first appeared (for pop anim)
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if scale != 1.0:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

        t = frame_idx / fps
        composite = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        frame_mask = np.zeros((height, width), np.uint8)

        # find the active caption chunk (at most one, by construction)
        for ci, (text, ts, te) in enumerate(track):
            if ts <= t < te:
                if ci not in chunk_start_frame:
                    chunk_start_frame[ci] = frame_idx
                layer, mask = render_caption(
                    cfg, text, width, height, frame_idx - chunk_start_frame[ci])
                composite = Image.alpha_composite(composite, layer)
                frame_mask = np.maximum(frame_mask, mask)
                break

        w_clean.write(frame)
        w_over.write(cv2.cvtColor(np.array(composite.convert("RGB")), cv2.COLOR_RGB2BGR))
        w_mask.write(frame_mask)
        frame_idx += 1

    cap.release()
    w_clean.release()
    w_over.release()
    w_mask.release()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--fonts_dir", default=None)
    ap.add_argument("--variants_per_clip", type=int, default=3)
    ap.add_argument("--target_height", type=int, default=720)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)
    fonts = load_fonts(args.fonts_dir)
    if not fonts:
        print("WARNING: no fonts found; using PIL default. Pass --fonts_dir with .ttf files "
              "(e.g. Montserrat Bold, Inter Bold) for realistic captions.\n")

    clips = [str(p) for p in Path(args.input_dir).rglob("*")
             if p.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv", ".webm")]
    print(f"Found {len(clips)} clean clips. Generating {args.variants_per_clip} variants each...")

    done = 0
    for clip in clips:
        for v in range(args.variants_per_clip):
            if process_clip(clip, args.output_dir, v, fonts, args.target_height):
                done += 1
        print(f"  {Path(clip).name}: done")

    print(f"\nGenerated {done} triplets ({done * 3} video files) in {args.output_dir}")


if __name__ == "__main__":
    main()
