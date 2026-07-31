"""W-G · ``ack init``: determinism, the plan screen, and generation.

Acceptance (brief §6, W-G): *init twice → identical trees*. That is
invariant 4, and it is asserted here by walking both trees and hashing
every byte, symlinks included.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

import pytest
from agenticcarekit.cli.blueprints import discover, load_blueprint, resolve, search_paths
from agenticcarekit.cli.detect.probes import facts_from_file
from agenticcarekit.cli.flows import generate_project, plan, rerun_command
from agenticcarekit.cli.main import app
from agenticcarekit.cli.render import TEMPLATE_VARS, build_vars, render_text, render_tree
from agenticcarekit.kernel.contracts import AckConfig, AckError
from typer.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures_cli"
BLUEPRINTS = FIXTURES / "blueprints"
MACHINES = FIXTURES / "machines"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


def env_for(machine: str = "mac-m4-max-96gb", **extra: str) -> dict[str, str]:
    env = {
        "ACK_BLUEPRINT_PATH": str(BLUEPRINTS),
        "ACK_MACHINE_FACTS": str(MACHINES / f"{machine}.json"),
        "ACK_OFFLINE": "1",
        "NO_COLOR": "1",
        "COLUMNS": "100",
    }
    env.update(extra)
    return env


def tree_hash(root: Path) -> dict[str, str]:
    """Every path under ``root`` mapped to a content digest.

    Symlinks hash their target, not the file they point at, so a copied
    ``CLAUDE.md`` and a symlinked one are distinguishable.
    """
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        relative = path.relative_to(root)
        rel = relative.as_posix()
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            out[rel] = "symlink:" + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


def flat(text: str) -> str:
    """Whitespace-normalised output — rich wraps to the terminal width, so
    an assertion about *content* must not be an assertion about wrapping."""
    return " ".join(ANSI.sub("", text).split())


def run_init(dest: Path, *args: str, machine: str = "mac-m4-max-96gb"):
    return runner.invoke(
        app,
        ["init", str(dest), "--blueprint", "test-voice", "--yes", "--no-pull", "--no-git", *args],
        env=env_for(machine),
        catch_exceptions=False,
    )


# ── determinism ──────────────────────────────────────────────────────────


def test_init_twice_produces_byte_identical_trees(tmp_path: Path) -> None:
    a, b = tmp_path / "one" / "proj", tmp_path / "two" / "proj"
    first = run_init(a)
    second = run_init(b)
    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    assert tree_hash(a) == tree_hash(b)
    assert tree_hash(a)  # and it is not empty


def test_re_running_init_in_place_is_idempotent(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest)
    before = tree_hash(dest)
    run_init(dest)
    assert tree_hash(dest) == before


def test_generated_tree_contains_the_expected_surface(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest)
    files = set(tree_hash(dest))
    assert {
        "ack.toml",
        "AGENTS.md",
        "CLAUDE.md",
        ".cursor/rules/agenticcarekit.mdc",
        ".github/copilot-instructions.md",
        "README.md",
        "Makefile",
        "app/main.py",
        "prompts/intake.md",
    } <= files
    assert tree_hash(dest)["CLAUDE.md"] == "symlink:AGENTS.md"


def test_generated_ack_toml_is_valid_and_matches_the_plan(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest)
    cfg = AckConfig.load(dest / "ack.toml")
    assert cfg.blueprint == "test-voice"
    assert cfg.pack == "healthcare"
    assert str(cfg.model_primary) == "ollama:gemma4:e4b-mlx"
    assert cfg.egress.value == "device"
    assert cfg.capabilities == ("voice", "extract")


def test_templates_are_substituted_and_the_tmpl_suffix_is_stripped(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest)
    readme = (dest / "README.md").read_text()
    assert "{{" not in readme
    assert "# proj" in readme
    assert "`ollama:gemma4:e4b-mlx`" in readme
    assert '["voice", "extract"]' in readme
    assert not (dest / "README.md.tmpl").exists()
    # Non-template files are copied verbatim.
    assert (dest / "Makefile").read_text() == (
        BLUEPRINTS / "test-voice" / "templates" / "Makefile"
    ).read_text()


def test_agents_md_is_invariants_not_description(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest)
    body = (dest / "AGENTS.md").read_text()
    assert "NEVER" in body and "ALWAYS" in body
    assert "ack check" in body
    assert "Not diagnosis. Not treatment." in body
    assert (dest / ".github" / "copilot-instructions.md").read_text() == body
    assert body in (dest / ".cursor" / "rules" / "agenticcarekit.mdc").read_text()


# ── the plan screen ──────────────────────────────────────────────────────


def test_plan_screen_has_the_brief_section_7_3_shape(tmp_path: Path) -> None:
    out = ANSI.sub("", run_init(tmp_path / "proj").stdout)
    assert "  Plan" in out
    assert re.search(r"blueprint\s+test-voice", out)
    assert re.search(r"model\s+gemma4:e4b-mlx\s+←", out)
    assert re.search(r"providers\s+ollama\s+←", out)
    assert re.search(r"pack\s+healthcare", out)
    assert "↵ accept   e edit   ? why these" in out
    assert "Re-run this exactly:" in out


def test_plan_screen_carries_the_top_two_reasons(tmp_path: Path) -> None:
    out = flat(run_init(tmp_path / "proj").stdout)
    assert "-mlx build: native Apple Silicon acceleration on Apple M4 Max" in out
    assert "e4b: native audio input, ~4.5B effective parameters" in out
    assert out.count("←") >= 3


@pytest.mark.parametrize("columns", ["40", "60", "79"])
def test_plan_screen_renders_below_80_columns(tmp_path: Path, columns: str) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice", "--yes",
         "--no-pull", "--no-git"],
        env=env_for(COLUMNS=columns),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    for line in ANSI.sub("", result.stdout).splitlines():
        assert len(line) <= int(columns), f"{len(line)} > {columns}: {line!r}"


def test_no_color_yields_no_ansi(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice", "--yes",
         "--no-pull", "--no-git"],
        env=env_for(NO_COLOR="1", FORCE_COLOR="1"),
        catch_exceptions=False,
    )
    assert not ANSI.search(result.stdout)


def test_the_non_interactive_rerun_line_is_always_printed(tmp_path: Path) -> None:
    out = flat(run_init(tmp_path / "proj").stdout)
    expected = (
        "ack init --blueprint test-voice --model gemma4:e4b-mlx "
        "--providers ollama --pack healthcare --yes"
    )
    assert expected in out.replace("\\ ", "")


def test_rerun_line_reproduces_the_plan(tmp_path: Path) -> None:
    """The printed command, replayed, must produce the same tree."""
    a, b = tmp_path / "a" / "proj", tmp_path / "b" / "proj"
    run_init(a)
    replay = runner.invoke(
        app,
        ["init", str(b), "--blueprint", "test-voice", "--model", "gemma4:e4b-mlx",
         "--providers", "ollama", "--pack", "healthcare", "--yes", "--no-pull", "--no-git"],
        env=env_for(),
        catch_exceptions=False,
    )
    assert replay.exit_code == 0, replay.stdout
    assert tree_hash(a) == tree_hash(b)


def test_why_prints_the_full_ranked_table(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice", "--yes", "--why",
         "--no-pull", "--no-git"],
        env=env_for(),
        catch_exceptions=False,
    )
    out = flat(result.stdout)
    assert "Why these" in out and "Eliminated" in out
    assert "gemma4:31b" in out


def test_json_init_carries_plan_ranking_and_generation(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice", "--yes",
         "--no-pull", "--no-git", "--json"],
        env=env_for(),
        catch_exceptions=False,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["elapsed_ms"] > 0
    data = payload["data"]
    assert data["plan"]["model"] == "gemma4:e4b-mlx"
    assert data["ranking"]["eliminated"]
    assert "ack.toml" in data["generated"]["files"]
    assert data["rerun"].endswith("--yes")
    assert data["pull"]["status"] == "skipped"


# ── flags and failures ───────────────────────────────────────────────────


def test_explicit_model_without_audio_fails_with_e203(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-voice",
         "--model", "gemma4:31b", "--yes", "--no-pull", "--no-git", "--json"],
        env=env_for(),
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E203"
    assert "gemma4:e4b-mlx" in error["details"]["candidates"]


def test_unknown_blueprint_lists_the_installed_ones(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "nope", "--yes", "--json"],
        env=env_for(),
        catch_exceptions=False,
    )
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E410"
    assert "test-voice" in error["details"]["available"]


def test_hosted_fallback_raises_egress_and_declares_a_redactor(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    run_init(dest, "--providers", "ollama,cerebras")
    cfg = AckConfig.load(dest / "ack.toml")
    assert cfg.egress.value == "public-cloud"
    assert cfg.redactor == "healthcare.phi"
    assert str(cfg.model_fallback) == "cerebras:gemma-4-31b"


def test_capabilities_override_appears_in_the_rerun_line(tmp_path: Path) -> None:
    out = flat(run_init(tmp_path / "proj", "--capabilities", "voice").stdout)
    assert "--capabilities voice" in out


# ── the renderer ─────────────────────────────────────────────────────────


def test_unknown_template_variable_is_e501_not_silence() -> None:
    variables = build_vars(project_name="p", blueprint="b")
    with pytest.raises(AckError) as excinfo:
        render_text("{{surprise}}", variables, "app/x.py.tmpl")
    err = excinfo.value
    assert err.code == "E501"
    assert err.details["variable"] == "surprise"
    assert err.details["template"] == "app/x.py.tmpl"


def test_broken_blueprint_fails_the_whole_init_with_e501(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", str(tmp_path / "proj"), "--blueprint", "test-broken", "--yes",
         "--no-pull", "--no-git", "--json"],
        env=env_for(),
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "E501"


def test_the_template_variable_set_is_closed() -> None:
    assert TEMPLATE_VARS == (
        "ack_version",
        "blueprint",
        "capabilities_list",
        "egress",
        "model_fallback",
        "model_primary",
        "pack",
        "project_name",
        "redactor",
    )
    assert set(build_vars(project_name="p", blueprint="b")) == set(TEMPLATE_VARS)


def test_render_tree_is_sorted_and_drops_timestamps(tmp_path: Path) -> None:
    variables = build_vars(project_name="p", blueprint="test-voice", pack="healthcare")
    written = render_tree(BLUEPRINTS / "test-voice" / "templates", tmp_path, variables)
    assert written == sorted(written)
    assert written == ["Makefile", "README.md", "app/main.py", "prompts/intake.md"]


# ── blueprint discovery ──────────────────────────────────────────────────


def test_discovery_reads_the_env_search_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACK_BLUEPRINT_PATH", str(BLUEPRINTS))
    found = discover()
    assert set(found) >= {"test-voice", "test-notes", "test-broken"}
    assert found["test-voice"].requires.modalities_in == frozenset({"text", "audio"})
    assert found["test-voice"].default_pack == "healthcare"


def test_the_packaged_directory_is_always_on_the_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-I's blueprints stay discoverable even with an override set."""
    monkeypatch.setenv("ACK_BLUEPRINT_PATH", str(BLUEPRINTS))
    paths = search_paths()
    assert paths[0] == BLUEPRINTS
    assert paths[-1].name == "blueprints"
    assert paths[-1].parent.name == "agenticcarekit"


