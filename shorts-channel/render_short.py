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
    "absurd-hypothetical": [(10, 40, 20), (120, 200, 30)],
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

def _tts(text, raw_path, voice):
    """OpenAI TTS (natural voice) with gTTS as a fallback. Returns True if gTTS was used."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with client.audio.speech.with_streaming_response.create(
            model=os.getenv("OPENAI_TTS_MODEL", "tts-1-hd"), voice=voice, input=text
        ) as response:
            response.stream_to_file(str(raw_path))
        return False
    except Exception as exc:  # noqa: BLE001 - any TTS failure falls back to gTTS
        print(f"OpenAI TTS unavailable ({exc}); falling back to gTTS.")
        from gtts import gTTS
        gTTS(text=text, lang="en").save(str(raw_path))
        return True


def _voice_filter(speed, pitch=1.0, normalize=True):
    """Speed up and optionally pitch-shift a voice (pitch > 1 = higher, cartoonier)."""
    parts = ["aresample=44100"]
    if abs(pitch - 1.0) > 0.01:
        parts += [f"asetrate={int(44100 * pitch)}", "aresample=44100"]
    tempo = speed / pitch
    while tempo > 2.0:  # atempo only accepts 0.5-2.0 per stage
        parts.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    parts.append(f"atempo={tempo:.4f}")
    if normalize:
        parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    return ",".join(parts)


def make_voiceover(script, out_path, voice="onyx", speed=1.12):
    """Single narrator, sped up a touch because faster narration holds attention better."""
    raw = out_path.with_suffix(".raw.mp3")
    if _tts(script, raw, voice):
        speed = max(speed, 1.25)  # gTTS is slow and flat; push it harder
    _run(["ffmpeg", "-y", "-i", str(raw), "-filter:a", _voice_filter(speed), str(out_path)])
    raw.unlink(missing_ok=True)
    return out_path


def make_dialogue(lines, characters, work_dir, gap=0.06):
    """Voice each line with its character's voice, stitch them together, and return
    (audio_path, words) with per-word timings and speakers."""
    parts, words, t = [], [], 0.0
    for i, line in enumerate(lines):
        char = characters.get(line["speaker"], {})
        raw = work_dir / f"line{i:02d}.raw.mp3"
        clip = work_dir / f"line{i:02d}.wav"
        used_gtts = _tts(line["text"], raw, char.get("voice", "onyx"))
        speed = max(char.get("speed", 1.15), 1.25) if used_gtts else char.get("speed", 1.15)
        _run(["ffmpeg", "-y", "-i", str(raw), "-filter:a",
              _voice_filter(speed, char.get("pitch", 1.0), normalize=False) + ",silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
              "-ac", "1", str(clip)])
        dur = _duration(clip)
        for w in _estimate_timings(line["text"], dur):
            words.append({"word": w["word"], "start": t + w["start"], "end": t + w["end"],
                          "speaker": line["speaker"]})
        parts.append(clip)
        t += dur + gap

    listing = work_dir / "lines.txt"
    with open(listing, "w") as f:
        for i, p in enumerate(parts):
            f.write(f"file '{p}'\n")
            if i < len(parts) - 1:
                f.write(f"file '{work_dir / 'gap.wav'}'\n")
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono", "-t", str(gap),
          str(work_dir / "gap.wav")])
    out = work_dir / "dialogue.wav"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-filter:a", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", str(out)])
    return out, words


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
    """Group words into on-screen chunks, breaking after punctuation and on speaker change."""
    chunks, current = [], []
    for i, w in enumerate(words):
        current.append(i)
        next_speaker = words[i + 1].get("speaker") if i + 1 < len(words) else None
        if (len(current) >= max_words or re.search(r"[.!?,;:]$", w["word"])
                or next_speaker != w.get("speaker")):
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


# ---------------------------------------------------------------- characters
# Original characters, drawn from simple shapes.

def _draw_toaster(draw, cx, cy, s, talking):
    body = [cx - s, cy - s * 0.7, cx + s, cy + s * 0.8]
    draw.rounded_rectangle(body, radius=int(s * 0.35), fill=(200, 205, 215), outline=(40, 40, 50), width=10)
    for dx in (-0.45, 0.15):  # bread slots with toast sticking out
        draw.rounded_rectangle([cx + s * dx, cy - s * 1.05, cx + s * (dx + 0.32), cy - s * 0.55],
                               radius=18, fill=(214, 150, 70), outline=(110, 60, 20), width=8)
    draw.rectangle([cx - s, cy - s * 0.62, cx + s, cy - s * 0.5], fill=(150, 155, 165))
    for ex in (-0.38, 0.38):  # half-lidded smug eyes
        x0, y0 = cx + s * ex - s * 0.17, cy - s * 0.15
        draw.ellipse([x0, y0, x0 + s * 0.34, y0 + s * 0.3], fill=(255, 255, 255), outline=(30, 30, 30), width=6)
        draw.ellipse([x0 + s * 0.1, y0 + s * 0.1, x0 + s * 0.24, y0 + s * 0.26], fill=(20, 20, 20))
        draw.rectangle([x0 - 4, y0 - 4, x0 + s * 0.34 + 4, y0 + s * 0.13], fill=(200, 205, 215))
        draw.line([x0, y0 + s * 0.13, x0 + s * 0.34, y0 + s * 0.13], fill=(30, 30, 30), width=7)
    if talking:
        draw.ellipse([cx - s * 0.2, cy + s * 0.28, cx + s * 0.2, cy + s * 0.55], fill=(60, 20, 20))
    else:
        draw.line([cx - s * 0.22, cy + s * 0.4, cx + s * 0.22, cy + s * 0.36], fill=(30, 30, 30), width=9)
    draw.rectangle([cx + s * 0.85, cy - s * 0.1, cx + s * 1.1, cy + s * 0.05], fill=(60, 60, 70))  # lever


def _draw_pigeon(draw, cx, cy, s, talking):
    draw.ellipse([cx - s * 0.9, cy - s * 0.9, cx + s * 0.9, cy + s * 0.9],
                 fill=(130, 135, 160), outline=(40, 40, 60), width=10)
    draw.chord([cx - s * 0.75, cy + s * 0.2, cx + s * 0.75, cy + s * 1.0], 180, 360, fill=(90, 160, 140))
    for ex in (-0.35, 0.35):  # huge wide eyes
        x, y = cx + s * ex, cy - s * 0.2
        draw.ellipse([x - s * 0.26, y - s * 0.26, x + s * 0.26, y + s * 0.26],
                     fill=(255, 140, 0), outline=(30, 30, 30), width=6)
        draw.ellipse([x - s * 0.12, y - s * 0.12, x + s * 0.12, y + s * 0.12], fill=(10, 10, 10))
        draw.ellipse([x - s * 0.06, y - s * 0.1, x, y - s * 0.04], fill=(255, 255, 255))
    top = cy + s * 0.05
    draw.polygon([(cx - s * 0.18, top), (cx + s * 0.18, top), (cx, top + s * 0.28)],
                 fill=(240, 170, 60), outline=(90, 50, 10))
    if talking:
        draw.polygon([(cx - s * 0.15, top + s * 0.3), (cx + s * 0.15, top + s * 0.3), (cx, top + s * 0.48)],
                     fill=(240, 170, 60), outline=(90, 50, 10))
    for fx in (-0.15, 0.0, 0.15):  # messy head feathers
        draw.line([cx + s * fx, cy - s * 0.85, cx + s * (fx * 2), cy - s * 1.15], fill=(40, 40, 60), width=9)


AVATARS = {"toaster": _draw_toaster, "pigeon": _draw_pigeon}


def _draw_speaker(draw, speaker, char, talking):
    s = 190
    cx, cy = W // 2, int(H * 0.77)
    painter = AVATARS.get(char.get("avatar"))
    if painter:
        painter(draw, cx, cy, s, talking)
    else:
        draw.ellipse([cx - s, cy - s, cx + s, cy + s], fill=tuple(char.get("color", (200, 200, 200))))
    font = _font(64)
    tw = draw.textlength(speaker, font=font)
    y = cy - s * 1.25 - 90
    color = tuple(char.get("color", (255, 255, 255)))
    draw.rounded_rectangle([(W - tw) / 2 - 30, y - 14, (W + tw) / 2 + 30, y + 80], radius=24, fill=color + (255,))
    draw.text(((W - tw) / 2, y), speaker, font=font, fill=(0, 0, 0, 255))


def render_caption_frame(chunk_text_words, active_idx, hook_text, out_path, speaker=None, char=None,
                         talking=False):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if hook_text:
        _draw_banner(draw, hook_text)
    highlight = HIGHLIGHT
    if speaker and char:
        _draw_speaker(draw, speaker, char, talking)
        highlight = tuple(char.get("color", HIGHLIGHT))

    if chunk_text_words:
        font = _font(112)
        tokens = [(w.upper(), i == active_idx) for i, w in enumerate(chunk_text_words)]
        lines = _wrap_lines(tokens, font, draw, W - 140)
        line_h = int(font.size * 1.18)
        y = int(H * (0.42 if speaker else 0.52)) - (len(lines) * line_h) // 2
        space = draw.textlength(" ", font=font)
        for line in lines:
            total = sum(draw.textlength(t, font=font) for t, _ in line) + space * (len(line) - 1)
            x = (W - total) / 2
            for text, active in line:
                draw.text((x, y), text, font=font,
                          fill=highlight + (255,) if active else (255, 255, 255, 255),
                          stroke_width=10, stroke_fill=(0, 0, 0, 255))
                x += draw.textlength(text, font=font) + space
            y += line_h
    img.save(out_path)


def build_caption_track(words, hook_text, work_dir, total_duration, characters=None):
    """One transparent PNG per spoken word, listed with durations for ffmpeg's concat demuxer."""
    characters = characters or {}
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
    first = words[0].get("speaker") if words else None
    render_caption_frame([], 0, hook_text, blank, first, characters.get(first))

    max_words = 2 if characters else 3  # duo style: faster, punchier captions
    for c_idx, chunk in enumerate(chunk_words(words, max_words)):
        chunk_words_text = [words[i]["word"] for i in chunk]
        speaker = words[chunk[0]].get("speaker")
        for pos, wi in enumerate(chunk):
            path = frames_dir / f"c{c_idx:03d}_{pos}.png"
            render_caption_frame(chunk_words_text, pos, hook_text, path, speaker,
                                 characters.get(speaker), talking=wi % 2 == 0)
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
    clips = [p for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm") for p in STOCK_DIR.rglob(ext)]
    if clips:
        clip = random.choice(clips)
        # Start at a random point so one long gameplay recording gives many different backgrounds.
        try:
            length = _duration(clip)
        except (RuntimeError, ValueError):
            length = 0
        start = random.uniform(0, length - duration - 1) if length > duration + 5 else 0
        args = ["-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(clip)]
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

def render_short(short, out_path, voice="onyx", speed=1.12, characters=None):
    out_path = Path(out_path).resolve()
    work_dir = out_path.parent / (out_path.stem + "_work")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    duo = bool(characters) and bool(short.get("lines"))
    if duo:
        audio, words = make_dialogue(short["lines"], characters, work_dir)
    else:
        audio = make_voiceover(short["script"], work_dir / "voice.wav", voice=voice, speed=speed)
    duration = _duration(audio) + 0.6
    if duration > 59:
        raise ValueError(f"Short is {duration:.1f}s; Shorts must stay under 60s.")

    if not duo:
        words = word_timings(audio, short["script"])
    captions = build_caption_track(words, short.get("hook_text", ""), work_dir, duration,
                                   characters if duo else None)
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
