"""W-K · every public example in ``serve/`` actually runs.

"Every public function has a docstring with a runnable example" (brief §9) is
only true if something runs them. Retrieval surfaces these examples to agents;
an example that does not run is worse than none.
"""

from __future__ import annotations

import doctest
import importlib
from pathlib import Path

import pytest

MODULES = [
    "agenticcarekit.serve",
    "agenticcarekit.serve.auth",
    "agenticcarekit.serve.trace",
    "agenticcarekit.serve.ops",
    "agenticcarekit.serve.app",
    "agenticcarekit.serve.mcp_server",
    "agenticcarekit.serve.runner",
]

MACHINES = Path(__file__).parent / "fixtures_cli" / "machines"


@pytest.mark.parametrize("name", MODULES)
def test_module_doctests(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run one module's doctests against a recorded machine, offline."""
    monkeypatch.setenv("ACK_MACHINE_FACTS", str(MACHINES / "mac-m3-e4b-pulled.json"))
    monkeypatch.setenv("ACK_OFFLINE", "1")
    result = doctest.testmod(importlib.import_module(name), verbose=False)
    assert result.failed == 0, f"{name}: {result.failed} of {result.attempted} examples failed"
    assert result.attempted > 0, f"{name} has no runnable examples"
