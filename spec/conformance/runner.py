#!/usr/bin/env python3
"""The agenticcarekit conformance harness.

Runs the language-neutral fixture corpus in ``spec/conformance/cases/``
against an *adapter*: any executable that speaks the JSON-lines protocol
described in ``spec/conformance/README.md``.

The harness is deliberately trivial — roughly 200 lines of stdlib — because
re-implementing it in another language must never be a reason to skip
conformance. Python here is a convenience, not a dependency of the suite.

Usage::

    python spec/conformance/runner.py -- python spec/conformance/adapters/python.py
    python spec/conformance/runner.py --filter policy -- ./my-adapter
    python spec/conformance/runner.py --json -- node adapters/ts.mjs

Exit codes: 0 every case passed · 1 at least one case failed · 2 the
harness or the adapter itself broke (bad JSON, crash, wrong result count).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).resolve().parent / "cases"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_HARNESS = 2


# ── corpus ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """One fixture: an input, the expected result, and where it came from."""

    id: str
    area: str
    input: dict[str, Any]
    expected: dict[str, Any]
    note: str = ""
    source: str = ""


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    """Every case in the corpus, in stable (file, declaration) order."""
    cases: list[Case] = []
    for path in sorted(cases_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        suite = doc.get("suite", path.stem)
        for raw in doc["cases"]:
            cases.append(
                Case(
                    id=raw["id"],
                    area=raw.get("area", suite),
                    input=raw["input"],
                    expected=raw["expected"],
                    note=raw.get("note", ""),
                    source=path.name,
                )
            )
    return cases


# ── comparison ───────────────────────────────────────────────────────────


def _canonical(value: Any) -> Any:
    """Sorted-key normalization: JSON objects compare by content, not order."""
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def diff(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Human-readable differences between two JSON values.

    Returns an empty list when they are deep-equal after sorted-key
    normalization. Numbers compare by value, so ``1.0`` matches ``1``
    (JSON has one number type; languages disagree about the rest).
    """
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                out.append(f"{path}.{key}: missing (expected {json.dumps(expected[key])})")
            elif key not in expected:
                out.append(f"{path}.{key}: unexpected {json.dumps(actual[key])}")
            else:
                out += diff(expected[key], actual[key], f"{path}.{key}")
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(actual)}, expected {len(expected)}"]
        out = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            out += diff(e, a, f"{path}[{i}]")
        return out
    if isinstance(expected, bool) != isinstance(actual, bool):
        return [f"{path}: {json.dumps(actual)}, expected {json.dumps(expected)}"]
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return [] if expected == actual else [f"{path}: {actual}, expected {expected}"]
    if expected != actual:
        return [f"{path}: {json.dumps(actual)}, expected {json.dumps(expected)}"]
    return []


def compare(expected: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """Check one adapter result against a case's ``expected`` block.

    Error expectations are a *subset* match: ``{"error": {"code": "E203"}}``
    accepts any raised error carrying that code, whatever else the
    implementation attaches (message, why, fix, details). Everything else is
    deep equality against the adapter's ``output``.
    """
    if "error" in expected:
        actual_err = result.get("error")
        if actual_err is None:
            got = json.dumps(_canonical(result.get("output")))
            return [f"$: expected error {json.dumps(expected['error'])}, got output {got}"]
        subset = {k: actual_err.get(k) for k in expected["error"]}
        return diff(_canonical(expected["error"]), _canonical(subset), "$.error")
    if "error" in result:
        return [f"$: unexpected error {json.dumps(result['error'])}"]
    return diff(_canonical(expected), _canonical(result.get("output")), "$")


# ── adapter transport ────────────────────────────────────────────────────


class AdapterError(RuntimeError):
    """The adapter process misbehaved — not a conformance failure."""


def describe(adapter: list[str]) -> dict[str, Any]:
    """Ask an adapter which areas it implements (``--describe``).

    An adapter that does not support the probe is treated as implementing
    every area, which is the safe default: cases then fail loudly instead of
    disappearing into a skip count.
    """
    try:
        proc = subprocess.run(
            [*adapter, "--describe"], capture_output=True, text=True, timeout=120, check=False
        )
    except OSError as exc:
        raise AdapterError(f"cannot execute adapter {adapter!r}: {exc}") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {}


def run_adapter(adapter: list[str], cases: list[Case], one_shot: bool = False) -> list[dict]:
    """Feed cases to the adapter and collect one result per case."""
    if one_shot:
        return [_run_one(adapter, case) for case in cases]
    payload = "".join(json.dumps(_wire(c), sort_keys=True) + "\n" for c in cases)
    try:
        proc = subprocess.run(
            adapter, input=payload, capture_output=True, text=True, timeout=900, check=False
        )
    except OSError as exc:
        raise AdapterError(f"cannot execute adapter {adapter!r}: {exc}") from None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) != len(cases):
        raise AdapterError(
            f"adapter returned {len(lines)} results for {len(cases)} cases "
            f"(exit {proc.returncode}). stderr:\n{proc.stderr.strip()}"
        )
    return [_parse_line(ln, case) for ln, case in zip(lines, cases, strict=True)]