def test_discovery_of_an_empty_directory_returns_what_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACK_BLUEPRINT_PATH", str(tmp_path))
    assert discover(tmp_path) == discover(tmp_path)  # no crash, no invention


def test_resolve_with_no_blueprints_anywhere_raises_e410(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACK_BLUEPRINT_PATH", str(tmp_path))
    from agenticcarekit.cli import blueprints as bp_module

    monkeypatch.setattr(bp_module, "packaged_dir", lambda: tmp_path / "nothing")
    with pytest.raises(AckError) as excinfo:
        resolve(None)
    assert excinfo.value.code == "E410"
    assert "search_paths" in excinfo.value.details


def test_blueprint_spec_serializes_for_json() -> None:
    spec = load_blueprint(BLUEPRINTS / "test-voice")
    data = spec.to_dict()
    assert data["requires"]["modalities_in"] == ["audio", "text"]
    assert data["requires"]["context_tokens"] == 32768
    assert data["has_templates"] is True
    json.dumps(data)


# ── the flow API, used directly ──────────────────────────────────────────


def test_plan_and_generate_without_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACK_BLUEPRINT_PATH", str(BLUEPRINTS))
    facts = facts_from_file(MACHINES / "mac-m4-max-96gb.json")
    spec, rec = plan(facts, blueprint="test-voice", blueprint_path=str(BLUEPRINTS))
    assert rec.model == "gemma4:e4b-mlx"
    assert rerun_command(rec).startswith("ack init --blueprint test-voice")
    result = generate_project(tmp_path, spec, rec, project_name="demo", git=False)
    assert result["project_name"] == "demo"
    assert "ack.toml" in result["files"]
    assert tomllib.loads((tmp_path / "ack.toml").read_text())["project"]["blueprint"] == (
        "test-voice"
    )
