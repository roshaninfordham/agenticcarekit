"""W-G · cross-cutting CLI contracts.

``--json`` on **every** command (invariant 10), the no-telemetry header,
``NO_COLOR``/``FORCE_COLOR``, and graceful degradation below 80 columns.

The ``--json`` test walks the typer app's command table programmatically,
so a command added without a ``--json`` flag fails here rather than in
somebody's pipeline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import typer.main
from agenticcarekit import __version__
from agenticcarekit.cli.main import app
from agenticcarekit.cli.output import Emitter, envelope, make_console
from typer.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures_cli"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


def base_env(**extra: str) -> dict[str, str]:
    env = {
        "ACK_BLUEPRINT_PATH": str(FIXTURES / "blueprints"),
        "ACK_MACHINE_FACTS": str(FIXTURES / "machines" / "linux-no-ollama.json"),
        "ACK_OFFLINE": "1",
        "NO_COLOR": "",
        "FORCE_COLOR": "",
        "COLUMNS": "100",
    }
    env.update(extra)
    return env


def command_names() -> list[str]:
    return sorted(typer.main.get_command(app).commands)  # type: ignore[attr-defined]


#: Extra arguments a command needs before ``--json`` produces its real
#: payload rather than a usage error. Anything omitted here is invoked with
#: ``--json`` alone and must still emit a parseable envelope.
EXTRA_ARGS: dict[str, list[str]] = {
    "init": ["--blueprint", "test-voice", "--yes", "--no-pull", "--no-git"],
    "explain": ["E203"],
    "new": ["provider", "smoke-provider"],
}


def test_every_command_is_covered() -> None:
    assert command_names() == [
        "add",
        "check",
        "demo",
        "doctor",
        "eject",
        "eval",
        "explain",
        "init",
        "manifest",
        "new",
        "swap",
        "sync",
    ]


@pytest.mark.parametrize("name", command_names())
def test_json_parses_on_every_command(name: str, tmp_path: Path) -> None:
    args = [name, *EXTRA_ARGS.get(name, []), "--json"]
    result = runner.invoke(app, args, env=base_env(), catch_exceptions=False)
    payload = json.loads(result.stdout)
    assert payload["command"] == name
    assert payload["version"] == __version__
    assert payload["envelope_version"] == 1
    assert isinstance(payload["ok"], bool)
    assert (payload["error"] is None) == payload["ok"]
    # Machine output carries no rich markup and no ANSI.
    assert "[bold]" not in result.stdout
    assert not ANSI.search(result.stdout)


@pytest.mark.parametrize("name", command_names())
def test_help_works_for_every_command(name: str) -> None:
    result = runner.invoke(app, [name, "--help"], env=base_env())
    assert result.exit_code == 0
    assert "--json" in result.stdout


def test_json_output_is_the_only_thing_on_stdout(tmp_path: Path) -> None:
    result = runner.invoke(app, ["explain", "E203", "--json"], env=base_env())
    assert result.stdout.strip().startswith("{")
    assert result.stdout.strip().endswith("}")
    assert json.loads(result.stdout)["data"]["code"] == "E203"


def test_header_states_no_telemetry_on_human_runs() -> None:
    result = runner.invoke(app, ["explain", "E203"], env=base_env())
    first = result.stdout.splitlines()[0]
    assert "agenticcarekit" in first
    assert __version__ in first
    assert "No telemetry, ever." in first


def test_header_is_absent_under_json() -> None:
    result = runner.invoke(app, ["explain", "E203", "--json"], env=base_env())
    assert "No telemetry" not in result.stdout


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"agenticcarekit {__version__}"


# ── colour ───────────────────────────────────────────────────────────────


def test_force_color_produces_ansi() -> None:
    result = runner.invoke(app, ["explain", "E203"], env=base_env(FORCE_COLOR="1"))
    assert ANSI.search(result.stdout), "FORCE_COLOR=1 should colour the output"


def test_no_color_beats_force_color() -> None:
    result = runner.invoke(
        app, ["explain", "E203"], env=base_env(NO_COLOR="1", FORCE_COLOR="1")
    )
    assert not ANSI.search(result.stdout)


def test_make_console_reads_the_colour_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert make_console().no_color is True
    monkeypatch.delenv("NO_COLOR")
    assert make_console().no_color is False


# ── width ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("columns", ["40", "60", "79", "80", "120"])
def test_output_never_exceeds_the_terminal_width(columns: str) -> None:
    result = runner.invoke(app, ["explain", "E203"], env=base_env(COLUMNS=columns))
    assert result.exit_code == 0
    for line in ANSI.sub("", result.stdout).splitlines():
        assert len(line) <= int(columns), f"{len(line)} > {columns}: {line!r}"


def test_narrow_layout_switches_below_80_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    assert Emitter("t", json_mode=True).narrow is True
    monkeypatch.setenv("COLUMNS", "100")
    assert Emitter("t", json_mode=True).narrow is False


# ── envelope ─────────────────────────────────────────────────────────────


def test_envelope_shape_is_stable() -> None:
    env = envelope("doctor", data={"a": 1})
    assert sorted(env) == [
        "command",
        "data",
        "elapsed_ms",
        "envelope_version",
        "error",
        "ok",
        "version",
    ]


def test_error_envelope_carries_the_registered_code() -> None:
    result = runner.invoke(app, ["explain", "E999", "--json"], env=base_env())
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E401"
    assert "E203" in payload["error"]["details"]["known"]


def test_unknown_error_code_lists_the_ranges_for_humans() -> None:
    result = runner.invoke(app, ["explain", "E999"], env=base_env())
    assert result.exit_code == 1
    assert "E2xx" in result.stdout
    assert "ack explain E401" in result.stdout


def test_elapsed_time_is_printed_on_init(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice", "--yes",
         "--no-pull", "--no-git"],
        env=base_env(),
    )
    assert result.exit_code == 0, result.stdout
    assert re.search(r"done in \d+\.\d\ds", result.stdout)
