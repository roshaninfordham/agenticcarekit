"""``check``, ``eval`` and ``demo``.

``ack check`` is the loop an agent closes against (brief §9): lint plus a
fast selftest, honest pass/fail, **under 30 seconds**. It never claims a
step passed that it did not run — a skipped step reports ``skipped`` with
the reason.

``ack eval`` and ``ack demo`` depend on workstreams that land later (the
eval harness needs a provider chain; the demo needs a generated app). Both
have their **final** command surface and ``--json`` envelope here; where a
runtime dependency is genuinely absent they fail with a registered code and
say plainly what is missing rather than pretending.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import AckConfig, AckError

__all__ = ["run_check", "run_demo", "run_eval_command"]

#: Total budget for ``ack check`` (brief §9: keep it under 30 seconds).
CHECK_BUDGET_SECONDS = 30
_STEP_TIMEOUT = 14

_SELFTEST = r"""
import doctest, importlib, sys

MODULES = [
    "agenticcarekit.kernel.contracts.provider",
    "agenticcarekit.kernel.contracts.policy",
    "agenticcarekit.kernel.contracts.tools",
    "agenticcarekit.kernel.contracts.trace",
    "agenticcarekit.kernel.contracts.config",
    "agenticcarekit.kernel.contracts.errors",
]
failed = attempted = missing = 0
for name in MODULES:
    try:
        mod = importlib.import_module(name)
    except Exception as exc:
        print(f"import-failed {name}: {type(exc).__name__}: {exc}")
        missing += 1
        continue
    result = doctest.testmod(mod, verbose=False, report=True)
    failed += result.failed
    attempted += result.attempted
print(f"RESULT failed={failed} attempted={attempted} missing={missing}")
sys.exit(1 if (failed or missing) else 0)
"""


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _tail(text: str, lines: int = 12) -> str:
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def run_check(root: Path) -> dict[str, Any]:
    """Lint + contracts doctests, honestly reported.

    Example:
        >>> sorted(run_check(Path(".")))  # doctest: +SKIP
        ['duration_ms', 'ok', 'steps', 'within_budget']
    """
    started = time.monotonic()
    steps: list[dict[str, Any]] = []

    ruff = shutil.which("ruff")
    ruff_argv = [ruff, "check", "."] if ruff else [sys.executable, "-m", "ruff", "check", "."]
    t0 = time.monotonic()
    code, output = _run(ruff_argv, root, _STEP_TIMEOUT)
    if code == 127 or "No module named" in output:
        steps.append(
            {
                "name": "lint",
                "status": "skipped",
                "detail": "ruff is not installed in this environment",
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        )
    else:
        steps.append(
            {
                "name": "lint",
                "status": "pass" if code == 0 else "fail",
                "detail": _tail(output) or "clean",
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        )

    t0 = time.monotonic()
    code, output = _run([sys.executable, "-c", _SELFTEST], root, _STEP_TIMEOUT)
    attempted = 0
    for line in output.splitlines():
        if line.startswith("RESULT "):
            for token in line.split()[1:]:
                key, _, value = token.partition("=")
                if key == "attempted" and value.isdigit():
                    attempted = int(value)
    steps.append(
        {
            "name": "selftest",
            "status": "pass" if code == 0 else "fail",
            "detail": _tail(output) or "no output",
            "doctests_attempted": attempted,
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        }
    )

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    return {
        "ok": all(s["status"] != "fail" for s in steps),
        "steps": steps,
        "duration_ms": duration_ms,
        "budget_seconds": CHECK_BUDGET_SECONDS,
        "within_budget": duration_ms / 1000 <= CHECK_BUDGET_SECONDS,
    }


# ── eval ─────────────────────────────────────────────────────────────────


def _golden_sets(root: Path) -> list[Path]:
    return sorted(
        {*root.glob("evals/*.jsonl"), *root.glob("evals/**/*.jsonl")},
        key=lambda p: p.as_posix(),
    )


def run_eval_command(root: Path, cfg: AckConfig, *, offline: bool = False) -> dict[str, Any]:
    """Score the project's golden set. Raises E601 when a piece is missing."""
    goldens = _golden_sets(root)
    if not goldens:
        raise AckError(
            f"no golden set found under {root / 'evals'}",
            code="E601",
            why="evals score against committed golden files; none exist in this project yet.",
            fix="ack eval --init   # scaffolds a golden set from the pack's clinical eval sets",
            details={"searched": str(root / "evals")},
        )
    try:
        from agenticcarekit.evals.harness import load_golden, run_eval
    except Exception as exc:  # noqa: BLE001
        raise AckError(
            "the eval harness is not available in this build",
            code="E601",
            why=f"importing agenticcarekit.evals.harness failed: {type(exc).__name__}.",
            fix="uv sync   # the harness ships with the package (W-C)",
        ) from None

    cases = load_golden(goldens[0])
    fn = _model_callable(cfg, offline=offline)
    report = run_eval(cases, fn)
    return {
        "golden_set": goldens[0].relative_to(root).as_posix(),
        "cases": len(cases),
        "exact_match_rate": report.exact_match_rate,
        "judge_score_avg": report.judge_score_avg,
        "offline": offline,
        "rows": [
            {"id": r.id, "exact_match": r.exact_match, "judge_score": r.judge_score}
            for r in report.rows
        ],
    }


