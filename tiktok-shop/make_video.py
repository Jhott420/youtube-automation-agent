"""Render vertical (1080x1920) TikTok product videos from videos.json.

Each video is a list of "beats". Every beat shows one of your product photos or
clips with a big caption, and the voiceover reads the beat's "say" line. The last
beat is the call to action.

Examples:
    python make_video.py --list
    python make_video.py --product spin-scrubber
    python make_video.py --product spin-scrubber --video grout-check
    python make_video.py --all --silent     # no voiceover: add a trending sound in TikTok

Put your product photos/clips in media/<product-id>/ (jpg, png, webp, mp4, mov).
They are used in filename order, so name them 01.jpg, 02.jpg, ...
Without media the beats fall back to plain text cards.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
# Reuse the storytelling channel's voice providers (Gemini/OpenAI/ElevenLabs/gTTS).
sys.path.insert(0, str(HERE.parent / "storytelling-channel"))

from moviepy.editor import (  # noqa: E402
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)

# moviepy 1.0.3 resizes with Image.ANTIALIAS, which Pillow 10 removed.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

W, H = 1080, 1920
FPS = 30
MEDIA_DIR = HERE / "media"
OUTPUT_DIR = HERE / "output"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# TikTok's buttons cover the right edge and its caption covers the bottom, so text
# stays inside this box.
SAFE_LEFT, SAFE_RIGHT = 70, W - 170
CAPTION_CENTER_Y = 560

MIN_BEAT = 1.6    # seconds; shortest a beat may be on screen
CTA_SECONDS = 2.5
SILENT_CHARS_PER_SECOND = 14  # reading speed used to time beats without a voiceover


def load_videos():
    return json.loads((HERE / "videos.json").read_text())["products"]


def media_for(product_id):
    folder = MEDIA_DIR / product_id
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS)


# ---------- Text overlays (drawn with Pillow, so ImageMagick isn't needed) ----------

def wrap(draw, text, font, max_width):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def caption_overlay(text, size=78, center_y=CAPTION_CENTER_Y, boxed=False):
    """Transparent full-frame layer with a centered caption."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    max_width = SAFE_RIGHT - SAFE_LEFT
    # Shrink long captions so they stay within 4 lines.
    while True:
        font = ImageFont.truetype(FONT_BOLD, size)
        lines = wrap(draw, text, font, max_width - (60 if boxed else 0))
        if len(lines) <= 4 or size <= 44:
            break
        size -= 6
    line_height = int(size * 1.22)
    top = center_y - line_height * len(lines) // 2
    mid_x = (SAFE_LEFT + SAFE_RIGHT) // 2

    if boxed:
        widest = max(draw.textlength(line, font=font) for line in lines)
        pad = 34
        draw.rounded_rectangle(
            (mid_x - widest / 2 - pad, top - pad, mid_x + widest / 2 + pad, top + line_height * len(lines) + pad // 2),
            radius=28, fill=(255, 255, 255, 235),
        )
    for i, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        x, y = mid_x - width / 2, top + i * line_height
        if boxed:
            draw.text((x, y), line, font=font, fill=(17, 17, 17))
        else:
            draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=7, stroke_fill=(0, 0, 0))
    return np.array(layer)


def overlay_clip(rgba, duration):
    """ImageClip with the alpha channel as a mask, so the caption sits on top of the media."""
    rgb = ImageClip(rgba[:, :, :3]).set_duration(duration)
    mask = ImageClip(rgba[:, :, 3] / 255.0, ismask=True).set_duration(duration)
    return rgb.set_mask(mask)


# ---------- Backgrounds ----------

def cover(image, size):
    scale = max(size[0] / image.width, size[1] / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (image.width - size[0]) // 2, (image.height - size[1]) // 2
    return image.crop((left, top, left + size[0], top + size[1]))


def photo_background(path, duration, index):
    """Product photo filling the frame with a slow pan so it doesn't sit still."""
    with Image.open(path) as source:
        image = source.convert("RGB")
    big = (int(W * 1.12), int(H * 1.12))
    clip = ImageClip(np.array(cover(image, big))).set_duration(duration)
    dx, dy = W - big[0], H - big[1]
    if index % 2:
        position = lambda t: (dx * t / duration, dy * t / duration)
    else:
        position = lambda t: (dx * (1 - t / duration), dy * (1 - t / duration))
    return CompositeVideoClip([clip.set_position(position)], size=(W, H)).set_duration(duration)


def video_background(path, duration):
    clip = VideoFileClip(str(path), audio=False)
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resize(scale)
    clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)
    if clip.duration < duration:
        clip = clip.fx(vfx.loop, duration=duration)
    return clip.subclip(0, duration)


def card_background(duration, index):
    """Plain gradient card used when there are no product photos yet."""
    colors = [((255, 94, 98), (255, 153, 102)), ((54, 209, 220), (91, 134, 229)), ((142, 84, 233), (71, 118, 230))]
    top, bottom = colors[index % len(colors)]
    t = np.linspace(0, 1, H)[:, None, None]
    gradient = (np.array(top) * (1 - t) + np.array(bottom) * t).astype(np.uint8)
    return ImageClip(np.broadcast_to(gradient, (H, W, 3)).copy()).set_duration(duration)


