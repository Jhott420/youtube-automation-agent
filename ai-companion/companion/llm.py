"""Thin wrapper around the Anthropic Messages API.

Everything else talks to the `LLM` protocol, so tests can swap in a fake and
the model is just a config string (CLAUDE_MODEL).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import anthropic

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when Claude can't produce a usable reply (API error, refusal, empty)."""


class LLM(Protocol):
    def complete(self, system: list[dict[str, Any]], messages: list[dict[str, Any]], max_tokens: int) -> str: ...


class ClaudeLLM:
    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60.0):
        # api_key=None lets the SDK resolve credentials from the environment.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    def complete(self, system: list[dict[str, Any]], messages: list[dict[str, Any]], max_tokens: int) -> str:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API rate limit hit") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("Claude API key is invalid or missing") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("could not reach the Claude API") from e

        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to answer")

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise LLMError("Claude returned an empty reply")
        if response.stop_reason == "max_tokens":
            log.warning("Reply hit max_tokens (%d); it may be cut off", max_tokens)
        return text
