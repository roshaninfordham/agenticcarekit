"""W-K · the MCP server — "an agent with only MCP access", literally.

The acceptance criterion in brief §6 (W-K) is a scenario, not a unit: an agent
with no shell must be able to diagnose a machine, scaffold a project, describe
it, run its eval, choose a model and look up an error code. That scenario is
:func:`test_agent_with_only_mcp_access_can_drive_the_toolkit`, run end to end
against the real packaged blueprints, entirely offline.

Everything here calls the tool functions directly (``build_tools``) or through
the MCP server's own dispatcher (``call_tool``). No subprocess, no socket.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agenticcarekit.serve.mcp_server import TOOL_NAMES, build_mcp_server, build_tools

FIXTURES = Path(__file__).parent / "fixtures_cli"
MACHINES = FIXTURES / "machines"


@pytest.fixture(autouse=True)
def offline_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tool call runs against a recorded machine: offline, deterministic.

    ``ACK_MACHINE_FACTS`` is the injection seam ``ack doctor`` already uses, so
    this drives the real code path rather than a stub of it.
    """
    monkeypatch.setenv("ACK_MACHINE_FACTS", str(MACHINES / "mac-m3-e4b-pulled.json"))
    monkeypatch.setenv("ACK_OFFLINE", "1")


def envelope_ok(payload: dict) -> dict:
    """Assert the CLI envelope shape and return its ``data``."""
    assert set(payload) == {
        "envelope_version",
        "ok",
        "command",
        "version",
        "elapsed_ms",
        "data",
        "error",
    }
    assert payload["ok"] is True, payload["error"]
    assert payload["error"] is None
    return payload["data"]


# ── the acceptance scenario ──────────────────────────────────────────────


def test_agent_with_only_mcp_access_can_drive_the_toolkit(tmp_path: Path) -> None:
    """Diagnose → choose a model → scaffold → describe → eval → explain.

    No shell is involved at any step, and every destination is an explicit
    path under ``tmp_path``.
    """
    tools = build_tools(tmp_path)

    # 1. Read the machine before diagnosing anything.
    facts = envelope_ok(tools["doctor"](offline=True))
    assert facts["facts"]["os"] == "Darwin"
    assert facts["facts"]["ollama_daemon"] is True
    assert isinstance(facts["problems"], list)

    # 2. Pick a model by capability. Audio is E2B/E4B only (brief §2).
    audio = envelope_ok(tools["search_models"](modality="audio", offline=True))
    assert [m["tag"] for m in audio["models"]] == [
        "gemma4:e2b",
        "gemma4:e2b-mlx",
        "gemma4:e4b",
        "gemma4:e4b-mlx",
    ]
    assert any(m["already_pulled"] for m in audio["models"])

    # 3. Scaffold a real, packaged blueprint into an explicit directory.
    init = envelope_ok(tools["init_project"](path="clinic", blueprint="on-device"))
    project = tmp_path / "clinic"
    assert (project / "ack.toml").is_file()
    assert "AGENTS.md" in init["generated"]["files"]
    assert init["plan"]["blueprint"] == "on-device"
    assert init["rerun"].startswith("ack init --blueprint on-device")
    # Nothing was downloaded, and the tool says so rather than implying it did.
    assert init["pull"]["status"] == "skipped"

    # 4. Describe what was generated.
    manifest = envelope_ok(tools["get_manifest"](path="clinic"))
    assert manifest["project"]["name"] == "clinic"
    assert manifest["project"]["blueprint"] == "on-device"
    assert manifest["policy"]["egress"] == "device"

    # 5. Enable a capability — idempotently.
    added = envelope_ok(tools["add_capability"](capability="rag", path="clinic"))
    assert added["changed"] is True and "rag" in added["capabilities"]
    again = envelope_ok(tools["add_capability"](capability="rag", path="clinic"))
    assert again["changed"] is False

    # 6. Run the eval. The generated project ships no golden set yet, so the
    #    honest answer is E601 with a fix — not a fabricated score.
    result = tools["run_eval"](path="clinic")
    assert result["ok"] is False
    assert result["error"]["code"] == "E601"
    assert result["error"]["fix"]
    assert "exact_match_rate" not in json.dumps(result["error"])

    # 7. Look up any code that came back.
    explained = envelope_ok(tools["explain_error"](code="E601"))
    assert explained["code"] == "E601"
    assert explained["fix"] == result["error"]["fix"] or explained["fix"]


