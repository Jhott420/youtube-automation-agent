"""Long-term memory: storage, retrieval (embedding similarity) and summarization.

Flow per user:
  1. Every turn is stored in `messages` (see Database).
  2. The newest `history_window` messages go to Claude verbatim.
  3. Once `summarize_batch` older, not-yet-summarized messages pile up, Claude
     condenses them into short factual memories, each stored with an embedding.
  4. Each turn, the top-k memories most similar to the user's message are
     injected into the system prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .db import Database, MemoryRow, Message
from .embeddings import Embedder, cosine
from .llm import LLM, LLMError

log = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """You maintain long-term memory for an AI companion chatbot.
Read the conversation excerpt and extract durable facts worth remembering about the USER:
names, preferences, important people/pets, life events, plans, ongoing topics, and how they
like to be talked to. Ignore small talk and anything the AI said about itself.

Rules:
- One fact per line, each line starting with "- ".
- Write in third person ("User ...").
- Keep each fact under 25 words.
- Do NOT record passwords, financial details, government IDs, or health crisis details.
- If there is nothing worth remembering, output exactly: NONE"""


@dataclass
class RetrievedMemory:
    id: int
    content: str
    score: float


class MemoryStore:
    def __init__(self, db: Database, embedder: Embedder):
        self.db = db
        self.embedder = embedder

    # ---- CRUD (used by the admin CLI / API) ---------------------------------

    def add(self, user_id: str, content: str, source: str = "manual") -> int:
        content = content.strip()
        if not content:
            raise ValueError("memory content cannot be empty")
        self.db.get_or_create_user(user_id)
        return self.db.add_memory(user_id, content, self.embedder.name, self.embedder.embed(content), source)

    def edit(self, memory_id: int, content: str) -> bool:
        content = content.strip()
        if not content:
            raise ValueError("memory content cannot be empty")
        return self.db.update_memory(memory_id, content, self.embedder.name, self.embedder.embed(content))

    def delete(self, memory_id: int) -> bool:
        return self.db.delete_memory(memory_id)

    def wipe(self, user_id: str) -> int:
        return self.db.wipe_memories(user_id)

    def list(self, user_id: str) -> list[MemoryRow]:
        return self.db.list_memories(user_id)

    # ---- retrieval -----------------------------------------------------------

    def retrieve(self, user_id: str, query: str, k: int = 5, min_score: float = 0.0) -> list[RetrievedMemory]:
        """Top-k memories for this user ranked by cosine similarity to `query`."""
        if k <= 0 or not query.strip():
            return []
        q = self.embedder.embed(query)
        scored: list[RetrievedMemory] = []
        for m in self.db.list_memories(user_id):
            vec = m.embedding
            if m.embedder != self.embedder.name:
                # Embedder changed since this memory was stored: re-embed lazily.
                vec = self.embedder.embed(m.content)
                self.db.update_memory_embedding(m.id, self.embedder.name, vec)
            score = cosine(q, vec)
            if score >= min_score:
                scored.append(RetrievedMemory(m.id, m.content, score))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    # ---- summarization ---------------------------------------------------------

    def maybe_summarize(self, user_id: str, llm: LLM, window: int, batch: int, max_tokens: int = 1024) -> int:
        """Summarize older messages into memories if enough have piled up.

        Returns the number of memories created. Never raises on LLM failure --
        the messages simply stay unsummarized and are retried next time.
        """
        pending = self.db.unsummarized_outside_window(user_id, window)
        if len(pending) < batch:
            return 0

        # Crisis-flagged turns are never turned into long-term memories.
        usable = [m for m in pending if not m.flag]
        created = 0
        if usable:
            transcript = _format_transcript(usable)
            try:
                raw = llm.complete(
                    system=[{"type": "text", "text": SUMMARY_SYSTEM_PROMPT}],
                    messages=[{"role": "user", "content": f"<conversation>\n{transcript}\n</conversation>"}],
                    max_tokens=max_tokens,
                )
            except LLMError as e:
                log.warning("Summarization failed for %s: %s", user_id, e)
                return 0
            for fact in parse_summary(raw):
                self.add(user_id, fact, source="summary")
                created += 1

        self.db.mark_summarized([m.id for m in pending])
        log.info("Summarized %d messages into %d memories for %s", len(pending), created, user_id)
        return created


def _format_transcript(messages: list[Message]) -> str:
    who = {"user": "User", "assistant": "AI"}
    return "\n".join(f"{who[m.role]}: {m.content}" for m in messages)


def parse_summary(raw: str) -> list[str]:
    """Turn the summarizer's bullet list into clean fact strings."""
    if raw.strip().upper() == "NONE":
        return []
    facts = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ", "• ")):
            fact = line[2:].strip()
            if fact:
                facts.append(fact)
    return facts
