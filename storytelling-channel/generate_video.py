import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from moviepy.editor import (
    ColorClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    AudioFileClip,
    concatenate_videoclips
)

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

def create_title_frame(title, out_path):
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), color=(12, 17, 24))
    draw = ImageDraw.Draw(image)

    for y in range(0, height, 18):
        draw.line((0, y, width, y), fill=(18 + y // 12, 24 + y // 12, 36 + y // 12), width=2)

    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
    sub_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)

    text = title[:32] + "..." if len(title) > 32 else title
    box = draw.textbbox((0, 0), text, font=title_font)
    tw = box[2] - box[0]
    x = (width - tw) / 2
    draw.text((x, 210), text, font=title_font, fill=(255, 255, 255))

    subtitle = "A Storytelling Series"
    sb = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = sb[2] - sb[0]
    draw.text(((width - sw) / 2, 560), subtitle, font=sub_font, fill=(220, 220, 220))

    image.save(out_path)

def generate_voiceover(script, output_path):
    tts = gTTS(text=script, lang="en", slow=False)
    tts.save(str(output_path))

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

def create_episode_video(title, script):
    output_dir = OUTPUT_DIR
    output_file = output_dir / "story_episode.mp4"
    title_frame = output_dir / "title_frame.png"
    voice_path = output_dir / "narration.mp3"

    create_title_frame(title, str(title_frame))
    generate_voiceover(script, str(voice_path))

    stock_clips = get_stock_clips()
    segments = []

    if stock_clips:
        for clip in stock_clips[:4]:
            v = VideoFileClip(str(clip))
            v = v.resize((1280, 720))
            v = v.subclip(0, min(v.duration, 10))
            segments.append(v)
    else:
        for i in range(4):
            segments.append(make_scene_clip(title, f"Chapter {i + 1}", duration=8))

    opener = make_scene_clip(title, "Episode Begins", duration=8)
    narration = AudioFileClip(str(voice_path))

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
