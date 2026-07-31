"""The Python implementation against the conformance corpus (W-J).

Invariant 11: one canonical implementation per tier, and ports conform to a
published suite or they don't ship. This module is how the *first* tier
proves it — the same JSON fixtures the TypeScript port (W-L) will run,
driven through the same harness, over the same adapter protocol.

Nothing here re-implements a rule. If a case fails, the fix is in the
implementation or in the fixture, never in this file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE = REPO_ROOT / "spec" / "conformance"
ADAPTER = [sys.executable, str(CONFORMANCE / "adapters" / "python.py")]

#: Area -> the module whose absence makes that area untestable. Areas land in
#: parallel workstreams; a missing implementation is a SKIP with a reason, not
#: a red suite and not a silent pass.
AREA_IMPLEMENTATIONS = {
    "message-build": "agenticcarekit.kernel.providers.builder",
    "capability-negotiation": "agenticcarekit.kernel.providers.models",
    "policy": "agenticcarekit.kernel.policy",
    "trace-shape": "agenticcarekit.kernel.trace.analysis",
    "config": "agenticcarekit.kernel.contracts.config",
}


def _load_runner():
    """Import the standalone harness by path.

    It deliberately lives outside the package: the suite must be runnable by
    a checkout with no Python project installed at all.
    """
    spec = importlib.util.spec_from_file_location(
        "ack_conformance_runner", CONFORMANCE / "runner.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their annotations through sys.modules, so a
    # path-loaded module has to register itself before it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _run(area: str):
    cases = [c for c in runner.load_cases() if c.area == area]
    assert cases, f"no cases found for area {area!r}"
    return cases, runner.evaluate(cases, runner.run_adapter(ADAPTER, cases))


@pytest.mark.parametrize("area", sorted(AREA_IMPLEMENTATIONS))
def test_area_conforms(area: str) -> None:
    pytest.importorskip(
        AREA_IMPLEMENTATIONS[area],
        reason=f"{AREA_IMPLEMENTATIONS[area]} has not landed yet; {area} cases are skipped",
    )
    cases, report = _run(area)
    if report.skipped:
        pytest.skip(f"{area}: adapter reported {report.skipped} unsupported case(s)")
    assert report.failed == 0, "\n" + runner.render(report, verbose=True)
    assert report.passed == len(cases)


def test_every_case_has_a_home() -> None:
    """Every case in the corpus belongs to a declared area — a fixture in an
    unknown area would silently never run."""
    unknown = {c.area for c in runner.load_cases()} - set(AREA_IMPLEMENTATIONS)
    assert not unknown, f"cases declare unknown areas: {sorted(unknown)}"


def test_adapter_describes_itself() -> None:
    """The adapter answers the ``--describe`` probe the protocol specifies."""
    described = runner.describe(ADAPTER)
    assert described.get("name") == "python"
    assert set(described.get("areas", [])) <= set(AREA_IMPLEMENTATIONS)


def test_harness_detects_a_wrong_answer() -> None:
    """The comparator must fail loudly on a wrong result.

    A conformance suite that cannot fail is decoration. This checks the three
    verdicts the protocol defines: match, mismatch, and subset-matched errors.
    """
    expected = {"options": {"temperature": 1.0}, "stream": False}
    assert runner.compare(expected, {"output": expected}) == []
    assert runner.compare(expected, {"output": {"options": {"temperature": 0.7}, "stream": False}})
    assert runner.compare(expected, {"error": {"code": "E301"}})

    # Error expectations match on the named fields only; extra fields on the
    # actual error (message, why, fix, details) are allowed.
    err_expected = {"error": {"code": "E203"}}
    assert runner.compare(err_expected, {"error": {"code": "E203", "message": "no audio"}}) == []
    assert runner.compare(err_expected, {"error": {"code": "E202"}})
    assert runner.compare(err_expected, {"output": {"ok": True}})
