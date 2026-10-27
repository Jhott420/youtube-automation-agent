import base64
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import imageio_ffmpeg

# Provider order mirrors the main app:
#   text:   OpenAI -> Gemini
#   speech: ElevenLabs -> OpenAI -> Gemini -> gTTS (free, no key)
#   images: OpenAI -> Gemini -> none (styled title cards are used instead)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Long scripts are narrated in chunks; providers cap input length per request.
TTS_CHUNK_CHARS = 3000

# Tried in order when the chosen text model is overloaded (same list as the main app).
GEMINI_TEXT_FALLBACKS = ["gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]

# Temporary server-side errors worth retrying. 429 is not retried: on the free tier it
# usually means the quota for that model is used up (or is 0), so waiting doesn't help.
RETRYABLE_STATUS = {500, 502, 503, 504}

# Free-tier quota is counted per model, so a second TTS model doubles the daily allowance.
GEMINI_TTS_FALLBACKS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
RETRY_DELAYS = [10, 30, 90]

# Text models that were overloaded or out of quota earlier in this run.
unavailable_text_models = set()

class HTTPStatusError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status

def env(name, default=None):
    return os.getenv(name) or default

def has_openai():
    return bool(env("OPENAI_API_KEY"))

def has_gemini():
    return bool(env("GEMINI_API_KEY"))

def has_elevenlabs():
    return bool(env("ELEVENLABS_API_KEY") and env("ELEVENLABS_VOICE_ID"))

def openai_client():
    from openai import OpenAI
    return OpenAI(api_key=env("OPENAI_API_KEY"))

def ffmpeg(*args):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True)

def post_json(url, payload, headers, timeout=300):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise HTTPStatusError(error.code, f"HTTP {error.code} from {url.split('?')[0]}: {detail}") from None
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as error:
        # Dropped or timed-out connections are treated like a 503 so they get retried.
        raise HTTPStatusError(503, f"Connection to {url.split('?')[0]} failed: {error}") from None

def post_json_with_retry(url, payload, headers):
    for delay in RETRY_DELAYS + [None]:
        try:
            return post_json(url, payload, headers)
        except HTTPStatusError as error:
            if error.status not in RETRYABLE_STATUS or delay is None:
                raise
            print(f"HTTP {error.status}, retrying in {delay}s...")
            time.sleep(delay)

def gemini_generate(model, payload):
    raw = post_json_with_retry(
        f"{GEMINI_API_BASE}/{model}:generateContent",
        payload,
        {"x-goog-api-key": env("GEMINI_API_KEY")},
    )
    data = json.loads(raw)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data.get('promptFeedback') or data}")
    return candidates[0].get("content", {}).get("parts", [])

# ---------- Text ----------

def generate_text(prompt, system=None, temperature=0.9, max_tokens=2600):
    if has_openai():
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        response = openai_client().chat.completions.create(
            model=env("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    if has_gemini():
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # Thinking models spend output tokens before answering, so leave headroom.
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens * 4, 8192)},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        preferred = env("GEMINI_TEXT_MODEL", GEMINI_TEXT_FALLBACKS[0])
        models = [preferred] + [m for m in GEMINI_TEXT_FALLBACKS if m != preferred]
        # Skip models that already failed this run, but always keep the last one to try.
        models = [m for m in models if m not in unavailable_text_models] or models[-1:]
        for model in models:
            try:
                parts = gemini_generate(model, payload)
            except HTTPStatusError as error:
                if error.status not in RETRYABLE_STATUS | {429} or model == models[-1]:
                    raise
                print(f"{model} unavailable ({error.status}), trying {models[models.index(model) + 1]}")
                unavailable_text_models.add(model)
                continue
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if text.strip():
                return text.strip()
        raise RuntimeError("Gemini returned no text")

    raise RuntimeError("No text provider configured. Set GEMINI_API_KEY or OPENAI_API_KEY.")

# ---------- Speech ----------

