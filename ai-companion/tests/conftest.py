from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from companion.config import Settings
from companion.db import Database
from companion.embeddings import HashingEmbedder
from companion.engine import ChatEngine
from companion.memory import MemoryStore
from companion.persona import PersonaManager

ROOT = Path(__file__).resolve().parent.parent


class FakeLLM:
    """Records every call and replays canned replies (last one repeats)."""

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or ["hey you 🙂"])
        self.calls: list[dict] = []

    def complete(self, system, messages, max_tokens):
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


@pytest.fixture
def persona_path(tmp_path) -> Path:
    dst = tmp_path / "persona.yaml"
    shutil.copy(ROOT / "config" / "persona.yaml", dst)
    return dst


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def memory(db) -> MemoryStore:
    return MemoryStore(db, HashingEmbedder())


@pytest.fixture
def settings(tmp_path, persona_path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        persona_path=persona_path,
        history_window=4,
        summarize_batch=4,
        admin_api_token="test-token",
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def engine(settings, db, memory, fake_llm) -> ChatEngine:
    return ChatEngine(
        settings=settings,
        db=db,
        personas=PersonaManager(settings.persona_path),
        memory=memory,
        llm=fake_llm,
    )
