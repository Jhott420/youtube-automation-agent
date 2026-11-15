"""Safety layer and end-to-end engine behaviour (with a fake LLM)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from companion.api import create_app
from companion.llm import LLMError
from companion.safety import (
    CRISIS_RESPONSE,
    SAFE_FALLBACK_REPLY,
    RateLimiter,
    detect_crisis,
    find_reply_violation,
)

# ---- crisis detection --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I want to kill myself",
        "honestly i wanna die",
        "I've been self-harming again",
        "thinking about suicide",
        "everyone would be better off without me",
        "I don't want to be alive anymore",
        "I'm going to end it all",
    ],
)
def test_detect_crisis_positive(text):
    assert detect_crisis(text)


@pytest.mark.parametrize(
    "text",
    ["this traffic is killing me", "I died laughing", "the plants died while I was away", "cut the cake"],
)
def test_detect_crisis_negative(text):
    assert not detect_crisis(text)


# ---- reply guards --------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "Please don't leave me tonight",
        "You can't leave me now!",
        "I'll be so lonely without you",
        "you're all I have",
        "If you really loved me you'd stay",
        "just five more minutes? 🥺",
    ],
)
def test_manipulative_replies_are_flagged(reply):
    assert "manipulative" in find_reply_violation(reply)


@pytest.mark.parametrize("reply", ["I'm a real person, I promise", "I'm not an AI", "let's meet in person!"])
def test_human_claims_are_flagged(reply):
    assert "human" in find_reply_violation(reply)


@pytest.mark.parametrize("reply", ["Goodnight! Sleep well 🙂", "Have fun with your friends tonight!", "I'm an AI, yes."])
def test_ok_replies_pass(reply):
    assert find_reply_violation(reply) is None


# ---- rate limiter -------------------------------------------------------------------------


def test_rate_limiter_per_minute_and_day():
    now = [0.0]
    rl = RateLimiter(per_minute=2, per_day=3, clock=lambda: now[0])
    assert rl.check("u")[0] and rl.check("u")[0]
    allowed, retry = rl.check("u")
    assert not allowed and 0 < retry <= 60
    assert rl.check("other")[0]  # per-user
    now[0] = 61
    assert rl.check("u")[0]       # minute window reset -> 3rd message today
    now[0] = 200
    assert not rl.check("u")[0]   # daily cap reached
    now[0] = 86401
    assert rl.check("u")[0]


# ---- engine ------------------------------------------------------------------------------


def test_first_contact_sends_disclosure_once(engine):
    r1 = engine.handle_message("u1", "hi")
    assert r1.kind == "normal" and "AI" in r1.disclosure
    r2 = engine.handle_message("u1", "hello again")
    assert r2.disclosure is None


def test_every_llm_call_carries_disclosure_and_safety(engine, fake_llm):
    engine.handle_message("u1", "hi")
    engine.handle_message("u1", "what's up")
    for call in fake_llm.calls:
        system = "\n".join(b["text"] for b in call["system"])
        assert engine.disclosure_text() in system and "NON-NEGOTIABLE" in system


def test_paused_user_gets_no_reply_and_no_llm_call(engine, fake_llm):
    engine.handle_message("u1", "hi")
    engine.db.set_paused("u1", True)
    r = engine.handle_message("u1", "hello?")
    assert r.kind == "paused" and r.text is None and r.disclosure is None
    assert len(fake_llm.calls) == 1
    engine.db.set_paused("u1", False)
    assert engine.handle_message("u1", "back").kind == "normal"


def test_crisis_bypasses_llm_and_returns_resources(engine, fake_llm):
    r = engine.handle_message("u1", "I want to kill myself")
    assert r.kind == "crisis" and r.text == CRISIS_RESPONSE and "988" in r.text
    assert fake_llm.calls == []
    assert [m.flag for m in engine.db.all_messages("u1")] == ["crisis", "crisis"]


def test_manipulative_reply_is_regenerated(engine, fake_llm):
    fake_llm.replies = ["noooo don't leave me", "ok, goodnight! 🙂"]
    r = engine.handle_message("u1", "gotta go to bed")
    assert r.text == "ok, goodnight! 🙂"
    assert len(fake_llm.calls) == 2
    assert "rejected" in fake_llm.calls[1]["system"][1]["text"]


def test_persistent_violation_falls_back_to_safe_reply(engine, fake_llm):
    fake_llm.replies = ["I'm a real person"]
    assert engine.handle_message("u1", "are you real?").text == SAFE_FALLBACK_REPLY


def test_memories_are_retrieved_into_prompt(engine, fake_llm):
    engine.memory.add("u1", "User's dog is named Max")
    engine.handle_message("u1", "my dog was so naughty today")
    assert "Max" in fake_llm.calls[0]["system"][1]["text"]


def test_history_is_sent_and_stored(engine, fake_llm):
    engine.handle_message("u1", "first")
    engine.handle_message("u1", "second")
    msgs = fake_llm.calls[1]["messages"]
    assert [m["content"] for m in msgs] == ["first", "hey you 🙂", "second"]


def test_llm_error_returns_friendly_message(engine):
    class Broken:
        def complete(self, *a, **k):
            raise LLMError("down")

    engine.llm = Broken()
    r = engine.handle_message("u1", "hi")
    assert r.kind == "error" and "trouble" in r.text


def test_rate_limited_engine(engine, fake_llm):
    engine.rate_limiter = RateLimiter(per_minute=1, per_day=10)
    engine.handle_message("u1", "one")
    r = engine.handle_message("u1", "two")
    assert r.kind == "rate_limited" and len(fake_llm.calls) == 1


def test_summarize_after_enough_turns(engine, fake_llm):
    for i in range(4):
        engine.handle_message("u1", f"msg {i}")
    fake_llm.replies = ["- User likes tea"]
    assert engine.summarize_if_needed("u1") == 1
    assert engine.memory.list("u1")[0].content == "User likes tea"


# ---- API ------------------------------------------------------------------------------------


def test_api_requires_token_and_chats(engine):
    client = TestClient(create_app(engine))
    assert client.get("/health").json()["persona"] == "Mira"
    assert client.post("/chat", json={"user_id": "a", "text": "hi"}).status_code == 401
    auth = {"Authorization": "Bearer test-token"}
    r = client.post("/chat", json={"user_id": "a", "text": "hi"}, headers=auth).json()
    assert r["kind"] == "normal" and "AI" in r["disclosure"]
    assert client.post("/admin/users/api:a/pause", headers=auth).json()["paused"] is True
    assert client.post("/chat", json={"user_id": "a", "text": "hi"}, headers=auth).json()["kind"] == "paused"
    mid = client.post("/admin/users/api:a/memories", json={"content": "likes tea"}, headers=auth).json()["id"]
    assert client.get("/admin/users/api:a/memories", headers=auth).json()[0]["content"] == "likes tea"
    assert client.delete(f"/admin/memories/{mid}", headers=auth).json() == {"ok": True}


def test_api_disabled_without_token(engine):
    engine.settings.admin_api_token = None
    client = TestClient(create_app(engine))
    assert client.post("/chat", json={"user_id": "a", "text": "hi"}).status_code == 503
