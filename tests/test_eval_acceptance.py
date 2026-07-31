"""W-C acceptance test (eval half).

Three canned cases run against a stub ``fn``: exact-match scores must be
correct per case and in aggregate. The judge path is exercised with a
stub judge (no real provider, no network). ``report_json`` must be a
stable, sorted, JSON-serializable dict across repeated calls.
"""

from __future__ import annotations

import json

from agenticcarekit.evals import EvalCase, report_json, run_eval

CASES = [
    EvalCase(id="c1", input="what is 2+2", expected="4", tags=["math"]),
    EvalCase(id="c2", input="capital of france", expected="paris", tags=["geo"]),
    EvalCase(id="c3", input="color of sky", expected="blue", tags=["trivia"]),
]

CANNED_ANSWERS = {
    "what is 2+2": "4",  # exact match
    "capital of france": "Paris",  # wrong case -> not exact match
    "color of sky": "blue",  # exact match
}


def stub_fn(x: str) -> str:
    return CANNED_ANSWERS[x]


def stub_judge(case: EvalCase, actual: str) -> float:
    # Case-insensitive semantic judge: gives partial credit where exact
    # match failed only because of casing.
    return 1.0 if actual.strip().lower() == case.expected.strip().lower() else 0.0


def test_three_canned_cases_exact_match_scores() -> None:
    report = run_eval(CASES, stub_fn)

    results = {r.id: r.exact_match for r in report.rows}
    assert results == {"c1": True, "c2": False, "c3": True}
    assert report.exact_match_rate == 2 / 3
    assert report.judge_score_avg is None


def test_judge_path_exercised_with_stub_judge() -> None:
    report = run_eval(CASES, stub_fn, judge=stub_judge)

    judge_scores = {r.id: r.judge_score for r in report.rows}
    # c2 fails exact-match (casing) but the judge scores it 1.0 anyway.
    assert judge_scores == {"c1": 1.0, "c2": 1.0, "c3": 1.0}
    assert report.judge_score_avg == 1.0
    # exact-match aggregate is unaffected by the judge being present.
    assert report.exact_match_rate == 2 / 3


def test_report_json_stable_and_sorted() -> None:
    report = run_eval(CASES, stub_fn, judge=stub_judge)

    data = report_json(report)
    again = report_json(report)

    assert data == again
    assert list(data.keys()) == sorted(data.keys())
    for row in data["rows"]:
        assert list(row.keys()) == sorted(row.keys())

    # Fully JSON-serializable, deterministic when key-sorted.
    text1 = json.dumps(data, sort_keys=True)
    text2 = json.dumps(again, sort_keys=True)
    assert text1 == text2
