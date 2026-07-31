"""Unit tests for ``agenticcarekit.evals.table``."""

from __future__ import annotations

import json

from agenticcarekit.evals import EvalCase, report_json, run_eval, score_table


def _sample_report():
    cases = [
        EvalCase(id="c1", input="a", expected="a", tags=["t1"]),
        EvalCase(id="c2", input="b", expected="z", tags=[]),
    ]

    def judge(case: EvalCase, actual: str) -> float:
        return 1.0 if actual == case.expected else 0.2

    return run_eval(cases, fn=lambda x: x, judge=judge)


def test_score_table_has_one_row_per_case() -> None:
    report = _sample_report()
    table = score_table(report)
    assert table.row_count == 2


def test_score_table_column_headers() -> None:
    report = _sample_report()
    table = score_table(report)
    headers = [col.header for col in table.columns]
    assert headers == ["id", "exact_match", "judge_score", "tags"]


def test_report_json_is_json_serializable() -> None:
    report = _sample_report()
    data = report_json(report)
    # Must round-trip through json.dumps without error.
    text = json.dumps(data, sort_keys=True)
    assert json.loads(text) == data


def test_report_json_keys_are_sorted() -> None:
    report = _sample_report()
    data = report_json(report)
    assert list(data.keys()) == sorted(data.keys())
    for row in data["rows"]:
        assert list(row.keys()) == sorted(row.keys())


def test_report_json_is_stable_across_calls() -> None:
    report = _sample_report()
    assert report_json(report) == report_json(report)


def test_report_json_aggregates_match_report() -> None:
    report = _sample_report()
    data = report_json(report)
    assert data["exact_match_rate"] == report.exact_match_rate
    assert data["judge_score_avg"] == report.judge_score_avg
    assert len(data["rows"]) == len(report.rows)