def _model_callable(cfg: AckConfig, *, offline: bool):
    """Resolve ``ack.toml``'s primary model to a callable, or explain why not.

    The provider chain is W-A. Until it exposes a factory this raises a
    registered error naming the gap — an honest failure beats a fabricated
    score.
    """
    try:
        from agenticcarekit.kernel import providers as providers_mod
    except Exception:  # noqa: BLE001
        providers_mod = None  # type: ignore[assignment]
    factory = None
    for attr in ("provider_for", "build_provider", "from_ref", "resolve_provider"):
        factory = getattr(providers_mod, attr, None) if providers_mod else None
        if callable(factory):
            break
        factory = None
    if factory is None:
        raise AckError(
            "ack eval needs the provider chain, which is not wired in this build",
            code="E601",
            why=(
                "scoring calls the model declared in [model] primary; the provider "
                "factory (W-A) is not exposed yet, so there is nothing honest to score."
            ),
            fix="ack check   # verify the toolkit, then re-run once providers land",
            details={"model": str(cfg.model_primary), "offline": offline, "pending": "W-A"},
        )

    kwargs: dict[str, Any] = {"offline": offline}
    if cfg.model_fallback is not None:
        kwargs["fallback"] = str(cfg.model_fallback)
    try:
        provider = factory(str(cfg.model_primary), **kwargs)
    except TypeError:  # a third-party factory without the keyword surface
        provider = factory(str(cfg.model_primary))

    def call(text: str) -> str:  # pragma: no cover - integration path
        from agenticcarekit.kernel.contracts import GenerateRequest, Message

        return provider.generate(
            GenerateRequest(messages=(Message.text("user", text),), model=cfg.model_primary.model)
        ).text

    return call


# ── demo ─────────────────────────────────────────────────────────────────


def run_demo(root: Path, cfg: AckConfig, *, offline: bool = False) -> dict[str, Any]:
    """Run the generated project's demo entry point.

    Prefers ``make demo`` (what every blueprint ships), then
    ``python app/main.py``. ``--offline`` sets ``ACK_OFFLINE=1`` in the
    child environment so tools dispatch to their mocks (invariant 5).
    """
    env = dict(os.environ)
    if offline:
        env["ACK_OFFLINE"] = "1"

    makefile = root / "Makefile"
    argv: list[str] | None = None
    entry = "unknown"
    if makefile.is_file() and "demo:" in makefile.read_text(encoding="utf-8"):
        make = shutil.which("make")
        if make:
            argv = [make, "demo"]
            entry = "make demo"
    if argv is None and (root / "app" / "main.py").is_file():
        argv = [sys.executable, "app/main.py"]
        entry = "python app/main.py"
    if argv is None:
        raise AckError(
            "this project has no demo entry point",
            code="E110",
            why=(
                "a demo runs `make demo` or `app/main.py`; neither exists here. "
                "The blueprints that ship them are W-I."
            ),
            fix="ack sync   # re-render the blueprint, then: ack demo --offline",
            details={"root": str(root), "offline": offline, "pending": "W-I"},
        )

    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - argv built from a fixed set
            argv, cwd=root, capture_output=True, text=True, timeout=120, env=env, check=False
        )
    except subprocess.TimeoutExpired:
        raise AckError(
            "the demo did not finish within 120 seconds",
            code="E102",
            why="a demo that hangs is a demo that fails; agenticcarekit refuses to wait longer.",
            fix="ack demo --offline   # everything runs against mocks with networking disabled",
        ) from None
    output = (proc.stdout + proc.stderr).strip()
    return {
        "entry": entry,
        "offline": offline,
        "succeeded": proc.returncode == 0,
        "exit_code": proc.returncode,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "output": _tail(output, 40),
        "model": str(cfg.model_primary),
    }
