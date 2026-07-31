"""Unit tests for ``agenticcarekit.evals.harness``."""

from __future__ import annotations

from pathlib import Path

import pytest
from agenticcarekit.evals import EvalCase, judge_with_provider, load_golden, run_eval
from agenticcarekit.kernel.contracts import (
    Capabilities,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Modality,
)


class StubProvider:
    """Minimal local Provider stub — never import a real provider here."""

    name = "stub"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[GenerateRequest] = []

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities_in=frozenset({Modality.TEXT}),
            modalities_out=frozenset({Modality.TEXT}),
            tool_calling=False,
            streaming=False,
            context_tokens=8192,
            thinking=False,
            egress=EgressClass.DEVICE,
        )

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.calls.append(req)
        return GenerateResponse(text=self.response_text)

    def stream(self, req: GenerateRequest):  # pragma: no cover - unused in tests
        raise NotImplementedError


def test_load_golden_reads_jsonl_cases(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id": "c1", "input": "hi", "expected": "hi", "tags": ["greet"]}\n'
        '{"id": "c2", "input": "bye", "expected": "bye", "tags": []}\n'
    )
    cases = load_golden(path)
    assert [c.id for c in cases] == ["c1", "c2"]
    assert cases[0].tags == ["greet"]


def test_load_golden_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text('{"id": "c1", "input": "hi", "expected": "hi", "tags": []}\n\n\n')
    assert len(load_golden(path)) == 1


def test_run_eval_exact_match_scoring() -> None:
    cases = [
        EvalCase(id="c1", input="2+2", expected="4", tags=[]),
        EvalCase(id="c2", input="1+1", expected="2", tags=[]),
        EvalCase(id="c3", input="3+3", expected="6", tags=[]),
    ]

    def fn(x: str) -> str:
        # deliberately wrong on c2
        return {"2+2": "4", "1+1": "wrong", "3+3": "6"}[x]

    report = run_eval(cases, fn)
    assert [r.exact_match for r in report.rows] == [True, False, True]
    assert report.exact_match_rate == pytest.approx(2 / 3)
    assert report.judge_score_avg is None


def test_run_eval_with_judge_averages_scores() -> None:
    cases = [
        EvalCase(id="c1", input="a", expected="a", tags=[]),
        EvalCase(id="c2", input="b", expected="b", tags=[]),
    ]

    def stub_judge(case: EvalCase, actual: str) -> float:
        return 1.0 if actual == case.expected else 0.0

    report = run_eval(cases, fn=lambda x: x, judge=stub_judge)
    assert report.judge_score_avg == pytest.approx(1.0)
    assert all(r.judge_score == 1.0 for r in report.rows)


def test_run_eval_empty_cases() -> None:
    report = run_eval([], fn=lambda x: x)
    assert report.rows == []
    assert report.exact_match_rate == 0.0
    assert report.judge_score_avg is None


def test_judge_with_provider_builds_working_judge(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.md"
    rubric_path.write_text("Input: {input}\nExpected: {expected}\nActual: {actual}\nScore:")
    provider = StubProvider(response_text="0.5")

    judge = judge_with_provider(provider, rubric_path)
    case = EvalCase(id="c1", input="q", expected="e", tags=[])
    score = judge(case, "a")

    assert score == pytest.approx(0.5)
    assert len(provider.calls) == 1
    sent_text = provider.calls[0].messages[0].parts[0].text
    assert "Input: q" in sent_text
    assert "Expected: e" in sent_text
    assert "Actual: a" in sent_text


def test_judge_with_provider_defaults_to_zero_on_bad_response(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.md"
    rubric_path.write_text("Score {input} {expected} {actual}")
    provider = StubProvider(response_text="not-a-number")

    judge = judge_with_provider(provider, rubric_path)
    case = EvalCase(id="c1", input="q", expected="e", tags=[])
    assert judge(case, "a") == 0.0


def test_judge_reads_rubric_from_md_file_not_string_literal(tmp_path: Path) -> None:
    """Prompts are .md files, never string literals — sanity-check the
    judge actually reads from disk rather than embedding a literal."""
    rubric_path = tmp_path / "rubric.md"
    rubric_path.write_text("UNIQUE_MARKER {input} {expected} {actual}")
    provider = StubProvider(response_text="1.0")
    judge = judge_with_provider(provider, rubric_path)
    judge(EvalCase(id="c1", input="i", expected="e", tags=[]), "a")
    assert "UNIQUE_MARKER" in provider.calls[0].messages[0].parts[0].text
