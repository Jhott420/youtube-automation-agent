"""Persona loading / hot reload and system-prompt assembly."""

from __future__ import annotations

import os
import time

import pytest
import yaml

from companion.db import Message
from companion.memory import RetrievedMemory
from companion.persona import Persona, PersonaError, PersonaManager, load_persona_file
from companion.prompt_builder import build_prompt, history_to_messages
from companion.safety import SAFETY_RULES


def msg(role, content, i=0):
    return Message(i, "u1", role, content, "t", False, None)


@pytest.fixture
def persona(persona_path) -> Persona:
    return load_persona_file(persona_path)


# ---- persona validation ----------------------------------------------------------


def test_seed_persona_is_valid(persona):
    assert persona.name == "Mira" and persona.age >= 18
    assert "AI" in persona.disclosure


def _write(path, **overrides):
    data = yaml.safe_load(path.read_text())
    data.update(overrides)
    path.write_text(yaml.safe_dump(data))


def test_persona_requires_ai_disclosure(persona_path):
    _write(persona_path, disclosure="Hi, I'm Mira!")
    with pytest.raises(PersonaError, match="disclosure"):
        load_persona_file(persona_path)


def test_persona_must_be_adult(persona_path):
    _write(persona_path, age=17)
    with pytest.raises(PersonaError, match="18"):
        load_persona_file(persona_path)


def test_persona_missing_file(tmp_path):
    with pytest.raises(PersonaError, match="not found"):
        load_persona_file(tmp_path / "nope.yaml")


# ---- hot reload ------------------------------------------------------------------


def _bump_mtime(path):
    t = time.time() + 5
    os.utime(path, (t, t))


def test_hot_reload_picks_up_changes(persona_path):
    mgr = PersonaManager(persona_path)
    assert mgr.get().name == "Mira"
    _write(persona_path, name="Juno", disclosure="I'm Juno, an AI companion.")
    _bump_mtime(persona_path)
    assert mgr.get().name == "Juno"


def test_hot_reload_keeps_last_good_persona_on_bad_edit(persona_path):
    mgr = PersonaManager(persona_path)
    persona_path.write_text("name: [unclosed")
    _bump_mtime(persona_path)
    assert mgr.get().name == "Mira"
    assert mgr.last_error and "YAML" in mgr.last_error
    # Removing the disclosure is also rejected; the old (disclosing) persona stays.
    persona_path.write_text(yaml.safe_dump({"name": "X", "age": 30, "disclosure": "hello"}))
    _bump_mtime(persona_path)
    assert "AI" in mgr.get().disclosure


# ---- prompt assembly ---------------------------------------------------------------


def test_prompt_contains_persona_disclosure_and_safety(persona):
    b = build_prompt(persona, [], [], "hi")
    text = b.system_text
    assert persona.disclosure in text
    assert SAFETY_RULES in text
    assert "Mira" in text and persona.speech_style.strip()[:30] in text
    for limit in persona.hard_limits:
        assert limit in text
    for trait in persona.personality_traits:
        assert trait in text


def test_disclosure_present_even_with_minimal_persona():
    p = Persona(name="Bo", age=30, disclosure="I'm Bo, an AI.")
    assert "I'm Bo, an AI." in build_prompt(p, [], [], "hi").system_text


def test_safety_rules_come_after_persona(persona):
    text = build_prompt(persona, [], [], "hi").system_text
    assert text.index(persona.backstory.strip()[:20]) < text.index("NON-NEGOTIABLE")


def test_memories_go_in_separate_uncached_block(persona):
    mems = [RetrievedMemory(1, "User's dog is named Max", 0.9), RetrievedMemory(2, "User likes tea", 0.5)]
    b = build_prompt(persona, mems, [], "hi", display_name="Sam")
    static, dynamic = b.system
    assert static["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in dynamic
    assert "dog is named Max" in dynamic["text"] and "likes tea" in dynamic["text"]
    assert "Sam" in dynamic["text"]
    assert "Max" not in static["text"]  # static block stays identical across users/turns


def test_static_block_is_stable_across_turns(persona):
    a = build_prompt(persona, [RetrievedMemory(1, "x", 1)], [msg("user", "a")], "one")
    b = build_prompt(persona, [], [], "two")
    assert a.system[0] == b.system[0]


def test_no_memories_placeholder(persona):
    assert "Nothing relevant stored yet" in build_prompt(persona, [], [], "hi").system[1]["text"]


def test_extra_instruction_is_appended(persona):
    b = build_prompt(persona, [], [], "hi", extra_instruction="Don't guilt-trip.")
    assert "Don't guilt-trip." in b.system[1]["text"]


# ---- history -> messages ---------------------------------------------------------


def test_history_ordering_and_new_message_last(persona):
    hist = [msg("user", "hi", 1), msg("assistant", "hey!", 2), msg("user", "how are you", 3), msg("assistant", "good", 4)]
    b = build_prompt(persona, [], hist, "cool")
    assert [m["role"] for m in b.messages] == ["user", "assistant", "user", "assistant", "user"]
    assert b.messages[-1]["content"] == "cool"


def test_history_drops_leading_assistant_turns():
    out = history_to_messages([msg("assistant", "orphan"), msg("user", "hi"), msg("assistant", "yo")], "next")
    assert out[0] == {"role": "user", "content": "hi"}


def test_history_merges_consecutive_same_role():
    out = history_to_messages([msg("user", "a"), msg("user", "b"), msg("assistant", "c"), msg("assistant", "d")], "e")
    assert out == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c\n\nd"},
        {"role": "user", "content": "e"},
    ]


def test_unanswered_user_message_merges_with_new_one():
    out = history_to_messages([msg("user", "hello?"), ], "anyone there?")
    assert out == [{"role": "user", "content": "hello?\n\nanyone there?"}]


def test_empty_history():
    assert history_to_messages([], "hi") == [{"role": "user", "content": "hi"}]