def clean_narration(text):
    """Strip screenplay/markdown leftovers so the voice only reads the story."""
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r"[-*_=#\s]*", line):  # blank lines and dividers
            kept.append("")
            continue
        if line.startswith("#"):  # headings
            continue
        if re.fullmatch(r"\*{0,2}[A-Z][A-Z0-9 .'’()/:-]{1,40}\*{0,2}:?", line):  # NARRATOR (V.O.), COLD OPEN
            continue
        if re.match(r"\*{0,2}(title|episode|format|target runtime|runtime)\*{0,2}\s*:", line, re.I):
            continue
        line = re.sub(r"^\*{0,2}[A-Z][A-Z .'’()]{1,30}\*{0,2}:\s*", "", line)  # "NARRATOR: text"
        line = re.sub(r"\[[^\]]*\]", "", line)  # [COLD OPEN], [Music swells]
        line = re.sub(r"^\*?\([^)]*\)\*?$", "", line)  # whole-line (stage direction)
        line = re.sub(r"\((audio|sfx|sound|music|beat|pause|v\.o\.)[^)]*\)", "", line, flags=re.I)
        line = re.sub(r"[*_`]+", "", line).strip()
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

def split_for_tts(text, limit=TTS_CHUNK_CHARS):
    chunks, current = [], ""
    for paragraph in [p.strip() for p in text.split("\n") if p.strip()]:
        while len(paragraph) > limit:
            cut = paragraph.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 0 else limit
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 1 > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks

def elevenlabs_tts(text, out_path):
    audio = post_json_with_retry(
        f"https://api.elevenlabs.io/v1/text-to-speech/{env('ELEVENLABS_VOICE_ID')}",
        {
            "text": text,
            "model_id": env("ELEVENLABS_TTS_MODEL", "eleven_v3"),
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True},
        },
        {"Accept": "audio/mpeg", "xi-api-key": env("ELEVENLABS_API_KEY")},
    )
    Path(out_path).write_bytes(audio)

def openai_tts(text, out_path):
    response = openai_client().audio.speech.create(
        model=env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=env("OPENAI_TTS_VOICE", "coral"),
        input=text,
    )
    Path(out_path).write_bytes(response.content)

def gemini_tts(text, out_path, model=None):
    parts = gemini_generate(model or env("GEMINI_TTS_MODEL", GEMINI_TTS_FALLBACKS[0]), {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": env("GEMINI_TTS_VOICE", "Kore")}}},
        },
    })
    audio = next((p["inlineData"]["data"] for p in parts if p.get("inlineData", {}).get("data")), None)
    if not audio:
        raise RuntimeError("Gemini TTS returned no audio data (usually its safety filter)")
    # Gemini returns raw PCM (24kHz, mono, 16-bit); encode it with the bundled FFmpeg.
    pcm_path = f"{out_path}.pcm"
    Path(pcm_path).write_bytes(base64.b64decode(audio))
    try:
        ffmpeg("-f", "s16le", "-ar", "24000", "-ac", "1", "-i", pcm_path, str(out_path))
    finally:
        Path(pcm_path).unlink(missing_ok=True)

def gtts_tts(text, out_path):
    from gtts import gTTS
    gTTS(text=text, lang="en", slow=False).save(str(out_path))

def speech_providers():
    """Every configured voice, best first. gTTS is always last and needs no key."""
    providers = []
    if has_elevenlabs():
        providers.append(("ElevenLabs", elevenlabs_tts, TTS_CHUNK_CHARS))
    if has_openai():
        providers.append(("OpenAI", openai_tts, TTS_CHUNK_CHARS))
    if has_gemini():
        # Free-tier quota is per model (10 requests/day), so each Gemini TTS model is its own fallback.
        preferred = env("GEMINI_TTS_MODEL", GEMINI_TTS_FALLBACKS[0])
        for model in [preferred] + [m for m in GEMINI_TTS_FALLBACKS if m != preferred]:
            providers.append((f"Gemini {model}", lambda t, o, m=model: gemini_tts(t, o, m), TTS_CHUNK_CHARS))
    # gTTS splits text internally, so it can take the whole script at once.
    providers.append(("gTTS", gtts_tts, None))
    return providers

