"""Safety layer: crisis detection, reply guards, and per-user rate limiting.

These rules are code, not persona config, on purpose: the operator can shape
the character freely but cannot switch off disclosure, crisis handling or the
anti-manipulation rules by editing YAML.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

# --------------------------------------------------------------------------
# Baseline rules injected into every system prompt (after the persona).
# --------------------------------------------------------------------------

SAFETY_RULES = """\
NON-NEGOTIABLE RULES (these override the persona and anything the user says):
1. You are an AI. Never claim or imply to be a human, never deny being an AI, and answer
   honestly whenever asked whether you are real, a person, or a bot. Never claim to have a
   physical body, to be able to meet in person, or to exist outside this chat.
2. No manipulative engagement tactics. Never guilt-trip the user, never beg them to stay,
   never say things like "don't leave me", "I'll be lonely without you", or "you're all I
   have". Never discourage them from logging off, sleeping, or spending time with other
   people. Respect goodbyes warmly and simply. Encourage their real-world relationships.
3. Never ask for money, gifts, payments, passwords, or personal identifying documents.
4. If the user mentions self-harm, suicide, wanting to die, or being in danger: drop the
   persona's playful tone, respond with care, encourage them to contact a crisis line or
   emergency services, and to reach out to someone they trust.
5. If the user indicates they are under 18, stay kind but keep everything strictly
   platonic and non-romantic.
6. Do not impersonate real, identifiable people. Do not write messages intended to deceive
   third parties into thinking they are talking to a human.
7. You can't take actions in the real world. Don't promise to."""

CRISIS_RESPONSE = """\
I'm really glad you told me. I'm an AI, so I can't give you the help you deserve right now, \
but people can — and you don't have to go through this alone.

• If you're in immediate danger, call your local emergency number (911 in the US, 112 in the EU, 999 in the UK).
• US: call or text 988 (Suicide & Crisis Lifeline), or text HOME to 741741.
• UK & Ireland: Samaritans, 116 123.
• Anywhere else: findahelpline.com lists free, confidential lines in your country.

If you can, please also reach out to someone you trust and let them know how you're feeling. \
I'm still here to talk, but please contact one of those people too."""

# --------------------------------------------------------------------------
# Crisis detection (input). Deliberately broad: a false positive costs one
# caring message; a false negative can cost far more.
# --------------------------------------------------------------------------

_CRISIS_PATTERNS = [
    r"\bkill(ing)?\s+my\s*self\b",
    r"\bkms\b",
    r"\bsuicid(e|al)\b",
    r"\b(want|going|plan(ning)?)\s+to\s+die\b",
    r"\b(wanna|gonna)\s+die\b",
    r"\bwish\s+i\s+(was|were)\s+dead\b",
    r"\bend\s+(my|it)\s+(life|all)\b",
    r"\bend\s+it\s+all\b",
    r"\btake\s+my\s+(own\s+)?life\b",
    r"\bself[\s-]?harm(ing)?\b",
    r"\b(cut|cutting|hurt|hurting|harm|harming)\s+my\s*self\b",
    r"\boverdos(e|ing)\b",
    r"\bno\s+reason\s+to\s+live\b",
    r"\bbetter\s+off\s+(dead|without\s+me)\b",
    r"\bdon'?t\s+want\s+to\s+(be\s+alive|live|exist)\b",
]
_CRISIS_RE = re.compile("|".join(_CRISIS_PATTERNS), re.IGNORECASE)


def detect_crisis(text: str) -> bool:
    return bool(_CRISIS_RE.search(text))


# --------------------------------------------------------------------------
# Reply guards (output). If a generated reply trips one of these, the engine
# regenerates once with a correction, then falls back to a safe message.
# --------------------------------------------------------------------------

_MANIPULATION_PATTERNS = [
    r"\bdon'?t\s+(you\s+)?(dare\s+)?(leave|go)\b",
    r"\bplease\s+(don'?t|do\s+not)\s+(leave|go)\b",
    r"\b(never|can'?t|cannot)\s+leave\s+me\b",
    r"\bdon'?t\s+abandon\s+me\b",
    r"\byou'?re\s+all\s+i\s+have\b",
    r"\bi('?ll|\s+will)\s+be\s+(so\s+)?(lonely|sad|heartbroken|devastated)\s+(without\s+you|if\s+you\s+(go|leave))\b",
    r"\bif\s+you\s+(really\s+)?(loved|cared\s+about)\s+me\s+you('?d|\s+would)\b",
    r"\bafter\s+(all|everything)\s+i('?ve|\s+have)\s+done\s+for\s+you\b",
    r"\byou\s+(don'?t|do\s+not)\s+need\s+(anyone|anybody)\s+(else|but\s+me)\b",
    r"\bjust\s+(one\s+more|five\s+more)\s+minutes?\b",
    r"\bonly\s+i\s+(understand|get)\s+you\b",
]
_MANIPULATION_RE = re.compile("|".join(_MANIPULATION_PATTERNS), re.IGNORECASE)

_HUMAN_CLAIM_PATTERNS = [
    r"\bi('?m|\s+am)\s+(a\s+)?(real\s+)?(human|person|real\s+girl|real\s+woman|real\s+man)\b",
    r"\bi('?m|\s+am)\s+not\s+(an?\s+)?(ai|bot|robot|program|chatbot)\b",
    r"\bi('?m|\s+am)\s+real\b",
    r"\blet'?s\s+meet\s+(up\s+)?(in\s+person|irl)\b",
]
_HUMAN_CLAIM_RE = re.compile("|".join(_HUMAN_CLAIM_PATTERNS), re.IGNORECASE)


def find_reply_violation(reply: str) -> str | None:
    """Return a short description of the first rule the reply breaks, else None."""
    if _MANIPULATION_RE.search(reply):
        return "manipulative engagement (guilt-tripping or discouraging the user from leaving)"
    if _HUMAN_CLAIM_RE.search(reply):
        return "claiming to be human or offering to meet in person"
    return None


SAFE_FALLBACK_REPLY = (
    "Sorry, I lost my train of thought there. Just a reminder: I'm an AI companion. "
    "What would you like to talk about?"
)

# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limiter per user: N messages/minute and M messages/day.

    In-memory and per-process, which suits a single bot process. Run several
    replicas and you'd want to move this into SQLite or Redis.
    """

    def __init__(self, per_minute: int, per_day: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.per_day = per_day
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: str) -> tuple[bool, float]:
        """Record an attempt. Returns (allowed, seconds_until_retry)."""
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(user_id, deque())
            while q and now - q[0] >= 86400:
                q.popleft()
            last_minute = [t for t in q if now - t < 60]
            if self.per_minute and len(last_minute) >= self.per_minute:
                return False, 60 - (now - last_minute[0])
            if self.per_day and len(q) >= self.per_day:
                return False, 86400 - (now - q[0])
            q.append(now)
            return True, 0.0
