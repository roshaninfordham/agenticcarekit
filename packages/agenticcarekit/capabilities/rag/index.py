"""Minimal, dependency-free local RAG: deterministic chunking plus a
stdlib-only TF-IDF cosine retriever. No numpy, no vector database — this is
the "it fits on a laptop with no network" tier.

Example:
    >>> idx = LocalIndex()
    >>> idx.add("doc1", "The quick brown fox jumps over the lazy dog.")
    >>> idx.add("doc2", "Paris is the capital of France.")
    >>> hits = idx.search("Which fox jumps?")
    >>> hits[0].doc_id
    'doc1'
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Hit", "LocalIndex"]

_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 200
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Hit:
    """One scored search result."""

    doc_id: str
    chunk_idx: int
    text: str
    score: float


@dataclass(frozen=True)
class _Chunk:
    doc_id: str
    chunk_idx: int
    text: str
    meta: dict


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split ``text`` into ~``size``-char chunks with ``overlap`` char
    overlap between consecutive chunks. Deterministic: same input always
    yields the same chunk boundaries.

    Example:
        >>> len(_chunk_text("a" * 1000))
        2
    """
    if not text:
        return [""]
    step = size - overlap
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        chunks.append(text[i : i + size])
        if i + size >= n:
            break
        i += step
    return chunks


class LocalIndex:
    """A tiny in-memory (optionally on-disk) document index.

    Chunks are ~800 chars with 200 char overlap. Retrieval is TF-IDF cosine
    similarity computed with the standard library only. Ordering is fully
    deterministic: score descending, then ``doc_id``, then ``chunk_idx``.
    """

    def __init__(self) -> None:
        self._chunks: list[_Chunk] = []

    def add(self, doc_id: str, text: str, meta: dict | None = None) -> None:
        """Chunk ``text`` and add it to the index under ``doc_id``.

        Example:
            >>> idx = LocalIndex()
            >>> idx.add("d1", "hello world")
            >>> len(idx.search("hello"))
            1
        """
        for idx, piece in enumerate(_chunk_text(text)):
            self._chunks.append(_Chunk(doc_id=doc_id, chunk_idx=idx, text=piece, meta=meta or {}))

    def search(self, query: str, k: int = 4) -> list[Hit]:
        """Return the top ``k`` chunks by TF-IDF cosine similarity to ``query``.

        Ties (including an all-zero-score query) break deterministically by
        ``doc_id`` then ``chunk_idx``.
        """
        if not self._chunks:
            return []

        doc_tokens = [_tokenize(c.text) for c in self._chunks]
        n_docs = len(doc_tokens)
        doc_freq: Counter[str] = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))

        def idf(term: str) -> float:
            freq = doc_freq.get(term, 0)
            return math.log((n_docs + 1) / (freq + 1)) + 1.0

        def vectorize(tokens: list[str]) -> tuple[dict[str, float], float]:
            counts = Counter(tokens)
            vec = {term: count * idf(term) for term, count in counts.items()}
            norm = math.sqrt(sum(w * w for w in vec.values()))
            return vec, norm

        query_vec, query_norm = vectorize(_tokenize(query))

        scored: list[tuple[float, str, int, str]] = []
        for chunk, tokens in zip(self._chunks, doc_tokens, strict=True):
            doc_vec, doc_norm = vectorize(tokens)
            if query_norm == 0.0 or doc_norm == 0.0:
                score = 0.0
            else:
                dot = sum(w * doc_vec.get(term, 0.0) for term, w in query_vec.items())
                score = dot / (query_norm * doc_norm)
            scored.append((score, chunk.doc_id, chunk.chunk_idx, chunk.text))

        scored.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [
            Hit(doc_id=doc_id, chunk_idx=chunk_idx, text=text, score=score)
            for score, doc_id, chunk_idx, text in scored[:k]
        ]

    def to_dict(self) -> dict:
        """Deterministic JSON-serializable representation of the index."""
        return {
            "chunks": [
                {
                    "doc_id": c.doc_id,
                    "chunk_idx": c.chunk_idx,
                    "text": c.text,
                    "meta": c.meta,
                }
                for c in self._chunks
            ]
        }

    def save(self, path: str | Path) -> None:
        """Write the index to ``path`` as deterministic (sorted-key) JSON.

        Example:
            >>> import tempfile, os
            >>> idx = LocalIndex()
            >>> idx.add("d1", "hello world")
            >>> path = tempfile.mktemp()
            >>> idx.save(path)
            >>> idx2 = LocalIndex.load(path)
            >>> idx2.search("hello")[0].doc_id
            'd1'
            >>> os.remove(path)
        """
        Path(path).write_text(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> LocalIndex:
        """Load an index previously written by :meth:`save`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        index = cls()
        for c in data["chunks"]:
            index._chunks.append(
                _Chunk(
                    doc_id=c["doc_id"],
                    chunk_idx=c["chunk_idx"],
                    text=c["text"],
                    meta=c.get("meta", {}),
                )
            )
        return index