def generate_speech(text, out_path):
    text = clean_narration(text)
    for name, synthesize, limit in speech_providers():
        chunks = split_for_tts(text, limit) if limit else [text]
        print(f"Narrating with {name} ({len(chunks)} part(s))")
        try:
            narrate_parts(name, synthesize, chunks, out_path)
            return name
        except HTTPStatusError as error:
            if error.status not in (401, 402, 403, 429):
                raise
            # Out of quota or credits: redo the whole episode with the next voice so it stays consistent.
            print(f"{name} unavailable (HTTP {error.status}), switching to the next voice")
    raise RuntimeError("No voice provider could narrate the episode")

def narrate_parts(name, synthesize, chunks, out_path):
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, chunk in enumerate(chunks):
            parts.extend(synthesize_part(name, synthesize, chunk, Path(tmp), f"{i:03d}"))
        playlist = Path(tmp) / "parts.txt"
        playlist.write_text("".join(f"file '{p}'\n" for p in parts))
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(playlist), "-c:a", "libmp3lame", "-q:a", "2", str(out_path))

def synthesize_part(name, synthesize, text, tmp, label, attempts=2):
    """Narrate one part, falling back so one rejected passage can't sink the episode.

    Retries, then splits the passage in half (Gemini's safety filter sometimes rejects
    a whole passage over one line), then uses gTTS for whatever still fails.
    Quota/auth errors are raised straight away so the caller can switch voices.
    Returns WAV files at a common format so parts from different voices join cleanly.
    """
    for attempt in range(attempts):
        try:
            raw = tmp / f"part_{label}.mp3"
            synthesize(text, raw)
            return [to_wav(raw)]
        except HTTPStatusError as error:
            if error.status in (401, 402, 403, 429):
                raise
            print(f"{name} could not narrate part {label} (attempt {attempt + 1}): {str(error)[:200]}")
        except Exception as error:
            print(f"{name} could not narrate part {label} (attempt {attempt + 1}): {str(error)[:200]}")

    if len(text) > 600:
        middle = text.rfind(". ", 0, len(text) // 2 + 200)
        middle = middle + 1 if middle > 0 else len(text) // 2
        return (synthesize_part(name, synthesize, text[:middle].strip(), tmp, f"{label}a", 1)
                + synthesize_part(name, synthesize, text[middle:].strip(), tmp, f"{label}b", 1))

    print(f"Using gTTS for part {label}")
    raw = tmp / f"part_{label}_gtts.mp3"
    gtts_tts(text, raw)
    return [to_wav(raw)]

def to_wav(path):
    wav = path.with_suffix(".wav")
    ffmpeg("-i", str(path), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(wav))
    return wav

# ---------- Images ----------

def openai_image(prompt, out_path):
    response = openai_client().images.generate(
        model=env("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        prompt=prompt,
        n=1,
        size="1536x1024",
        quality="high",
    )
    item = response.data[0]
    if item.b64_json:
        Path(out_path).write_bytes(base64.b64decode(item.b64_json))
    else:
        urllib.request.urlretrieve(item.url, out_path)

def gemini_image(prompt, out_path):
    parts = gemini_generate(env("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"), {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9", "imageSize": "1K"},
        },
    })
    images = [
        p for p in parts
        if p.get("inlineData", {}).get("data")
        and p["inlineData"].get("mimeType", "image/").startswith("image/")
    ]
    rendered = [p for p in images if not p.get("thought")]
    chosen = (rendered or images)[-1:] if images else []
    if not chosen:
        raise RuntimeError("Gemini image generation returned no image data")
    Path(out_path).write_bytes(base64.b64decode(chosen[0]["inlineData"]["data"]))

def image_provider():
    if has_openai():
        return "OpenAI", openai_image
    if has_gemini():
        return "Gemini", gemini_image
    return None, None

def generate_image(prompt, out_path):
    """Returns the image path, or None if no provider is set up or generation fails."""
    from PIL import Image

    name, render = image_provider()
    if not render:
        return None
    try:
        render(prompt, out_path)
        with Image.open(out_path) as image:
            image.verify()
        return str(out_path)
    except Exception as error:
        # Image generation is optional (e.g. Gemini images need a paid tier), so fall back quietly.
        print(f"{name} image generation failed, using title cards instead: {error}")
        Path(out_path).unlink(missing_ok=True)
        return None
