"""Writes Shorts scripts built around the patterns that drive Shorts retention:
a hook in the first second, no intro, one idea, a payoff saved for the end,
and a last line that loops back into the first.

Two styles:
  narrator - one voice reads the script
  duo      - fast brainrot-style banter between the two original characters
             defined in shorts_config.json
"""

import json
import os

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

RULES = """Rules you never break:
- The first line is the hook. Under 12 words. It must open a curiosity gap,
  state something shocking, or drop the viewer mid-action. Never start with
  "Did you know", "Hey guys", "In this video", "Today", or a greeting.
- No intro, no channel name, no "like and subscribe" anywhere.
- One idea only. Every line must raise tension, land a joke, or deliver information.
- Short spoken sentences. Plain words. Written for the ear, not the eye.
- Save the biggest payoff for the final line.
- The final line should flow naturally back into the first line so the video
  loops seamlessly when it replays.
- Never invent facts. If something is presented as true, it must be accurate.
- Nothing hateful, sexual, gory, or targeting a real person.
- Never reference, imitate or quote existing TV shows, cartoons, films or their
  characters or catchphrases. All characters and jokes are original."""

NARRATOR_SYSTEM = "You write YouTube Shorts scripts that people watch to the end and replay.\n\n" + RULES

DUO_SYSTEM = """You write chaotic, fast, funny 'brainrot' YouTube Shorts performed as a
rapid-fire dialogue between two original characters:

{cast}

Style: hyper-fast back-and-forth, constant interruptions, absurd escalation,
deadpan one-liners, sudden tangents that snap back to the point. Each line is
one short burst (max 16 words). Characters stay fully in personality.

""" + RULES

NARRATOR_SCHEMA = """Return JSON with exactly these keys:
- "topic": 3-8 word summary of the subject (used to avoid repeats)
- "title": YouTube title, under 60 characters, curiosity-driven, no clickbait lies, no hashtags, no emoji
- "hook_text": 2-6 word on-screen banner shown for the whole video, ALL CAPS
- "script": the narration, plain text only, no stage directions, no emoji, no hashtags
- "description": 1-2 sentence YouTube description that adds context without spoiling the ending
- "pinned_comment": a short question that invites viewers to comment"""

DUO_SCHEMA = """Return JSON with exactly these keys:
- "topic": 3-8 word summary of the subject (used to avoid repeats)
- "title": YouTube title, under 60 characters, curiosity-driven, no clickbait lies, no hashtags, no emoji
- "hook_text": 2-6 word on-screen banner shown for the whole video, ALL CAPS
- "lines": array of 8-14 objects {{"speaker": one of {names}, "text": "..."}}; plain spoken text, no stage directions, no emoji
- "description": 1-2 sentence YouTube description that adds context without spoiling the ending
- "pinned_comment": a short question that invites viewers to comment"""

USER_TEMPLATE = """Write one YouTube Short.

Format: {label}
Brief: {brief}
Length: {min_words}-{max_words} spoken words in total (about 30-45 seconds).

Do NOT repeat or closely resemble any of these previous topics:
{previous}

{schema}
"""


def _client():
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _cast_text(characters):
    return "\n".join(f"- {name}: {c['personality']}" for name, c in characters.items())


def write_short(fmt, previous_topics, word_range, characters=None):
    duo = bool(characters)
    previous = "\n".join(f"- {t}" for t in previous_topics[-60:]) or "- (none yet)"
    names = list(characters) if duo else []
    schema = DUO_SCHEMA.format(names=" or ".join(f'"{n}"' for n in names)) if duo else NARRATOR_SCHEMA
    brief = fmt.get("duo_brief", fmt["brief"]) if duo else fmt["brief"]
    prompt = USER_TEMPLATE.format(
        label=fmt["label"], brief=brief, min_words=word_range[0], max_words=word_range[1],
        previous=previous, schema=schema,
    )
    system = DUO_SYSTEM.format(cast=_cast_text(characters)) if duo else NARRATOR_SYSTEM

    response = _client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.95,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)

    if duo:
        lines = []
        for line in data.get("lines") or []:
            speaker = str(line.get("speaker", "")).strip().upper()
            text = " ".join(str(line.get("text", "")).split())
            if text:
                lines.append({"speaker": speaker if speaker in characters else names[len(lines) % 2],
                              "text": text})
        if len(lines) < 4:
            raise ValueError(f"Dialogue for {fmt['id']} has too few lines")
        data["lines"] = lines
        data["script"] = " ".join(line["text"] for line in lines)

    for key in ("topic", "title", "hook_text", "script", "description"):
        if not str(data.get(key, "")).strip():
            raise ValueError(f"Script for {fmt['id']} is missing '{key}'")

    data["script"] = " ".join(data["script"].split())
    data["hook_text"] = data["hook_text"].strip().upper()[:40]
    data["format"] = fmt["id"]
    return data