def _run_one(adapter: list[str], case: Case) -> dict[str, Any]:
    """One process per case — slower, but the simplest thing an adapter can be."""
    proc = subprocess.run(
        adapter,
        input=json.dumps(_wire(case), sort_keys=True) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise AdapterError(
            f"{case.id}: adapter returned {len(lines)} lines, expected 1 "
            f"(exit {proc.returncode}). stderr:\n{proc.stderr.strip()}"
        )
    return _parse_line(lines[0], case)


def _wire(case: Case) -> dict[str, Any]:
    """The case as the adapter sees it — never the expected result."""
    return {"id": case.id, "area": case.area, "input": case.input}


def _parse_line(line: str, case: Case) -> dict[str, Any]:
    try:
        result = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{case.id}: adapter emitted invalid JSON: {exc}") from None
    if not isinstance(result, dict):
        raise AdapterError(f"{case.id}: adapter result is not a JSON object")
    if result.get("id") not in (None, case.id):
        raise AdapterError(f"{case.id}: adapter answered for {result.get('id')!r} instead")
    return result


# ── reporting ────────────────────────────────────────────────────────────


@dataclass
class Report:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "failing_ids": [f["id"] for f in self.failures],
            "failures": self.failures,
            "skips": self.skips,
        }


def evaluate(cases: list[Case], results: list[dict[str, Any]]) -> Report:
    """Turn adapter results into a pass/fail/skip report."""
    report = Report(total=len(cases))
    for case, result in zip(cases, results, strict=True):
        if "unsupported" in result:
            report.skipped += 1
            report.skips.append({"id": case.id, "reason": str(result["unsupported"])})
            continue
        differences = compare(case.expected, result)
        if differences:
            report.failed += 1
            report.failures.append(
                {
                    "id": case.id,
                    "area": case.area,
                    "source": case.source,
                    "note": case.note,
                    "diff": differences,
                    "expected": case.expected,
                    "actual": result.get("output", result),
                }
            )
        else:
            report.passed += 1
    return report


def render(report: Report, verbose: bool) -> str:
    """Append-only text report (invariant 9: no full-screen anything)."""
    lines: list[str] = []
    for failure in report.failures:
        lines.append(f"FAIL {failure['id']}  ({failure['source']})")
        if failure["note"]:
            lines.append(f"     {failure['note']}")
        for d in failure["diff"]:
            lines.append(f"     {d}")
    if verbose:
        for skip in report.skips:
            lines.append(f"SKIP {skip['id']}  {skip['reason']}")
    lines.append(
        f"{report.passed}/{report.total} passed · {report.failed} failed · {report.skipped} skipped"
    )
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.py",
        description="Run the agenticcarekit conformance corpus against an adapter.",
    )
    parser.add_argument("--filter", dest="area", help="only run cases from this area")
    parser.add_argument("--case", dest="case_id", help="only run this case id")
    parser.add_argument("--cases", type=Path, default=CASES_DIR, help="corpus directory")
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    parser.add_argument("--one-shot", action="store_true", help="one adapter process per case")
    parser.add_argument("-v", "--verbose", action="store_true", help="list skipped cases too")
    parser.add_argument("adapter", nargs=argparse.REMAINDER, help="adapter command (after --)")
    args = parser.parse_args(argv)

    adapter = [a for a in args.adapter if a != "--"]
    if not adapter:
        parser.error("no adapter command given (use: runner.py [options] -- <cmd> [args...])")

    cases = load_cases(args.cases)
    if args.area:
        cases = [c for c in cases if c.area == args.area]
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
    if not cases:
        print("no cases matched the filter", file=sys.stderr)
        return EXIT_HARNESS

    try:
        supported = describe(adapter).get("areas")
        if supported is not None:
            unsupported = [c for c in cases if c.area not in supported]
            cases = [c for c in cases if c.area in supported]
            report = (
                evaluate(cases, run_adapter(adapter, cases, args.one_shot)) if cases else Report()
            )
            report.total += len(unsupported)
            report.skipped += len(unsupported)
            report.skips += [
                {"id": c.id, "reason": f"adapter does not implement area {c.area!r}"}
                for c in unsupported
            ]
        else:
            report = evaluate(cases, run_adapter(adapter, cases, args.one_shot))
    except AdapterError as exc:
        if args.json:
            print(json.dumps({"harness_error": str(exc)}, indent=2))
        else:
            print(f"harness error: {exc}", file=sys.stderr)
        return EXIT_HARNESS

    print(
        json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.json
        else render(report, args.verbose)
    )
    return EXIT_OK if report.failed == 0 else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
