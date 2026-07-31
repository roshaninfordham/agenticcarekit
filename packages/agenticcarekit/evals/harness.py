"""Eval harness — golden sets, scoring, and judge wiring.

Cases are ``pydantic`` models loaded from JSONL golden files (never Python
literals, per invariant 4's spirit of no hidden state). Scoring combines
exact-match with an optional judge score in ``[0.0, 1.0]``; judges are
built from any ``Provider`` (Contract 1) against a rubric loaded from a
``.md`` file — prompts are markdown files, never string literals.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from agenticcarekit.kernel.contracts import GenerateRequest, Message, Provider

__all__ = [
    "EvalCase",
    "EvalReport",
    "EvalRow",
    "judge_with_provider",
    "load_golden",
    "run_eval",
]


class EvalCase(BaseModel):
    """One golden case loaded from a JSONL golden set.

    Example:
        >>> c = EvalCase(id="c1", input="2+2", expected="4", tags=["math"])
        >>> c.id, c.tags
        ('c1', ['math'])
    """

    id: str
    input: str
    expected: str
    tags: list[str] = Field(default_factory=list)


class EvalRow(BaseModel):
    """Per-case scoring result, one row of an ``EvalReport``."""

    id: str
    input: str
    expected: str
    actual: str
    exact_match: bool
    judge_score: float | None = None
    tags: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    """Aggregate result of one eval run: per-case rows plus aggregates.

    Example:
        >>> row = EvalRow(id="c1", input="i", expected="e", actual="e",
        ...                exact_match=True, judge_score=None, tags=[])
        >>> report = EvalReport(rows=[row], exact_match_rate=1.0, judge_score_avg=None)
        >>> report.exact_match_rate
        1.0
    """

    rows: list[EvalRow]
    exact_match_rate: float
    judge_score_avg: float | None = None


def load_golden(path: str | Path) -> list[EvalCase]:
    """Load a JSONL golden set into a list of ``EvalCase``.

    Example:
        >>> import tempfile, os
        >>> path = tempfile.mktemp()
        >>> _ = Path(path).write_text(
        ...     '{"id": "c1", "input": "hi", "expected": "hi", "tags": []}\\n'
        ... )
        >>> cases = load_golden(path)
        >>> cases[0].id
        'c1'
        >>> os.remove(path)
    """
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(EvalCase.model_validate(json.loads(line)))
    return cases


def run_eval(
    cases: list[EvalCase],
    fn: Callable[[str], str],
    judge: Callable[[EvalCase, str], float] | None = None,
) -> EvalReport:
    """Run ``fn`` over every case and score exact-match (+ optional judge).

    ``exact_match_rate`` is the fraction of cases where ``fn(case.input)
    == case.expected``. ``judge_score_avg`` is the mean of ``judge``'s
    scores when a judge is supplied, else ``None``.

    Example:
        >>> cases = [
        ...     EvalCase(id="c1", input="2+2", expected="4", tags=[]),
        ...     EvalCase(id="c2", input="1+1", expected="2", tags=[]),
        ... ]
        >>> report = run_eval(cases, fn=lambda x: "4" if x == "2+2" else "9")
        >>> report.exact_match_rate
        0.5
    """
    rows: list[EvalRow] = []
    for case in cases:
        actual = fn(case.input)
        exact = actual == case.expected
        judge_score = judge(case, actual) if judge is not None else None
        rows.append(
            EvalRow(
                id=case.id,
                input=case.input,
                expected=case.expected,
                actual=actual,
                exact_match=exact,
                judge_score=judge_score,
                tags=case.tags,
            )
        )
    exact_rate = (sum(1 for r in rows if r.exact_match) / len(rows)) if rows else 0.0
    judge_scores = [r.judge_score for r in rows if r.judge_score is not None]
    judge_avg = (sum(judge_scores) / len(judge_scores)) if judge_scores else None
    return EvalReport(rows=rows, exact_match_rate=exact_rate, judge_score_avg=judge_avg)


def judge_with_provider(
    provider: Provider, rubric_prompt_path: str | Path
) -> Callable[[EvalCase, str], float]:
    """Build a judge function from any ``Provider`` using a rubric loaded
    from a markdown file.

    The rubric template is formatted with ``{input}``, ``{expected}``, and
    ``{actual}`` and sent as the sole user message; the returned judge
    parses the provider's response text as a bare float, defaulting to
    ``0.0`` if parsing fails rather than raising mid-eval-run.

    Example:
        >>> from agenticcarekit.kernel.contracts import GenerateResponse
        >>> class StubProvider:
        ...     name = "stub"
        ...     def capabilities(self):
        ...         raise NotImplementedError
        ...     def generate(self, req):
        ...         return GenerateResponse(text="0.75")
        ...     def stream(self, req):
        ...         raise NotImplementedError
        >>> import tempfile, os
        >>> path = tempfile.mktemp(suffix=".md")
        >>> _ = Path(path).write_text("Score {actual} vs {expected} for {input}.")
        >>> judge = judge_with_provider(StubProvider(), path)
        >>> case = EvalCase(id="c1", input="q", expected="e", tags=[])
        >>> judge(case, "a")
        0.75
        >>> os.remove(path)
    """
    rubric_template = Path(rubric_prompt_path).read_text(encoding="utf-8")

    def judge(case: EvalCase, actual: str) -> float:
        prompt = rubric_template.format(input=case.input, expected=case.expected, actual=actual)
        req = GenerateRequest(messages=(Message.text("user", prompt),))
        resp = provider.generate(req)
        try:
            return float(resp.text.strip())
        except ValueError:
            return 0.0

    return judge
