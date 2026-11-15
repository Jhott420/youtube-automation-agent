"""Assembles each Claude request from: persona + disclosure + safety rules +
retrieved memories + recent conversation history.

Layout (order matters for prompt caching -- stable content first):

  system[0]  persona description + AI disclosure + safety rules   (cache_control)
  system[1]  retrieved memories about this user                   (changes per turn)
  messages   recent conversation history, ending with the new user message
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Message
from .memory import RetrievedMemory
from .persona import Persona
from .safety import SAFETY_RULES


@dataclass
class PromptBundle:
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def system_text(self) -> str:
        """All system blocks joined -- handy for tests and debugging."""
        return "\n\n".join(block["text"] for block in self.system)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_persona(persona: Persona) -> str:
    parts = [
        f"You are {persona.name}, an AI companion character. "
        f"Your character is {persona.age} years old.",
        f"## AI disclosure (always true, always honour it)\n{persona.disclosure}",
    ]
    if persona.backstory:
        parts.append(f"## Character backstory (fictional)\n{persona.backstory.strip()}")
    if persona.personality_traits:
        parts.append(f"## Personality\n{_bullets(persona.personality_traits)}")
    if persona.speech_style:
        parts.append(f"## How you talk\n{persona.speech_style.strip()}")
    if persona.emoji_habits:
        parts.append(f"## Emoji habits\n{persona.emoji_habits.strip()}")
    if persona.relationship_dynamic:
        parts.append(f"## Relationship with the user\n{persona.relationship_dynamic.strip()}")
    if persona.interests:
        parts.append(f"## Interests\n{_bullets(persona.interests)}")
    if persona.hard_limits:
        parts.append(f"## Hard limits (never do these)\n{_bullets(persona.hard_limits)}")
    parts.append(
        "## Style\nReply like a text message in a chat app: usually 1-4 short sentences, "
        "no headings or bullet lists unless the user asks for them."
    )
    return "\n\n".join(parts)


def render_memories(memories: list[RetrievedMemory], display_name: str | None = None) -> str:
    lines = ["## What you remember about this user"]
    if display_name:
        lines.append(f"Their display name is {display_name}.")
    if memories:
        lines.append("Use these naturally when relevant; don't recite them:")
        lines.extend(f"- {m.content}" for m in memories)
    else:
        lines.append("Nothing relevant stored yet.")
    return "\n".join(lines)


def history_to_messages(history: list[Message], new_user_text: str) -> list[dict[str, Any]]:
    """Convert stored turns to Messages API format.

    Guarantees: starts with a user turn, ends with the new user message, and
    merges consecutive same-role turns (e.g. a user message that got no reply
    because of a rate limit or an API error).
    """
    msgs: list[dict[str, Any]] = []
    for m in history:
        if not msgs and m.role != "user":
            continue  # the API requires the first message to be from the user
        if msgs and msgs[-1]["role"] == m.role:
            msgs[-1]["content"] += "\n\n" + m.content
        else:
            msgs.append({"role": m.role, "content": m.content})

    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + new_user_text
    else:
        msgs.append({"role": "user", "content": new_user_text})
    return msgs


def build_prompt(
    persona: Persona,
    memories: list[RetrievedMemory],
    history: list[Message],
    user_text: str,
    display_name: str | None = None,
) -> PromptBundle:
    static = f"{render_persona(persona)}\n\n{SAFETY_RULES}"
    dynamic = render_memories(memories, display_name)
    return PromptBundle(
        system=[
            {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic},
        ],
        messages=history_to_messages(history, user_text),
    )
