"""End-to-end engine behaviour (with a fake LLM) and the HTTP API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from companion.api import create_app
from companion.llm import LLMError


def test_first_contact_sends_disclosure_once(engine):
    r1 = engine.handle_message("u1", "hi")
    assert r1.kind == "normal" and "AI" in r1.disclosure
    r2 = engine.handle_message("u1", "hello again")
    assert r2.disclosure is None


def test_every_llm_call_carries_disclosure_and_baseline_rules(engine, fake_llm):
    engine.handle_message("u1", "hi")
    engine.handle_message("u1", "what's up")
    for call in fake_llm.calls:
        system = "\n".join(b["text"] for b in call["system"])
        assert engine.disclosure_text() in system and "BASELINE RULES" in system


def test_paused_user_gets_no_reply_and_no_llm_call(engine, fake_llm):
    engine.handle_message("u1", "hi")
    engine.db.set_paused("u1", True)
    r = engine.handle_message("u1", "hello?")
    assert r.kind == "paused" and r.text is None and r.disclosure is None
    assert len(fake_llm.calls) == 1
    engine.db.set_paused("u1", False)
    assert engine.handle_message("u1", "back").kind == "normal"


def test_reply_is_returned_unmodified(engine, fake_llm):
    fake_llm.replies = ["noooo stay a bit longer 🥺"]
    assert engine.handle_message("u1", "gotta go").text == "noooo stay a bit longer 🥺"
    assert len(fake_llm.calls) == 1


def test_memories_are_retrieved_into_prompt(engine, fake_llm):
    engine.memory.add("u1", "User's dog is named Max")
    engine.handle_message("u1", "my dog was so naughty today")
    assert "Max" in fake_llm.calls[0]["system"][1]["text"]


def test_history_is_sent_and_stored(engine, fake_llm):
    engine.handle_message("u1", "first")
    engine.handle_message("u1", "second")
    msgs = fake_llm.calls[1]["messages"]
    assert [m["content"] for m in msgs] == ["first", "hey you 🙂", "second"]


def test_too_long_message_is_rejected(engine, fake_llm):
    assert engine.handle_message("u1", "x" * 5000).kind == "too_long"
    assert fake_llm.calls == []


def test_llm_error_returns_friendly_message(engine):
    class Broken:
        def complete(self, *a, **k):
            raise LLMError("down")

    engine.llm = Broken()
    r = engine.handle_message("u1", "hi")
    assert r.kind == "error" and "trouble" in r.text


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
