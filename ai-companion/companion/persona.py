"""Persona model + hot-reloading loader for config/persona.yaml.

Hot reload: every call to `PersonaManager.get()` checks the file's mtime and
re-parses it when it changed. If the new file is invalid, the last good
persona stays active and the error is logged -- a typo in the YAML never takes
the bot down or strips the AI disclosure.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

# The disclosure line must literally say the bot is an AI.
_AI_WORD = re.compile(r"\b(AI|A\.I\.|artificial intelligence)\b", re.IGNORECASE)


class Persona(BaseModel):
    """Everything the operator can configure about the character."""

    name: str = Field(min_length=1)
    age: int
    backstory: str = ""
    personality_traits: list[str] = Field(default_factory=list)
    speech_style: str = ""
    emoji_habits: str = ""
    relationship_dynamic: str = ""
    interests: list[str] = Field(default_factory=list)
    hard_limits: list[str] = Field(default_factory=list)
    # Injected into EVERY system prompt and sent on first contact.
    disclosure: str = Field(min_length=1)
    # Optional first message after the disclosure on /start.
    greeting: str = ""

    @field_validator("age")
    @classmethod
    def _adult_only(cls, v: int) -> int:
        # Companion personas must be adults. Non-negotiable.
        if v < 18:
            raise ValueError("persona age must be 18 or older")
        return v

    @field_validator("disclosure")
    @classmethod
    def _must_disclose_ai(cls, v: str) -> str:
        if not _AI_WORD.search(v):
            raise ValueError("disclosure must explicitly state that the companion is an AI")
        return v.strip()


class PersonaError(Exception):
    """Raised when a persona file can't be loaded and no previous good copy exists."""


def load_persona_file(path: Path) -> Persona:
    """Parse and validate a persona YAML file. Raises PersonaError on failure."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PersonaError(f"persona file not found: {path}") from e
    except yaml.YAMLError as e:
        raise PersonaError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise PersonaError(f"{path} must contain a YAML mapping at the top level")
    try:
        return Persona.model_validate(data)
    except ValidationError as e:
        raise PersonaError(f"invalid persona in {path}:\n{e}") from e


class PersonaManager:
    """Thread-safe, hot-reloading access to the current persona."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._persona: Persona | None = None
        self._mtime: float | None = None
        self.last_error: str | None = None
        self.get()  # fail fast at startup if there is no valid persona at all

    def get(self) -> Persona:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                mtime = None

            if self._persona is None or mtime != self._mtime:
                try:
                    persona = load_persona_file(self.path)
                except PersonaError as e:
                    self.last_error = str(e)
                    if self._persona is None:
                        raise
                    log.error("Persona reload failed; keeping previous persona. %s", e)
                    self._mtime = mtime  # don't re-log on every message
                else:
                    if self._persona is not None:
                        log.info("Persona reloaded from %s", self.path)
                    self._persona = persona
                    self._mtime = mtime
                    self.last_error = None
            return self._persona
