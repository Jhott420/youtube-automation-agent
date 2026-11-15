"""ChatEngine: the single entry point every interface (Telegram, HTTP) uses.

For each incoming message:
  1. kill switch (paused users get no reply at all)
  2. first-contact AI disclosure
  3. retrieve memories -> build prompt -> Claude -> store reply
Summarization is a separate step (`summarize_if_needed`) so interfaces can run
it after the reply has been delivered.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from .config import Settings
from .db import Database
from .embeddings import make_embedder
from .llm import LLM, ClaudeLLM, LLMError
from .memory import MemoryStore
from .persona import PersonaManager
from .prompt_builder import build_prompt

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 4000

ReplyKind = Literal["normal", "paused", "too_long", "error"]


@dataclass
class Reply:
    kind: ReplyKind
    text: str | None                 # None => send nothing (paused)
    disclosure: str | None = None    # send this BEFORE `text` (first contact)


class ChatEngine:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        personas: PersonaManager,
        memory: MemoryStore,
        llm: LLM,
    ):
        self.settings = settings
        self.db = db
        self.personas = personas
        self.memory = memory
        self.llm = llm

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLM | None = None) -> "ChatEngine":
        db = Database(settings.db_path)
        return cls(
            settings=settings,
            db=db,
            personas=PersonaManager(settings.persona_path),
            memory=MemoryStore(db, make_embedder(settings.embedder, settings.embedding_model)),
            llm=llm or ClaudeLLM(settings.model, api_key=settings.anthropic_api_key),
        )

    # ------------------------------------------------------------------

    def disclosure_text(self) -> str:
        return self.personas.get().disclosure

    def greet(self, user_id: str, display_name: str | None = None, platform: str = "unknown") -> Reply:
        """Explicit start of a conversation (e.g. Telegram /start): always disclose."""
        user = self.db.get_or_create_user(user_id, display_name, platform)
        if user.paused:
            return Reply("paused", None)
        persona = self.personas.get()
        self.db.mark_disclosed(user_id)
        return Reply("normal", persona.greeting or None, disclosure=persona.disclosure)

    def handle_message(
        self, user_id: str, text: str, display_name: str | None = None, platform: str = "unknown"
    ) -> Reply:
        user = self.db.get_or_create_user(user_id, display_name, platform)

        # 1. Kill switch: stay completely silent.
        if user.paused:
            log.info("User %s is paused; not replying", user_id)
            return Reply("paused", None)

        persona = self.personas.get()

        # 2. First contact: disclose before anything else.
        disclosure = None
        if user.disclosed_at is None:
            disclosure = persona.disclosure
            self.db.mark_disclosed(user_id)

        text = text.strip()
        if len(text) > MAX_INPUT_CHARS:
            return Reply(
                "too_long",
                f"That message is too long for me. Please keep it under {MAX_INPUT_CHARS} characters.",
                disclosure,
            )

        # 3. Reply.
        history = self.db.recent_messages(user_id, self.settings.history_window)
        self.db.add_message(user_id, "user", text)
        memories = self.memory.retrieve(
            user_id, text, k=self.settings.memory_top_k, min_score=self.settings.memory_min_score
        )
        bundle = build_prompt(persona, memories, history, text, user.display_name)

        try:
            reply_text = self.llm.complete(bundle.system, bundle.messages, self.settings.max_reply_tokens)
        except LLMError as e:
            log.error("LLM failure for %s: %s", user_id, e)
            return Reply("error", "Sorry, I'm having trouble replying right now. Please try again in a moment.", disclosure)

        self.db.add_message(user_id, "assistant", reply_text)
        return Reply("normal", reply_text, disclosure)

    def summarize_if_needed(self, user_id: str) -> int:
        """Fold older turns into long-term memories. Safe to call after every reply."""
        return self.memory.maybe_summarize(
            user_id,
            self.llm,
            window=self.settings.history_window,
            batch=self.settings.summarize_batch,
            max_tokens=self.settings.max_summary_tokens,
        )