def test_explain_error_returns_the_registry_entry(tmp_path: Path) -> None:
    """``explain_error("E203")`` is the registry entry, verbatim."""
    tools = build_tools(tmp_path)
    data = envelope_ok(tools["explain_error"](code="E203"))
    assert data["code"] == "E203"
    assert data["title"] == "Model does not support a required input modality"
    assert data["what"] and data["why"] and data["fix"]

    listing = envelope_ok(tools["explain_error"]())
    assert {"code": "E301", "title": "Sensitive value blocked at egress boundary"} in listing[
        "codes"
    ]


def test_unknown_error_code_fails_with_a_registered_code(tmp_path: Path) -> None:
    """An unregistered code is E401 — never a silent empty answer."""
    result = build_tools(tmp_path)["explain_error"](code="E999")
    assert result["ok"] is False
    assert result["error"]["code"] == "E401"
    assert "E999" in result["error"]["message"]


def test_init_project_refuses_to_guess_a_destination(tmp_path: Path) -> None:
    """No path, no scaffold. The sidecar never writes into an implied cwd."""
    result = build_tools(tmp_path)["init_project"](path="")
    assert result["ok"] is False
    assert result["error"]["code"] == "E401"
    assert "path" in result["error"]["message"]


def test_search_models_filters_are_honest(tmp_path: Path) -> None:
    """Filters compose, and hosted entries stay flagged as unverified."""
    tools = build_tools(tmp_path)
    big = envelope_ok(tools["search_models"](min_context_tokens=262144, offline=True))
    assert all(m["context_tokens"] >= 262144 for m in big["models"])
    assert "gemma4:e2b" not in [m["tag"] for m in big["models"]]

    local = envelope_ok(tools["search_models"](include_hosted=False, offline=True))
    assert all(not m["hosted"] for m in local["models"])

    pulled = envelope_ok(tools["search_models"](already_pulled_only=True, offline=False))
    assert [m["tag"] for m in pulled["models"]] == ["gemma4:e4b-mlx"]

    bad = tools["search_models"](modality="video")
    assert bad["ok"] is False and bad["error"]["code"] == "E401"


def test_manifest_without_a_project_says_so(tmp_path: Path) -> None:
    """E404 with ``ack init`` as the fix, not a stack trace."""
    result = build_tools(tmp_path)["get_manifest"]()
    assert result["ok"] is False
    assert result["error"]["code"] == "E404"
    assert result["error"]["fix"] == "ack init"


# ── the MCP surface itself ───────────────────────────────────────────────


def test_the_server_exposes_exactly_the_seven_tools(tmp_path: Path) -> None:
    """The tool list is a product decision; assert it, don't drift it."""
    server = build_mcp_server(tmp_path)
    tools = asyncio.run(server.list_tools())
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)


def test_every_tool_carries_a_schema_and_a_docstring_an_agent_can_read(
    tmp_path: Path,
) -> None:
    """Typed JSON schema + the docstring the agent actually reads."""
    server = build_mcp_server(tmp_path)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in TOOL_NAMES:
        tool = tools[name]
        assert tool.description and len(tool.description) > 80, name
        assert tool.input_schema["type"] == "object", name

    search = tools["search_models"].input_schema["properties"]
    assert search["min_context_tokens"]["anyOf"][0]["type"] == "integer"
    assert search["include_hosted"]["type"] == "boolean"
    # init_project's path is required — the schema, not just the code, says so.
    assert tools["init_project"].input_schema["required"] == ["path"]


def test_calling_a_tool_through_the_server_returns_the_envelope(tmp_path: Path) -> None:
    """Dispatch through MCP itself, not just the underlying function."""
    server = build_mcp_server(tmp_path)
    result = asyncio.run(server.call_tool("explain_error", {"code": "E301"}))
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"]["code"] == "E301"


def test_a_failing_tool_returns_the_error_dict_not_an_exception(tmp_path: Path) -> None:
    """An AckError arrives as data an agent can branch on."""
    server = build_mcp_server(tmp_path)
    result = asyncio.run(server.call_tool("get_manifest", {}))
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E404"
    assert set(payload["error"]) == {"code", "message", "why", "fix", "details"}
