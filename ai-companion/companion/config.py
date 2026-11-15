"""Runtime settings, loaded from environment variables (and a local .env file).

Secrets (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ADMIN_API_TOKEN) are only ever
read from the environment -- never hardcode them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Project root = the directory that contains the `companion` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present. Real environment variables win.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    p = Path(raw) if raw else default
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _id_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


@dataclass
class Settings:
    # --- Claude ---------------------------------------------------------
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-4-5"
    max_reply_tokens: int = 1024
    max_summary_tokens: int = 1024

    # --- Storage / files -----------------------------------------------
    db_path: Path = PROJECT_ROOT / "data" / "companion.db"
    persona_path: Path = PROJECT_ROOT / "config" / "persona.yaml"

    # --- Memory ----------------------------------------------------------
    history_window: int = 20          # recent messages sent verbatim to Claude
    summarize_batch: int = 20         # summarize once this many older msgs pile up
    memory_top_k: int = 5             # memories retrieved per turn
    memory_min_score: float = 0.15    # cosine-similarity floor for retrieval
    embedder: str = "hashing"         # "hashing" (default) or "sentence-transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # --- Interfaces -------------------------------------------------------
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: set[str] = field(default_factory=set)  # empty = anyone
    admin_api_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            model=os.getenv("CLAUDE_MODEL", cls.model),
            max_reply_tokens=_int("MAX_REPLY_TOKENS", cls.max_reply_tokens),
            max_summary_tokens=_int("MAX_SUMMARY_TOKENS", cls.max_summary_tokens),
            db_path=_path("DB_PATH", cls.db_path),
            persona_path=_path("PERSONA_PATH", cls.persona_path),
            history_window=_int("HISTORY_WINDOW", cls.history_window),
            summarize_batch=_int("SUMMARIZE_BATCH", cls.summarize_batch),
            memory_top_k=_int("MEMORY_TOP_K", cls.memory_top_k),
            memory_min_score=_float("MEMORY_MIN_SCORE", cls.memory_min_score),
            embedder=os.getenv("EMBEDDER", cls.embedder),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_allowed_user_ids=_id_set("TELEGRAM_ALLOWED_USER_IDS"),
            admin_api_token=os.getenv("ADMIN_API_TOKEN") or None,
        )