def background(media, index, duration):
    if not media:
        return card_background(duration, index)
    path = media[index % len(media)]
    if path.suffix.lower() in VIDEO_EXTS:
        return video_background(path, duration)
    return photo_background(path, duration, index)


# ---------- Timing and voice ----------

def beat_durations(beats, total):
    """Split the voiceover length across beats in proportion to how much each one says."""
    lengths = [len(beat.get("say") or beat["text"]) for beat in beats]
    durations = [max(MIN_BEAT, total * n / sum(lengths)) for n in lengths]
    return durations


def narrate(lines, out_path):
    from providers import generate_speech
    return generate_speech("\n".join(lines), str(out_path))


# ---------- Render ----------

def render(product, video, silent=False):
    media = media_for(product["id"])
    beats = video["beats"]
    out_dir = OUTPUT_DIR / product["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{video['id']}.mp4"

    with tempfile.TemporaryDirectory() as tmp:
        voice = None
        if silent:
            speech_seconds = sum(len(b["text"]) / SILENT_CHARS_PER_SECOND + 0.8 for b in beats)
        else:
            voice_path = Path(tmp) / "voice.mp3"
            provider = narrate([b.get("say") or b["text"] for b in beats] + [video["cta"]], voice_path)
            print(f"  voice: {provider}")
            voice = AudioFileClip(str(voice_path))
            # The CTA line is narrated too, so leave its share for the CTA card.
            cta_share = len(video["cta"]) / (sum(len(b.get("say") or b["text"]) for b in beats) + len(video["cta"]))
            speech_seconds = voice.duration * (1 - cta_share)

        durations = beat_durations(beats, speech_seconds)
        segments = []
        for i, (beat, duration) in enumerate(zip(beats, durations)):
            first = i == 0
            layers = [background(media, i, duration)]
            # The hook gets a white box so it reads instantly with the sound off.
            layers.append(overlay_clip(caption_overlay(beat["text"], size=86 if first else 78, boxed=first), duration))
            segments.append(CompositeVideoClip(layers, size=(W, H)).set_duration(duration))

        cta_duration = max(CTA_SECONDS, (voice.duration - sum(durations) + 0.6) if voice else CTA_SECONDS)
        cta_layers = [background(media, len(beats), cta_duration)]
        cta_layers.append(overlay_clip(caption_overlay(f"{video['cta']} ↓", size=84, center_y=900, boxed=True), cta_duration))
        cta_layers.append(overlay_clip(caption_overlay(f"{product['name']} · {product['price']}", size=52, center_y=520), cta_duration))
        segments.append(CompositeVideoClip(cta_layers, size=(W, H)).set_duration(cta_duration))

        final = concatenate_videoclips(segments, method="compose")
        if voice:
            final = final.set_audio(voice.set_duration(min(voice.duration, final.duration)))
        final.write_videofile(
            str(out_file), fps=FPS, codec="libx264", audio_codec="aac", preset="medium",
            ffmpeg_params=["-pix_fmt", "yuv420p"], temp_audiofile=str(Path(tmp) / "temp_audio.m4a"),
            remove_temp=True, logger=None,
        )
        if voice:
            voice.close()

    post_text = f"{video['caption']}\n\n{' '.join(product['hashtags'])}\n"
    out_file.with_suffix(".txt").write_text(post_text)
    print(f"  saved {out_file.relative_to(HERE)} ({final.duration:.1f}s)")
    return out_file


def main():
    parser = argparse.ArgumentParser(description="Render vertical TikTok product videos from videos.json")
    parser.add_argument("--product", help="product id, e.g. spin-scrubber")
    parser.add_argument("--video", help="one video id from that product (default: all of its videos)")
    parser.add_argument("--all", action="store_true", help="render every video for every product")
    parser.add_argument("--silent", action="store_true", help="no voiceover; add a trending sound in the TikTok app")
    parser.add_argument("--list", action="store_true", help="list products and video ids")
    args = parser.parse_args()

    products = load_videos()
    if args.list or not (args.product or args.all):
        for product in products:
            count = len(media_for(product["id"]))
            print(f"{product['id']}  ({count} media file(s) in media/{product['id']}/)")
            for video in product["videos"]:
                print(f"    {video['id']}")
        return

    selected = products if args.all else [p for p in products if p["id"] == args.product]
    if not selected:
        sys.exit(f"Unknown product '{args.product}'. Run with --list to see the ids.")

    for product in selected:
        videos = [v for v in product["videos"] if not args.video or v["id"] == args.video]
        if not videos:
            sys.exit(f"Unknown video '{args.video}' for {product['id']}.")
        if not media_for(product["id"]):
            print(f"No photos in media/{product['id']}/ yet, so {product['id']} uses text cards.")
        for video in videos:
            print(f"Rendering {product['id']}/{video['id']}")
            render(product, video, silent=args.silent)


if __name__ == "__main__":
    main()
