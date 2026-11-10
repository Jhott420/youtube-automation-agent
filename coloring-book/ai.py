"""Minimal text and image calls for the coloring book generator.

Provider order matches the storytelling channel: OpenAI first, then Gemini.
Free-tier Gemini keys cannot generate images (their image quota is 0), so real
artwork needs either Gemini billing turned on or an OpenAI key.
"""
import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_TEXT_FALLBACKS = ["gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]
RETRYABLE_STATUS = {500, 502, 503, 504}
RETRY_DELAYS = [10, 30, 90]


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


def has_text_provider():
    return has_openai() or has_gemini()


def has_image_provider():
    return has_openai() or has_gemini()


def _post_json(url, payload, headers, timeout=300):
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
        raise HTTPStatusError(error.code, f"HTTP {error.code}: {detail}") from None
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as error:
        raise HTTPStatusError(503, f"Connection failed: {error}") from None


def _gemini(model, payload):
    for delay in RETRY_DELAYS + [None]:
        try:
            raw = _post_json(
                f"{GEMINI_API_BASE}/{model}:generateContent",
                payload,
                {"x-goog-api-key": env("GEMINI_API_KEY")},
            )
            break
        except HTTPStatusError as error:
            if error.status not in RETRYABLE_STATUS or delay is None:
                raise
            print(f"Gemini busy ({error.status}), retrying in {delay}s...")
            time.sleep(delay)
    data = json.loads(raw)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data.get('promptFeedback') or data}")
    return candidates[0].get("content", {}).get("parts", [])


def generate_text(prompt, temperature=0.9):
    if has_openai():
        from openai import OpenAI
        response = OpenAI(api_key=env("OPENAI_API_KEY")).chat.completions.create(
            model=env("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()

    if has_gemini():
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 8192},
        }
        preferred = env("GEMINI_TEXT_MODEL", GEMINI_TEXT_FALLBACKS[0])
        models = [preferred] + [m for m in GEMINI_TEXT_FALLBACKS if m != preferred]
        for model in models:
            try:
                parts = _gemini(model, payload)
            except HTTPStatusError as error:
                if error.status not in RETRYABLE_STATUS | {429} or model == models[-1]:
                    raise
                print(f"{model} unavailable ({error.status}), trying the next model")
                continue
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if text.strip():
                return text.strip()
        raise RuntimeError("Gemini returned no text")

    raise RuntimeError("No text provider: set OPENAI_API_KEY or GEMINI_API_KEY")


def generate_image(prompt, out_path):
    """Generates one portrait (3:4) image. Raises on failure."""
    out_path = Path(out_path)
    if has_openai():
        from openai import OpenAI
        response = OpenAI(api_key=env("OPENAI_API_KEY")).images.generate(
            model=env("OPENAI_IMAGE_MODEL", "gpt-image-2"),
            prompt=prompt,
            n=1,
            size="1024x1536",
            quality=env("OPENAI_IMAGE_QUALITY", "high"),
        )
        item = response.data[0]
        if item.b64_json:
            out_path.write_bytes(base64.b64decode(item.b64_json))
        else:
            urllib.request.urlretrieve(item.url, out_path)
        return out_path

    if has_gemini():
        parts = _gemini(env("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"), {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "3:4", "imageSize": "2K"},
            },
        })
        images = [
            p for p in parts
            if p.get("inlineData", {}).get("data")
            and p["inlineData"].get("mimeType", "image/").startswith("image/")
        ]
        rendered = [p for p in images if not p.get("thought")] or images
        if not rendered:
            raise RuntimeError("Gemini returned no image data")
        out_path.write_bytes(base64.b64decode(rendered[-1]["inlineData"]["data"]))
        return out_path

    raise RuntimeError("No image provider: set OPENAI_API_KEY or GEMINI_API_KEY (with billing on)")
