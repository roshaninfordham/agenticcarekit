"""Acceptance tests for the minimal local RAG index (W-E)."""

from __future__ import annotations

import pytest
from agenticcarekit.capabilities.rag import Hit, LocalIndex


def _build_index() -> LocalIndex:
    idx = LocalIndex()
    idx.add(
        "diabetes",
        "Type 2 diabetes is managed with metformin and lifestyle changes. "
        "Blood glucose monitoring is recommended daily.",
    )
    idx.add(
        "asthma",
        "Asthma is a chronic respiratory condition treated with inhaled corticosteroids "
        "and bronchodilators for symptom relief.",
    )
    idx.add(
        "hypertension",
        "Hypertension, or high blood pressure, is often treated with ACE inhibitors, "
        "diuretics, and reduced sodium intake.",
    )
    return idx


def test_search_returns_most_relevant_chunk_first():
    idx = _build_index()

    hits = idx.search("What medication treats asthma symptoms?")

    assert hits
    assert hits[0].doc_id == "asthma"
    assert isinstance(hits[0], Hit)
    assert hits[0].score >= hits[-1].score


def test_search_respects_k():
    idx = _build_index()
    hits = idx.search("blood", k=2)
    assert len(hits) <= 2


def test_search_on_empty_index_returns_empty_list():
    idx = LocalIndex()
    assert idx.search("anything") == []


def test_deterministic_ordering_on_tied_scores():
    idx = LocalIndex()
    idx.add("b", "completely unrelated filler content")
    idx.add("a", "completely unrelated filler content")

    hits = idx.search("query sharing no terms with either document")

    # All scores tie at 0.0 -> break by doc_id then chunk_idx.
    assert [h.doc_id for h in hits] == ["a", "b"]


def test_save_load_round_trips_byte_identically(tmp_path):
    idx = _build_index()
    path_a = tmp_path / "index_a.json"
    path_b = tmp_path / "index_b.json"

    idx.save(path_a)
    loaded = LocalIndex.load(path_a)
    loaded.save(path_b)

    assert path_a.read_bytes() == path_b.read_bytes()

    # And the loaded index answers the same query the same way.
    original_hits = idx.search("metformin diabetes")
    loaded_hits = loaded.search("metformin diabetes")
    assert original_hits == loaded_hits


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
