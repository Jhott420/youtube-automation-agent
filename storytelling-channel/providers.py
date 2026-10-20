import base64
import json
import os
import subprocess
import tempfile
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
        raise RuntimeError(f"HTTP {error.code} from {url.split('?')[0]}: {detail}") from None

def gemini_generate(model, payload):
    raw = post_json(
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
        parts = gemini_generate(env("GEMINI_TEXT_MODEL", "gemini-3.7-flash"), payload)
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise RuntimeError("Gemini returned no text")
        return text.strip()

    raise RuntimeError("No text provider configured. Set GEMINI_API_KEY or OPENAI_API_KEY.")

# ---------- Speech ----------

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
    audio = post_json(
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

def gemini_tts(text, out_path):
    parts = gemini_generate(env("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"), {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": env("GEMINI_TTS_VOICE", "Kore")}}},
        },
    })
    audio = next((p["inlineData"]["data"] for p in parts if p.get("inlineData", {}).get("data")), None)
    if not audio:
        raise RuntimeError("Gemini TTS returned no audio data")
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

def speech_provider():
    if has_elevenlabs():
        return "ElevenLabs", elevenlabs_tts, TTS_CHUNK_CHARS
    if has_openai():
        return "OpenAI", openai_tts, TTS_CHUNK_CHARS
    if has_gemini():
        return "Gemini", gemini_tts, TTS_CHUNK_CHARS
    # gTTS splits text internally, so it can take the whole script at once.
    return "gTTS", gtts_tts, None

def generate_speech(text, out_path):
    name, synthesize, limit = speech_provider()
    chunks = split_for_tts(text, limit) if limit else [text]
    print(f"Narrating with {name} ({len(chunks)} part(s))")

    if len(chunks) == 1:
        synthesize(chunks[0], out_path)
        return name

    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, chunk in enumerate(chunks):
            part = Path(tmp) / f"part_{i:03d}.mp3"
            synthesize(chunk, part)
            parts.append(part)
        playlist = Path(tmp) / "parts.txt"
        playlist.write_text("".join(f"file '{p}'\n" for p in parts))
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(playlist), "-c:a", "libmp3lame", "-q:a", "2", str(out_path))
    return name

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
