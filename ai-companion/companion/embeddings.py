"""Text embedders for memory retrieval.

Default: `HashingEmbedder` -- pure Python, no downloads, no extra dependencies.
It hashes word unigrams and character n-grams into a fixed-size vector. That
is enough to match "what's my dog called?" to "User's dog is named Max".

Optional: `SentenceTransformerEmbedder` for real semantic embeddings. It needs
the `sentence-transformers` package (large: pulls in PyTorch), so it is NOT in
requirements.txt. Install it yourself and set EMBEDDER=sentence-transformers.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")

# Small stopword list: words that carry no retrieval signal.
_STOPWORDS = frozenset(
    """a an and are as at be but by do does did for from has have he her his i if in is it
    its me my of on or our she so that the their them they this to was we were what when
    where which who why will with you your yours i'm im it's its s t""".split()
)


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> list[float]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _stem(word: str) -> str:
    """Very light suffix stripping so 'dogs'/'dog', 'walking'/'walk' collide."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


class HashingEmbedder:
    """Feature-hashing bag of words + char n-grams, L2-normalized."""

    def __init__(self, dim: int = 512, ngram: int = 4, ngram_weight: float = 0.4):
        self.dim = dim
        self.ngram = ngram
        self.ngram_weight = ngram_weight
        self.name = f"hashing-v1-d{dim}"

    def _bucket(self, feature: str) -> tuple[int, float]:
        # md5 is stable across processes (unlike Python's salted hash()).
        h = hashlib.md5(feature.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = [_stem(w) for w in _TOKEN.findall(text.lower()) if w not in _STOPWORDS]
        for w in words:
            idx, sign = self._bucket("w:" + w)
            vec[idx] += sign
            padded = f"#{w}#"
            for i in range(max(1, len(padded) - self.ngram + 1)):
                idx, sign = self._bucket("c:" + padded[i : i + self.ngram])
                vec[idx] += sign * self.ngram_weight
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class SentenceTransformerEmbedder:
    """Wrapper around sentence-transformers (optional dependency)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:  # pragma: no cover - optional path
            raise RuntimeError(
                "EMBEDDER=sentence-transformers requires `pip install sentence-transformers`"
            ) from e
        self._model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"

    def embed(self, text: str) -> list[float]:  # pragma: no cover - optional path
        return [float(x) for x in self._model.encode(text, normalize_embeddings=True)]


def make_embedder(kind: str, model_name: str = "all-MiniLM-L6-v2") -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind in ("sentence-transformers", "st"):
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"unknown EMBEDDER {kind!r} (use 'hashing' or 'sentence-transformers')")
