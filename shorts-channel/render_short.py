"""Turns a script into a 1080x1920 YouTube Short:
voiceover -> word timings -> big word-by-word captions -> moving background -> MP4."""

import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
HERE = Path(__file__).resolve().parent
STOCK_DIR = HERE / "assets" / "stock"
MUSIC_DIR = HERE / "assets" / "music"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# Background colour pairs per format, so each series has its own look.
PALETTES = {
    "sounds-fake-fact": [(10, 30, 70), (0, 120, 140)],
    "twist-story": [(20, 5, 10), (90, 10, 30)],
    "impossible-choice": [(40, 10, 70), (200, 60, 40)],
    "last-signal-teaser": [(5, 15, 20), (20, 70, 60)],
}
HIGHLIGHT = (255, 221, 0)


def _font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:4])}...\n{result.stderr[-2000:]}")
    return result.stdout


def _duration(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


# ---------------------------------------------------------------- voiceover

def make_voiceover(script, out_path, voice="onyx", speed=1.12):
    """OpenAI TTS (natural voice) with gTTS as a fallback, then speed up a touch
    because faster narration holds attention better on Shorts."""
    raw = out_path.with_suffix(".raw.mp3")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with client.audio.speech.with_streaming_response.create(
            model=os.getenv("OPENAI_TTS_MODEL", "tts-1-hd"), voice=voice, input=script
        ) as response:
            response.stream_to_file(str(raw))
    except Exception as exc:  # noqa: BLE001 - any TTS failure falls back to gTTS
        print(f"OpenAI TTS unavailable ({exc}); falling back to gTTS.")
        from gtts import gTTS
        gTTS(text=script, lang="en").save(str(raw))
        speed = max(speed, 1.25)  # gTTS is slow and flat; push it harder

    _run(["ffmpeg", "-y", "-i", str(raw), "-filter:a",
          f"atempo={speed},loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", str(out_path)])
    raw.unlink(missing_ok=True)
    return out_path


def word_timings(audio_path, script):
    """Word-level timestamps from Whisper; falls back to spreading words by length."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="verbose_json",
                timestamp_granularities=["word"], prompt=script[:800],
            )
        words = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                 for w in (result.words or []) if w.word.strip()]
        if len(words) >= 5:
            return _align_to_script(words, script)
    except Exception as exc:  # noqa: BLE001
        print(f"Whisper timings unavailable ({exc}); estimating word timings.")
    return _estimate_timings(script, _duration(audio_path))


def _align_to_script(words, script):
    """Show the script's own spelling and punctuation, using Whisper's timing."""
    script_words = script.split()
    if abs(len(script_words) - len(words)) <= max(3, len(script_words) // 20):
        n = min(len(script_words), len(words))
        aligned = [{"word": script_words[i], "start": words[i]["start"], "end": words[i]["end"]}
                   for i in range(n)]
        if len(script_words) > n:  # put any trailing words on the last timestamp
            for extra in script_words[n:]:
                aligned.append({"word": extra, "start": aligned[-1]["end"], "end": aligned[-1]["end"] + 0.3})
        return aligned
    return words


def _estimate_timings(script, total):
    words = script.split()
    weights = [len(w) + 2 + (4 if re.search(r"[.!?]$", w) else 2 if w.endswith(",") else 0) for w in words]
    scale = total / sum(weights)
    t, out = 0.0, []
    for w, weight in zip(words, weights):
        out.append({"word": w, "start": t, "end": t + weight * scale})
        t += weight * scale
    return out


# ---------------------------------------------------------------- captions

def chunk_words(words, max_words=3):
    """Group words into on-screen chunks of up to 3, breaking after punctuation."""
    chunks, current = [], []
    for i, w in enumerate(words):
        current.append(i)
        if len(current) >= max_words or re.search(r"[.!?,;:]$", w["word"]):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _wrap_lines(tokens, font, draw, max_width):
    lines, line = [], []
    for tok in tokens:
        trial = " ".join(t for t, _ in line + [tok])
        if line and draw.textlength(trial, font=font) > max_width:
            lines.append(line)
            line = [tok]
        else:
            line.append(tok)
    if line:
        lines.append(line)
    return lines


def _draw_banner(draw, text):
    font = _font(64)
    tw = draw.textlength(text, font=font)
    while tw > W - 160 and font.size > 36:
        font = _font(font.size - 4)
        tw = draw.textlength(text, font=font)
    pad_x, pad_y, y = 40, 26, 250
    box = [(W - tw) / 2 - pad_x, y - pad_y, (W + tw) / 2 + pad_x, y + font.size + pad_y]
    draw.rounded_rectangle(box, radius=28, fill=(0, 0, 0, 190))
    draw.text(((W - tw) / 2, y), text, font=font, fill=(255, 255, 255, 255))


def render_caption_frame(chunk_text_words, active_idx, hook_text, out_path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if hook_text:
        _draw_banner(draw, hook_text)

    if chunk_text_words:
        font = _font(112)
        tokens = [(w.upper(), i == active_idx) for i, w in enumerate(chunk_text_words)]
        lines = _wrap_lines(tokens, font, draw, W - 140)
        line_h = int(font.size * 1.18)
        y = int(H * 0.52) - (len(lines) * line_h) // 2
        space = draw.textlength(" ", font=font)
        for line in lines:
            total = sum(draw.textlength(t, font=font) for t, _ in line) + space * (len(line) - 1)
            x = (W - total) / 2
            for text, active in line:
                draw.text((x, y), text, font=font,
                          fill=HIGHLIGHT + (255,) if active else (255, 255, 255, 255),
                          stroke_width=10, stroke_fill=(0, 0, 0, 255))
                x += draw.textlength(text, font=font) + space
            y += line_h
    img.save(out_path)


def build_caption_track(words, hook_text, work_dir, total_duration):
    """One transparent PNG per spoken word, listed with durations for ffmpeg's concat demuxer."""
    frames_dir = work_dir / "captions"
    frames_dir.mkdir(exist_ok=True)
    entries = []
    t = 0.0

    def add(path, until):
        nonlocal t
        if until - t > 0.001:
            entries.append((path, until - t))
            t = until

    blank = frames_dir / "blank.png"
    render_caption_frame([], 0, hook_text, blank)

    for c_idx, chunk in enumerate(chunk_words(words)):
        chunk_words_text = [words[i]["word"] for i in chunk]
        for pos, wi in enumerate(chunk):
            path = frames_dir / f"c{c_idx:03d}_{pos}.png"
            render_caption_frame(chunk_words_text, pos, hook_text, path)
            start = max(words[wi]["start"], t)
            add(blank if not entries else entries[-1][0], start)  # hold previous frame until this word
            nxt = words[wi + 1]["start"] if wi + 1 < len(words) else total_duration
            add(path, max(nxt, words[wi]["end"]))
    add(entries[-1][0] if entries else blank, total_duration)

    listing = work_dir / "captions.txt"
    with open(listing, "w") as f:
        for path, dur in entries:
            f.write(f"file '{path}'\nduration {dur:.3f}\n")
        f.write(f"file '{entries[-1][0]}'\n")  # concat demuxer needs the last file repeated
    return listing


# ---------------------------------------------------------------- background

def make_gradient_background(fmt_id, out_path):
    """A tall soft gradient with blurred light blobs; ffmpeg pans over it for motion."""
    c0, c1 = PALETTES.get(fmt_id, [(15, 15, 30), (60, 20, 90)])
    tall = H + 900
    img = Image.new("RGB", (W, tall))
    draw = ImageDraw.Draw(img)
    for y in range(tall):
        k = y / tall
        draw.line([(0, y), (W, y)], fill=tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3)))
    blobs = Image.new("RGB", (W, tall), (0, 0, 0))
    bd = ImageDraw.Draw(blobs)
    rng = random.Random(fmt_id)
    for _ in range(9):
        r = rng.randint(140, 360)
        x, y = rng.randint(0, W), rng.randint(0, tall)
        tint = tuple(min(255, int(v * 1.8) + 30) for v in c1)
        bd.ellipse([x - r, y - r, x + r, y + r], fill=tint)
    blobs = blobs.filter(ImageFilter.GaussianBlur(120))
    img = Image.blend(img, blobs, 0.35)
    img.save(out_path)
    return out_path


