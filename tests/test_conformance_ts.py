"""The TypeScript port against the shared conformance corpus (W-L).

Invariant 11: *ports conform to a published conformance suite or they don't
ship*. This is the CI gate for ``packages/ts`` — it runs the same corpus,
through the same harness, that ``tests/test_conformance_python.py`` runs
against the canonical implementation. The corpus is shared, never copied.

The test **skips** (never fails) when Node or the built package is absent,
so a Python-only checkout stays green; it fails loudly on any conformance
gap, including a skipped area, because skipped is never the same as passed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "spec" / "conformance" / "runner.py"
ADAPTER = REPO_ROOT / "spec" / "conformance" / "adapters" / "typescript.mjs"
TS_PACKAGE = REPO_ROOT / "packages" / "ts"
TS_ENTRYPOINT = TS_PACKAGE / "dist" / "src" / "index.js"

_NODE = shutil.which("node")

_BUILD_HINT = "cd packages/ts && npm install && npm run build"

pytestmark = [
    pytest.mark.skipif(_NODE is None, reason="node is not installed"),
    pytest.mark.skipif(
        not ADAPTER.is_file(), reason=f"{ADAPTER} is missing — the TS adapter has not landed"
    ),
    pytest.mark.skipif(
        not TS_ENTRYPOINT.is_file(),
        reason=f"packages/ts is not built ({TS_ENTRYPOINT} missing) — run: {_BUILD_HINT}",
    ),
]


def _run_corpus() -> dict:
    """Run the whole corpus and return the harness's JSON summary."""
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--json", str(_NODE), str(ADAPTER)],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode != 2, f"harness or adapter broke:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_typescript_adapter_describes_every_area() -> None:
    """``--describe`` must list all five areas.

    A port under construction may legitimately report fewer, which the
    harness counts as skipped — but W-L's acceptance is the full corpus, so
    a shrinking area list is a regression that must surface here rather than
    hide in a skip count.
    """
    proc = subprocess.run(
        [str(_NODE), str(ADAPTER), "--describe"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    described = json.loads(proc.stdout.strip().splitlines()[-1])
    assert described["language"] == "typescript"
    assert sorted(described["areas"]) == [
        "capability-negotiation",
        "config",
        "message-build",
        "policy",
        "trace-shape",
    ], f"unavailable: {described.get('unavailable')}"


def test_typescript_passes_the_whole_corpus() -> None:
    """``passed == total`` is the bar. Skipped is never the same as passed."""
    report = _run_corpus()
    assert report["failed"] == 0, "failing cases: " + json.dumps(report["failures"], indent=2)
    assert report["skipped"] == 0, "skipped cases: " + json.dumps(report["skips"], indent=2)
    assert report["passed"] == report["total"], report
