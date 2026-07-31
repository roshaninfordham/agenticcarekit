"""Score a :class:`~agenticcarekit.kernel.contracts.Redactor` against a
hand-labelled PHI golden set.

Matching is entity-level: a predicted redaction and a labelled entity
match when their categories are equal **and** their text spans overlap
(the labelled set carries entity text, not offsets, so the expected span
is located in the input text and compared by character-range overlap
against the redactor's actual span — not by string equality of category
alone). This is honest scoring, not a rigged one: a redactor that
over-redacts (flags text with no matching label) loses precision; one
that under-redacts loses recall.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agenticcarekit.kernel.contracts import Redactor

__all__ = ["score_phi_redactor"]


def _expected_spans(text: str, entities: list[dict]) -> list[tuple[int, int, str]]:
    """Locate each labelled entity's text in ``text``, returning
    ``(start, end, category)``. Repeated occurrences of the same text are
    matched to distinct positions in order."""
    spans: list[tuple[int, int, str]] = []
    search_from: dict[str, int] = {}
    for ent in entities:
        needle = ent["text"]
        category = ent["category"]
        start_at = search_from.get(needle, 0)
        idx = text.find(needle, start_at)
        if idx == -1:
            idx = text.find(needle)  # fall back to first occurrence
            if idx == -1:
                continue
        end = idx + len(needle)
        spans.append((idx, end, category))
        search_from[needle] = end
    return spans


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def score_phi_redactor(redactor: Redactor, labelled_path: str | Path) -> dict:
    """Score ``redactor`` against a JSONL labelled set at ``labelled_path``.

    Each line: ``{"id", "input", "expected": {"entities": [{"category",
    "text"}, ...]}, "tags": [...]}``.

    Returns ``{"precision": float, "recall": float, "per_category": {cat:
    {"precision": float, "recall": float, "tp": int, "fp": int, "fn": int}}}``.

    Example:
        >>> import tempfile, os
        >>> from agenticcarekit.packs.healthcare.phi import PHIRedactor
        >>> lines = [
        ...     json.dumps({"id": "1", "input": "Call Jane Smith at 555-123-4567.",
        ...                 "expected": {"entities": [
        ...                     {"category": "NAME", "text": "Jane Smith"},
        ...                     {"category": "PHONE", "text": "555-123-4567"},
        ...                 ]}, "tags": []})
        ... ]
        >>> fd, path = tempfile.mkstemp(suffix=".jsonl")
        >>> _ = os.write(fd, ("\\n".join(lines)).encode())
        >>> os.close(fd)
        >>> result = score_phi_redactor(PHIRedactor(), path)
        >>> 0.0 <= result["precision"] <= 1.0
        True
        >>> os.remove(path)
    """
    path = Path(labelled_path)
    total_tp = total_fp = total_fn = 0
    per_category: dict[str, dict[str, int]] = {}

    def _bucket(cat: str) -> dict[str, int]:
        return per_category.setdefault(cat, {"tp": 0, "fp": 0, "fn": 0})

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            text = record["input"]
            expected_entities = record["expected"]["entities"]
            expected = _expected_spans(text, expected_entities)
            _, redactions = redactor.redact(text)
            predicted = [(r.start, r.end, r.category) for r in redactions]

            matched_expected: set[int] = set()
            matched_predicted: set[int] = set()
            for pi, (ps, pe, pcat) in enumerate(predicted):
                for ei, (es, ee, ecat) in enumerate(expected):
                    if ei in matched_expected:
                        continue
                    if pcat == ecat and _overlaps(ps, pe, es, ee):
                        matched_expected.add(ei)
                        matched_predicted.add(pi)
                        break

            for pi, (_, _, pcat) in enumerate(predicted):
                if pi in matched_predicted:
                    total_tp += 1
                    _bucket(pcat)["tp"] += 1
                else:
                    total_fp += 1
                    _bucket(pcat)["fp"] += 1
            for ei, (_, _, ecat) in enumerate(expected):
                if ei not in matched_expected:
                    total_fn += 1
                    _bucket(ecat)["fn"] += 1

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 1.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 1.0

    per_category_scored = {}
    for cat, counts in sorted(per_category.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        cat_precision = tp / (tp + fp) if (tp + fp) else 1.0
        cat_recall = tp / (tp + fn) if (tp + fn) else 1.0
        per_category_scored[cat] = {
            "precision": cat_precision,
            "recall": cat_recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    return {
        "precision": precision,
        "recall": recall,
        "per_category": per_category_scored,
    }
