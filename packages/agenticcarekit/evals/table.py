"""Rendering an ``EvalReport``: a rich table for terminals, a stable dict
for ``--json`` (invariant 10: non-TTY is first class).
"""

from __future__ import annotations

from typing import Any

from rich.table import Table

from .harness import EvalReport

__all__ = ["report_json", "score_table"]


def score_table(report: EvalReport) -> Table:
    """Render an ``EvalReport`` as a single, static ``rich.Table``.

    One render call, no live-updating rows — append-only output
    (invariant 9), never a full-screen TUI.

    Example:
        >>> from agenticcarekit.evals.harness import EvalRow
        >>> row = EvalRow(id="c1", input="i", expected="e", actual="e",
        ...                exact_match=True, judge_score=0.9, tags=["t"])
        >>> report = EvalReport(rows=[row], exact_match_rate=1.0, judge_score_avg=0.9)
        >>> table = score_table(report)
        >>> table.row_count
        1
    """
    table = Table(title="Eval results")
    table.add_column("id")
    table.add_column("exact_match")
    table.add_column("judge_score")
    table.add_column("tags")
    for row in report.rows:
        table.add_row(
            row.id,
            "pass" if row.exact_match else "fail",
            f"{row.judge_score:.2f}" if row.judge_score is not None else "-",
            ",".join(row.tags),
        )
    return table


def report_json(report: EvalReport) -> dict[str, Any]:
    """Stable, sorted-key ``--json`` surface for an ``EvalReport``.

    Top-level keys and every row's keys are alphabetically sorted so the
    output is byte-identical for identical inputs (invariant 4).

    Example:
        >>> from agenticcarekit.evals.harness import EvalRow
        >>> row = EvalRow(id="c1", input="i", expected="e", actual="e",
        ...                exact_match=True, judge_score=None, tags=[])
        >>> report = EvalReport(rows=[row], exact_match_rate=1.0, judge_score_avg=None)
        >>> data = report_json(report)
        >>> list(data.keys())
        ['exact_match_rate', 'judge_score_avg', 'rows']
        >>> list(data["rows"][0].keys())
        ['actual', 'exact_match', 'expected', 'id', 'input', 'judge_score', 'tags']
    """
    return {
        "exact_match_rate": report.exact_match_rate,
        "judge_score_avg": report.judge_score_avg,
        "rows": [dict(sorted(row.model_dump().items())) for row in report.rows],
    }
