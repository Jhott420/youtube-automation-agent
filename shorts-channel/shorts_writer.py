"""Writes Shorts scripts built around the patterns that drive Shorts retention:
a hook in the first second, no intro, one idea, a payoff saved for the end,
and a last line that loops back into the first."""

import json
import os

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """You write YouTube Shorts scripts that people watch to the end and replay.

Rules you never break:
- The first sentence is the hook. Under 12 words. It must open a curiosity gap,
  state something shocking, or drop the viewer mid-action. Never start with
  "Did you know", "Hey guys", "In this video", "Today", or a greeting.
- No intro, no channel name, no "like and subscribe" anywhere in the script.
- One idea only. Every sentence must raise tension or deliver information.
- Short spoken sentences. Plain words. Written for the ear, not the eye.
- Save the biggest payoff for the final sentence.
- The final sentence should flow naturally back into the first sentence so the
  video loops seamlessly when it replays.
- Never invent facts. If something is presented as true, it must be accurate.
- Nothing hateful, sexual, gory, or targeting a real private person."""

USER_TEMPLATE = """Write one YouTube Short.

Format: {label}
Brief: {brief}
Length: {min_words}-{max_words} spoken words (about 30-45 seconds).

Do NOT repeat or closely resemble any of these previous topics:
{previous}

Return JSON with exactly these keys:
- "topic": 3-8 word summary of the subject (used to avoid repeats)
- "title": YouTube title, under 60 characters, curiosity-driven, no clickbait lies, no hashtags, no emoji
- "hook_text": 2-6 word on-screen banner shown for the whole video, ALL CAPS
- "script": the narration, plain text only, no stage directions, no emoji, no hashtags
- "description": 1-2 sentence YouTube description that adds context without spoiling the ending
- "pinned_comment": a short question that invites viewers to comment
"""


def _client():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def write_short(fmt, previous_topics, word_range):
    previous = "\n".join(f"- {t}" for t in previous_topics[-60:]) or "- (none yet)"
    prompt = USER_TEMPLATE.format(
        label=fmt["label"],
        brief=fmt["brief"],
        min_words=word_range[0],
        max_words=word_range[1],
        previous=previous,
    )
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.95,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)

    for key in ("topic", "title", "hook_text", "script", "description"):
        if not str(data.get(key, "")).strip():
            raise ValueError(f"Script for {fmt['id']} is missing '{key}'")

    data["script"] = " ".join(data["script"].split())
    data["hook_text"] = data["hook_text"].strip().upper()[:40]
    data["format"] = fmt["id"]
    return data
