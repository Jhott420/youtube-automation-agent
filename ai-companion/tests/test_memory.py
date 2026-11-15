"""Memory storage, embedding retrieval and summarization."""

from __future__ import annotations

import math

from companion.embeddings import HashingEmbedder, cosine
from companion.llm import LLMError
from companion.memory import parse_summary

from .conftest import FakeLLM

FACTS = [
    "User's dog is named Max, a golden retriever",
    "User works as a nurse on night shifts",
    "User is learning to play the guitar",
    "User's sister Ana lives in Lisbon",
    "User hates cilantro and loves spicy ramen",
]


def seed(memory, user="u1"):
    return [memory.add(user, f) for f in FACTS]


# ---- embedder -----------------------------------------------------------------


def test_hashing_embedder_is_deterministic_and_normalized():
    e = HashingEmbedder()
    a, b = e.embed("My dog Max loves the beach"), e.embed("My dog Max loves the beach")
    assert a == b
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)


def test_hashing_embedder_similar_text_scores_higher():
    e = HashingEmbedder()
    q = e.embed("what is my dog called?")
    assert cosine(q, e.embed("User's dog is named Max")) > cosine(q, e.embed("User works night shifts"))


def test_empty_text_embeds_to_zero_vector():
    assert cosine(HashingEmbedder().embed(""), HashingEmbedder().embed("dog")) == 0.0


# ---- retrieval ----------------------------------------------------------------


def test_retrieve_ranks_relevant_memory_first(memory):
    seed(memory)
    cases = {
        "how is my dog doing?": "dog is named Max",
        "I picked up my guitar again today": "guitar",
        "work was exhausting, another night shift": "nurse",
        "visiting my sister next month": "sister Ana",
        "let's get ramen": "spicy ramen",
    }
    for query, expected in cases.items():
        top = memory.retrieve("u1", query, k=1)
        assert top and expected in top[0].content, (query, top)


def test_retrieve_respects_k_and_sorts_by_score(memory):
    seed(memory)
    results = memory.retrieve("u1", "dog guitar sister", k=3)
    assert len(results) == 3
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_retrieve_min_score_filters_unrelated(memory):
    seed(memory)
    assert memory.retrieve("u1", "quantum chromodynamics lecture", k=5, min_score=0.3) == []


def test_retrieve_is_isolated_per_user(memory):
    seed(memory, "u1")
    memory.add("u2", "User owns a cat named Luna")
    assert all("Luna" not in r.content for r in memory.retrieve("u1", "my cat", k=10))
    assert memory.retrieve("u2", "my cat", k=1)[0].content == "User owns a cat named Luna"


def test_retrieve_empty_query_or_zero_k(memory):
    seed(memory)
    assert memory.retrieve("u1", "   ", k=5) == []
    assert memory.retrieve("u1", "dog", k=0) == []


def test_retrieve_reembeds_when_embedder_changes(memory, db):
    mid = memory.add("u1", "User's dog is named Max")
    memory.embedder = HashingEmbedder(dim=128)  # simulate switching embedders
    top = memory.retrieve("u1", "dog", k=1)
    assert top[0].id == mid
    row = db.get_memory(mid)
    assert row.embedder == "hashing-v1-d128" and len(row.embedding) == 128


# ---- CRUD -----------------------------------------------------------------------


def test_edit_reembeds_and_changes_retrieval(memory):
    mid = memory.add("u1", "User's dog is named Max")
    assert memory.edit(mid, "User's cat is named Luna")
    assert memory.retrieve("u1", "cat", k=1)[0].content == "User's cat is named Luna"
    assert memory.edit(9999, "nope") is False


def test_delete_and_wipe(memory):
    ids = seed(memory)
    memory.add("u2", "other user fact")
    assert memory.delete(ids[0]) and not memory.delete(ids[0])
    assert len(memory.list("u1")) == len(FACTS) - 1
    assert memory.wipe("u1") == len(FACTS) - 1
    assert memory.list("u1") == [] and len(memory.list("u2")) == 1


# ---- summarization ----------------------------------------------------------------


def _chat(db, user, n):
    db.get_or_create_user(user)
    for i in range(n):
        db.add_message(user, "user" if i % 2 == 0 else "assistant", f"message {i}")


def test_summarize_waits_for_batch(memory, db):
    _chat(db, "u1", 6)  # window 4 => only 2 old messages, batch is 4
    llm = FakeLLM(["- User likes tea"])
    assert memory.maybe_summarize("u1", llm, window=4, batch=4) == 0
    assert llm.calls == []


def test_summarize_creates_memories_and_marks_messages(memory, db):
    _chat(db, "u1", 10)  # 6 old messages outside a window of 4
    llm = FakeLLM(["- User's dog is named Max\n- User likes tea\nsome stray text"])
    assert memory.maybe_summarize("u1", llm, window=4, batch=4) == 2
    assert {m.content for m in memory.list("u1")} == {"User's dog is named Max", "User likes tea"}
    assert all(m.source == "summary" for m in memory.list("u1"))
    msgs = db.all_messages("u1")
    assert [m.summarized for m in msgs] == [True] * 6 + [False] * 4
    # The summarizer saw only the old messages, not the recent window.
    sent = llm.calls[0]["messages"][0]["content"]
    assert "message 5" in sent and "message 6" not in sent
    # Running again does nothing: everything outside the window is summarized.
    assert memory.maybe_summarize("u1", llm, window=4, batch=4) == 0


def test_summarize_skips_flagged_messages(memory, db):
    db.get_or_create_user("u1")
    db.add_message("u1", "user", "I want to die", flag="crisis")
    db.add_message("u1", "assistant", "resources...", flag="crisis")
    _chat(db, "u1", 8)
    llm = FakeLLM(["NONE"])
    memory.maybe_summarize("u1", llm, window=4, batch=4)
    assert "want to die" not in llm.calls[0]["messages"][0]["content"]
    assert all(m.summarized for m in db.all_messages("u1")[:6])


def test_summarize_llm_failure_leaves_messages_pending(memory, db):
    _chat(db, "u1", 10)

    class Broken:
        def complete(self, *a, **k):
            raise LLMError("boom")

    assert memory.maybe_summarize("u1", Broken(), window=4, batch=4) == 0
    assert not any(m.summarized for m in db.all_messages("u1"))


def test_parse_summary():
    assert parse_summary("NONE") == []
    assert parse_summary("- a\n* b\n• c\nnot a bullet\n- ") == ["a", "b", "c"]
