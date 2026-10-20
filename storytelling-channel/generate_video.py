from pathlib import Path

import numpy as np

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from moviepy.editor import (
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    AudioFileClip,
    concatenate_videoclips
)

from providers import generate_image, generate_speech

# moviepy 1.0.3 resizes with Image.ANTIALIAS, which Pillow 10 removed.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

STOCK_DIR = Path("assets/stock")
STOCK_DIR.mkdir(parents=True, exist_ok=True)

def get_stock_clips():
    files = []
    for pattern in ["*.mp4", "*.mov", "*.mkv", "*.avi"]:
        files.extend(STOCK_DIR.glob(pattern))
    return files

def fit_image(path, size):
    """Scale and center-crop an image so it exactly fills size."""
    with Image.open(path) as source:
        image = source.convert("RGB")
    scale = max(size[0] / image.width, size[1] / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (image.width - size[0]) // 2
    top = (image.height - size[1]) // 2
    return image.crop((left, top, left + size[0], top + size[1]))

def wrap_text(draw, text, font, max_width, max_lines=3):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + "..."
    return lines

def create_title_frame(title, out_path, background=None):
    width, height = 1280, 720
    if background:
        image = ImageEnhance.Brightness(fit_image(background, (width, height))).enhance(0.55)
        draw = ImageDraw.Draw(image)
    else:
        image = Image.new("RGB", (width, height), color=(12, 17, 24))
        draw = ImageDraw.Draw(image)
        for y in range(0, height, 18):
            draw.line((0, y, width, y), fill=(18 + y // 12, 24 + y // 12, 36 + y // 12), width=2)

    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
    sub_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)

    lines = wrap_text(draw, title, title_font, width - 160)
    line_height = 78
    y = (height - line_height * len(lines)) / 2 - 40
    for line in lines:
        tw = draw.textlength(line, font=title_font)
        draw.text(((width - tw) / 2, y), line, font=title_font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
        y += line_height

    subtitle = "A Storytelling Series"
    sb = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = sb[2] - sb[0]
    draw.text(((width - sw) / 2, 560), subtitle, font=sub_font, fill=(220, 220, 220))

    image.save(out_path)

def generate_voiceover(script, output_path):
    return generate_speech(script, output_path)

def generate_scene_images(scene_prompts):
    images = []
    for i, prompt in enumerate(scene_prompts):
        path = generate_image(f"{prompt}, cinematic film still, 16:9, no text", OUTPUT_DIR / f"scene_{i + 1}.png")
        if not path:
            # Same provider error would repeat for every scene (no key, quota, paid tier).
            break
        images.append(path)
    print(f"Generated {len(images)} of {len(scene_prompts)} scene images")
    return images

def make_image_clip(path, duration, index):
    """A slow pan across a still image so it doesn't sit completely static."""
    frame = (1280, 720)
    oversize = (1408, 792)
    image = ImageClip(np.array(fit_image(path, oversize))).set_duration(duration)
    dx, dy = frame[0] - oversize[0], frame[1] - oversize[1]
    if index % 2:
        position = lambda t: (dx * (1 - t / duration), dy * (1 - t / duration))
    else:
        position = lambda t: (dx * t / duration, dy * t / duration)
    return CompositeVideoClip([image.set_position(position)], size=frame).set_duration(duration)

def make_scene_clip(title, subtitle, duration=8):
    base = ColorClip((1280, 720), color=(18, 20, 28), duration=duration)

    title_clip = TextClip(
        title,
        fontsize=46,
        color="white",
        font="DejaVu-Sans-Bold",
        method="caption",
        size=(base.w * 0.8, None),
        stroke_color="black",
        stroke_width=2
    ).set_position(("center", 0.22), relative=True).set_duration(base.duration)

    subtitle_clip = TextClip(
        subtitle,
        fontsize=24,
        color="#E4E4E4",
        font="DejaVu-Sans",
        method="caption",
        size=(base.w * 0.7, None),
        stroke_color="black",
        stroke_width=1
    ).set_position(("center", 0.72), relative=True).set_duration(base.duration)

    return CompositeVideoClip([base, title_clip, subtitle_clip], size=(1280, 720))

def create_thumbnail(title, background=None):
    thumbnail = OUTPUT_DIR / "thumbnail.jpg"
    create_title_frame(title, str(thumbnail), background=background)
    return str(thumbnail)

def create_episode_video(title, script, scene_images=None):
    output_dir = OUTPUT_DIR
    output_file = output_dir / "story_episode.mp4"
    title_frame = output_dir / "title_frame.png"
    voice_path = output_dir / "narration.mp3"

    create_title_frame(title, str(title_frame))
    generate_voiceover(script, str(voice_path))

    stock_clips = get_stock_clips()
    segments = []
    narration = AudioFileClip(str(voice_path))

    if scene_images:
        # Spread the AI scene images evenly across the narration after the opener.
        per_image = max(8, (narration.duration - 8) / len(scene_images))
        for i, path in enumerate(scene_images):
            segments.append(make_image_clip(path, per_image, i))
    elif stock_clips:
        for clip in stock_clips[:4]:
            v = VideoFileClip(str(clip))
            v = v.resize((1280, 720))
            v = v.subclip(0, min(v.duration, 10))
            segments.append(v)
    else:
        for i in range(4):
            segments.append(make_scene_clip(title, f"Chapter {i + 1}", duration=8))

    opener = make_scene_clip(title, "Episode Begins", duration=8)

    # Cycle through the scene segments until the visuals cover the whole narration.
    final_segments = [opener]
    total = opener.duration
    while total < narration.duration:
        for segment in segments:
            final_segments.append(segment)
            total += segment.duration
            if total >= narration.duration:
                break

    final_video = concatenate_videoclips(final_segments, method="compose")

    final_video = final_video.subclip(0, min(final_video.duration, narration.duration))
    final_video = final_video.set_audio(narration)

    final_video.write_videofile(
        str(output_file),
        fps=24,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(output_dir / "temp_audio.m4a"),
        remove_temp=True
    )

    print(f"Episode created: {output_file}")
    return str(output_file)
