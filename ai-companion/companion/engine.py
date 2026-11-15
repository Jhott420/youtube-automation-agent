"""ChatEngine: the single entry point every interface (Telegram, HTTP) uses.

Order of checks for each incoming message:
  1. kill switch (paused users get no reply at all)
  2. rate limit
  3. first-contact AI disclosure
  4. crisis detection -> fixed resource message, no LLM call
  5. normal reply: retrieve memories -> build prompt -> Claude -> reply guard
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
from .safety import (
    CRISIS_RESPONSE,
    SAFE_FALLBACK_REPLY,
    RateLimiter,
    detect_crisis,
    find_reply_violation,
)

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 4000

ReplyKind = Literal["normal", "crisis", "paused", "rate_limited", "too_long", "error"]


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
        rate_limiter: RateLimiter,
    ):
        self.settings = settings
        self.db = db
        self.personas = personas
        self.memory = memory
        self.llm = llm
        self.rate_limiter = rate_limiter

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLM | None = None) -> "ChatEngine":
        db = Database(settings.db_path)
        return cls(
            settings=settings,
            db=db,
            personas=PersonaManager(settings.persona_path),
            memory=MemoryStore(db, make_embedder(settings.embedder, settings.embedding_model)),
            llm=llm or ClaudeLLM(settings.model, api_key=settings.anthropic_api_key),
            rate_limiter=RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day),
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

        # 2. Rate limit.
        allowed, retry_in = self.rate_limiter.check(user_id)
        if not allowed:
            return Reply(
                "rate_limited",
                f"You're messaging faster than I can keep up. Try again in about {int(retry_in) + 1} seconds.",
            )

        persona = self.personas.get()

        # 3. First contact: disclose before anything else.
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

        # 4. Crisis: fixed, vetted resources. No persona, no LLM.
        if detect_crisis(text):
            log.warning("Crisis language detected for user %s", user_id)
            self.db.add_message(user_id, "user", text, flag="crisis")
            self.db.add_message(user_id, "assistant", CRISIS_RESPONSE, flag="crisis")
            return Reply("crisis", CRISIS_RESPONSE, disclosure)

        # 5. Normal reply.
        history = self.db.recent_messages(user_id, self.settings.history_window)
        self.db.add_message(user_id, "user", text)
        memories = self.memory.retrieve(
            user_id, text, k=self.settings.memory_top_k, min_score=self.settings.memory_min_score
        )

        try:
            reply_text = self._generate(persona, memories, history, text, user.display_name)
        except LLMError as e:
            log.error("LLM failure for %s: %s", user_id, e)
            return Reply("error", "Sorry, I'm having trouble replying right now. Please try again in a moment.", disclosure)

        self.db.add_message(user_id, "assistant", reply_text)
        return Reply("normal", reply_text, disclosure)

    def _generate(self, persona, memories, history, text, display_name) -> str:
        """Call Claude; if the reply breaks a safety rule, retry once with a
        correction, then fall back to a safe canned reply."""
        correction = None
        for _attempt in range(2):
            bundle = build_prompt(persona, memories, history, text, display_name, extra_instruction=correction)
            reply = self.llm.complete(bundle.system, bundle.messages, self.settings.max_reply_tokens)
            violation = find_reply_violation(reply)
            if violation is None:
                return reply
            log.warning("Reply rejected (%s); regenerating", violation)
            correction = (
                f"Your previous draft was rejected for {violation}. Rewrite it without that, "
                "following the non-negotiable rules."
            )
        return SAFE_FALLBACK_REPLY

    def summarize_if_needed(self, user_id: str) -> int:
        """Fold older turns into long-term memories. Safe to call after every reply."""
        return self.memory.maybe_summarize(
            user_id,
            self.llm,
            window=self.settings.history_window,
            batch=self.settings.summarize_batch,
            max_tokens=self.settings.max_summary_tokens,
        )