def background_input(fmt_id, work_dir, duration):
    """ffmpeg input args and filter for the background (licensed stock clip if present)."""
    clips = [p for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm") for p in STOCK_DIR.glob(ext)]
    if clips:
        clip = random.choice(clips)
        args = ["-stream_loop", "-1", "-i", str(clip)]
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"eq=brightness=-0.12:saturation=1.1,fps={FPS},setsar=1")
        return args, vf
    bg = make_gradient_background(fmt_id, work_dir / "bg.png")
    args = ["-loop", "1", "-framerate", str(FPS), "-i", str(bg)]
    period = max(duration, 12)
    vf = (f"crop={W}:{H}:0:'(ih-{H})*(0.5-0.5*cos(2*PI*t/{period:.2f}))',"
          f"fps={FPS},setsar=1")
    return args, vf


# ---------------------------------------------------------------- assemble

def render_short(short, out_path, voice="onyx", speed=1.12):
    out_path = Path(out_path).resolve()
    work_dir = out_path.parent / (out_path.stem + "_work")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    audio = make_voiceover(short["script"], work_dir / "voice.wav", voice=voice, speed=speed)
    duration = _duration(audio) + 0.6
    if duration > 59:
        raise ValueError(f"Short is {duration:.1f}s; Shorts must stay under 60s.")

    words = word_timings(audio, short["script"])
    captions = build_caption_track(words, short.get("hook_text", ""), work_dir, duration)
    bg_args, bg_vf = background_input(short.get("format", ""), work_dir, duration)

    music = [p for ext in ("*.mp3", "*.m4a", "*.wav") for p in MUSIC_DIR.glob(ext)]
    music_args = ["-stream_loop", "-1", "-i", str(random.choice(music))] if music else []

    filters = [
        f"[0:v]{bg_vf}[bg]",
        "[1:v]format=rgba[cap]",
        "[bg][cap]overlay=0:0:format=auto[v0]",
        # Progress bar along the bottom keeps viewers watching to the end.
        f"[v0]drawbox=x=0:y={H - 18}:w='iw*t/{duration:.3f}':h=18:color=0xFFDD00@0.95:t=fill[v]",
    ]
    if music_args:
        filters.append("[3:a]volume=0.12[m];[2:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]")
        audio_map = "[a]"
    else:
        audio_map = "2:a"

    cmd = ["ffmpeg", "-y", *bg_args,
           "-f", "concat", "-safe", "0", "-i", str(captions),
           "-i", str(audio), *music_args,
           "-filter_complex", ";".join(filters),
           "-map", "[v]", "-map", audio_map,
           "-t", f"{duration:.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
           str(out_path)]
    _run(cmd)
    shutil.rmtree(work_dir, ignore_errors=True)
    print(f"Rendered {out_path} ({duration:.1f}s)")
    return str(out_path), duration


if __name__ == "__main__":
    # Quick local preview: python render_short.py sample.json
    import sys
    data = json.load(open(sys.argv[1]))
    render_short(data, HERE / "output" / "preview.mp4")
