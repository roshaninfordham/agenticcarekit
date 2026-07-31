"""``evals`` — golden-set harness, judge wiring, and score rendering (W-C).

Fully offline except ``judge_with_provider``, which takes any
``Provider`` (Contract 1) supplied by the caller — this package never
imports a concrete provider itself.
"""

from .harness import EvalCase, EvalReport, EvalRow, judge_with_provider, load_golden, run_eval
from .table import report_json, score_table

__all__ = [
    "EvalCase",
    "EvalReport",
    "EvalRow",
    "judge_with_provider",
    "load_golden",
    "report_json",
    "run_eval",
    "score_table",
]
