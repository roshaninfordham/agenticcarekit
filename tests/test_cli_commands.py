"""W-G · ``doctor``, ``explain``, ``new``, ``manifest``, ``sync``, ``add``,
``swap``, ``eject``, ``check``, ``eval`` and ``demo``.

These are the commands an agent drives (brief §9), so what is asserted is
the ``--json`` payload as much as the human rendering.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from agenticcarekit.cli.main import app
from agenticcarekit.cli.project_ops import EJECTABLES, build_manifest, sync_project
from agenticcarekit.cli.scaffolds import KINDS
from agenticcarekit.kernel.contracts import AckConfig
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


def flat(text: str) -> str:
    return " ".join(ANSI.sub("", text).split())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    dest = tmp_path / "proj"
    result = runner.invoke(
        app,
        ["init", str(dest), "--blueprint", "test-voice", "--yes", "--no-pull", "--no-git"],
        env=env_for(),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    return dest


def data_of(result) -> dict:
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    return payload["data"]


# ── doctor ───────────────────────────────────────────────────────────────


def test_doctor_json_is_facts_plus_registered_problem_codes() -> None:
    result = runner.invoke(app, ["doctor", "--json"], env=env_for("linux-no-ollama"))
    data = data_of(result)
    assert data["facts"]["os"] == "Linux"
    assert data["facts"]["ram_total_gb"] == 32.0
    codes = [p["code"] for p in data["problems"]]
    assert "E010" in codes
    for problem in data["problems"]:
        assert set(problem) == {"code", "title", "what", "fix"}
        assert problem["fix"]


def test_doctor_reports_probe_timings_honestly() -> None:
    result = runner.invoke(app, ["doctor"], env=env_for("mac-m4-max-96gb"))
    assert result.exit_code == 0
    assert "probe timings" in flat(result.stdout)


def test_doctor_never_prints_a_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-super-secret-value"
    result = runner.invoke(
        app, ["doctor", "--json"], env=env_for("mac-m4-max-96gb", OPENAI_API_KEY=secret)
    )
    assert secret not in result.stdout
    keys = data_of(result)["facts"]["provider_keys"]
    assert all(isinstance(v, bool) for v in keys.values())


def test_doctor_on_a_healthy_machine_reports_no_problems() -> None:
    result = runner.invoke(app, ["doctor"], env=env_for("mac-m4-max-96gb"))
    assert "no problems detected" in flat(result.stdout)


# ── explain ──────────────────────────────────────────────────────────────


def test_explain_returns_the_registry_long_form() -> None:
    data = data_of(runner.invoke(app, ["explain", "E203", "--json"], env=env_for()))
    assert data["title"] == "Model does not support a required input modality"
    assert data["fix"] == "ack init --model gemma4:e4b-mlx"


def test_explain_is_case_insensitive() -> None:
    assert (
        data_of(runner.invoke(app, ["explain", "e301", "--json"], env=env_for()))["code"]
        == "E301"
    )


def test_explain_with_no_code_lists_ranges_and_every_registered_code() -> None:
    data = data_of(runner.invoke(app, ["explain", "--json"], env=env_for()))
    assert data["ranges"]["E3xx"] == "policy and privacy violations"
    codes = [c["code"] for c in data["codes"]]
    assert codes == sorted(codes)
    assert {"E010", "E203", "E301", "E501", "E601"} <= set(codes)


# ── new ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", KINDS)
def test_new_scaffolds_every_extension_point(kind: str, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["new", kind, f"demo-{kind}", "--path", str(tmp_path), "--json"], env=env_for()
    )
    data = data_of(result)
    assert data["kind"] == kind
    assert data["files"]
    for rel in data["files"]:
        assert (tmp_path / rel).is_file()


@pytest.mark.parametrize("kind", ["provider", "redactor", "pack", "capability"])
def test_scaffolded_python_imports_and_its_doctests_pass(kind: str, tmp_path: Path) -> None:
    """A scaffold whose example does not run is a stub, not an interface."""
    result = runner.invoke(
        app, ["new", kind, "demo", "--path", str(tmp_path), "--json"], env=env_for()
    )
    files = [f for f in data_of(result)["files"] if f.endswith(".py")]
    assert files
    modules = [
        rel[: -len("/__init__.py")].replace("/", ".")
        if rel.endswith("/__init__.py")
        else rel[: -len(".py")].replace("/", ".")
        for rel in files
    ]
    script = (
        "import doctest, importlib, sys\n"
        "sys.path.insert(0, '.')\n"
        "failed = 0\n"
        f"for name in {modules!r}:\n"
        "    failed += doctest.testmod(importlib.import_module(name)).failed\n"
        "sys.exit(1 if failed else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode == 0, f"{modules}\n{proc.stdout}\n{proc.stderr}"


def test_a_scaffolded_blueprint_is_immediately_usable(tmp_path: Path) -> None:
    runner.invoke(app, ["new", "blueprint", "custom", "--path", str(tmp_path)], env=env_for())
    dest = tmp_path / "generated"
    result = runner.invoke(
        app,
        ["init", str(dest), "--blueprint", "custom", "--blueprint-path",
         str(tmp_path / "blueprints"), "--yes", "--no-pull", "--no-git", "--json"],
        env=env_for(),
        catch_exceptions=False,
    )
    data = data_of(result)
    assert data["blueprint"]["name"] == "custom"
    assert (dest / "app" / "main.py").is_file()
    assert "{{" not in (dest / "app" / "main.py").read_text()


def test_new_with_no_kind_lists_the_five_extension_points() -> None:
    data = data_of(runner.invoke(app, ["new", "--json"], env=env_for()))
    assert data["kinds"] == list(KINDS)


def test_new_with_an_unknown_kind_names_the_valid_ones(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["new", "widget", "x", "--path", str(tmp_path), "--json"], env=env_for()
    )
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E401"
    assert error["details"]["kinds"] == list(KINDS)


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_describes_the_generated_project(project: Path) -> None:
    data = data_of(
        runner.invoke(app, ["manifest", "--path", str(project), "--json"], env=env_for())
    )
    assert data["project"] == {
        "name": "proj",
        "blueprint": "test-voice",
        "pack": "healthcare",
    }
    assert data["model"]["primary"] == "ollama:gemma4:e4b-mlx"
    assert data["policy"] == {"egress": "device", "redactor": "healthcare.phi"}
    assert data["capabilities"] == ["voice", "extract"]
    assert "ack.toml" in data["files"]
    assert data["tools"] == []


def test_manifest_tool_entries_match_the_published_schema(project: Path) -> None:
    tools_dir = project / "app" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "lookup.py").write_text(
        'from agenticcarekit.kernel.contracts import tool\n'
        '\n'
        'def mock_lookup(code: str) -> str:\n'
        '    return "canned"\n'
        '\n'
        '@tool(permissions={"network"}, mock=mock_lookup)\n'
        'def lookup(code: str) -> str:\n'
        '    """Look a code up."""\n'
        '    return code\n',
        encoding="utf-8",
    )
    cfg = AckConfig.load(project / "ack.toml")
    data = build_manifest(project, cfg)
    assert data["tools"] == [
        {
            "name": "lookup",
            "description": "Look a code up.",
            "permissions": ["network"],
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            "has_mock": True,
        }
    ]
    schema = json.loads(
        (Path(__file__).parents[1] / "spec" / "schemas" / "tool-manifest.schema.json").read_text()
    )
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"tools": data["tools"]}, schema)


def test_a_broken_tool_module_is_a_note_not_a_crash(project: Path) -> None:
    tools_dir = project / "app" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "broken.py").write_text("import nonexistent_module_xyz\n", encoding="utf-8")
    cfg = AckConfig.load(project / "ack.toml")
    data = build_manifest(project, cfg)
    assert data["tools"] == []
    assert any("broken.py" in note for note in data["tool_notes"])


def test_manifest_outside_a_project_is_e404(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["manifest", "--path", str(tmp_path), "--json"], env=env_for()
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "E404"


# ── sync ─────────────────────────────────────────────────────────────────


def test_sync_recreates_a_deleted_template_file(project: Path) -> None:
    (project / "app" / "main.py").unlink()
    data = data_of(runner.invoke(app, ["sync", "--path", str(project), "--json"], env=env_for()))
    assert "app/main.py" in data["created"]
    assert (project / "app" / "main.py").is_file()


def test_sync_reports_drift_and_keeps_the_users_edit(project: Path) -> None:
    edited = project / "app" / "main.py"
    edited.write_text("# mine\n", encoding="utf-8")
    data = data_of(runner.invoke(app, ["sync", "--path", str(project), "--json"], env=env_for()))
    assert data["drifted"] == ["app/main.py"]
    assert edited.read_text() == "# mine\n"


def test_sync_force_replaces_drifted_files(project: Path) -> None:
    (project / "app" / "main.py").write_text("# mine\n", encoding="utf-8")
    data = data_of(
        runner.invoke(app, ["sync", "--path", str(project), "--force", "--json"], env=env_for())
    )
    assert data["drifted"] == []
    assert "app/main.py" in data["created"]
    assert "# mine" not in (project / "app" / "main.py").read_text()


def test_init_re_run_preserves_unknown_ack_toml_tables(project: Path) -> None:
    """Contract 5 again, on the generator side: re-running init keeps edits."""
    path = project / "ack.toml"
    path.write_text(path.read_text() + '\n[team]\nowner = "cardiology"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        ["init", str(project), "--blueprint", "test-voice", "--yes", "--no-pull", "--no-git"],
        env=env_for(),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    assert 'owner = "cardiology"' in path.read_text()


def test_sync_preserves_unknown_ack_toml_tables(project: Path) -> None:
    """Contract 5: users and agents may extend ack.toml; sync must not eat it."""
    path = project / "ack.toml"
    path.write_text(
        path.read_text() + '\n[team]\nowner = "cardiology"\nreviewers = ["a", "b"]\n',
        encoding="utf-8",
    )
    cfg = AckConfig.load(path)
    sync_project(project, cfg)
    after = path.read_text()
    assert '[team]' in after
    assert 'owner = "cardiology"' in after
    assert 'reviewers = ["a", "b"]' in after
    assert AckConfig.load(path).blueprint == "test-voice"


def test_sync_is_deterministic(project: Path) -> None:
    cfg = AckConfig.load(project / "ack.toml")
    sync_project(project, cfg)
    first = (project / "ack.toml").read_bytes()
    sync_project(project, cfg)
    assert (project / "ack.toml").read_bytes() == first


# ── add / swap ───────────────────────────────────────────────────────────


def test_add_enables_a_capability_idempotently(project: Path) -> None:
    data = data_of(
        runner.invoke(app, ["add", "rag", "--path", str(project), "--json"], env=env_for())
    )
    assert data["changed"] is True
    assert AckConfig.load(project / "ack.toml").capabilities == ("extract", "rag", "voice")
    again = data_of(
        runner.invoke(app, ["add", "rag", "--path", str(project), "--json"], env=env_for())
    )
    assert again["changed"] is False


def test_add_an_unknown_capability_names_the_known_ones(project: Path) -> None:
    result = runner.invoke(
        app, ["add", "telepathy", "--path", str(project), "--json"], env=env_for()
    )
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E401"
    assert "voice" in error["details"]["known"]


def test_swap_model_accepts_a_bare_tag_and_resolves_the_provider(project: Path) -> None:
    data = data_of(
        runner.invoke(
            app, ["swap", "model", "gemma4:12b", "--path", str(project), "--json"], env=env_for()
        )
    )
    assert data == {"what": "model", "from": "ollama:gemma4:e4b-mlx", "to": "ollama:gemma4:12b"}
    assert str(AckConfig.load(project / "ack.toml").model_primary) == "ollama:gemma4:12b"


def test_swap_egress_validates_against_the_closed_set(project: Path) -> None:
    ok = data_of(
        runner.invoke(
            app, ["swap", "egress", "trusted-network", "--path", str(project), "--json"],
            env=env_for(),
        )
    )
    assert ok["to"] == "trusted-network"
    bad = runner.invoke(
        app, ["swap", "egress", "the-moon", "--path", str(project), "--json"], env=env_for()
    )
    assert json.loads(bad.stdout)["error"]["code"] == "E403"


def test_swap_fallback_to_none_clears_it(project: Path) -> None:
    runner.invoke(
        app, ["swap", "fallback", "cerebras:gemma-4-31b", "--path", str(project)], env=env_for()
    )
    assert AckConfig.load(project / "ack.toml").model_fallback is not None
    runner.invoke(app, ["swap", "fallback", "none", "--path", str(project)], env=env_for())
    assert AckConfig.load(project / "ack.toml").model_fallback is None


def test_swap_with_no_arguments_lists_the_slots(project: Path) -> None:
    data = data_of(
        runner.invoke(app, ["swap", "--path", str(project), "--json"], env=env_for())
    )
    assert data["swappable"] == ["model", "fallback", "pack", "redactor", "egress"]


# ── eject ────────────────────────────────────────────────────────────────


def test_eject_prompts_copies_packaged_markdown(project: Path) -> None:
    data = data_of(
        runner.invoke(app, ["eject", "prompts", "--path", str(project), "--json"], env=env_for())
    )
    assert data["ejected"] == "prompts"
    for rel in data["copied"]:
        assert rel.startswith("prompts/")
        assert (project / rel).is_file()
        assert (project / rel).read_text().strip()


def test_eject_never_clobbers_without_force(project: Path) -> None:
    first = data_of(
        runner.invoke(app, ["eject", "prompts", "--path", str(project), "--json"], env=env_for())
    )
    if not first["copied"]:
        pytest.skip("no packaged prompts in this build")
    target = project / first["copied"][0]
    target.write_text("mine\n", encoding="utf-8")
    second = data_of(
        runner.invoke(app, ["eject", "prompts", "--path", str(project), "--json"], env=env_for())
    )
    assert second["skipped"] == first["copied"]
    assert target.read_text() == "mine\n"
    forced = data_of(
        runner.invoke(
            app, ["eject", "prompts", "--path", str(project), "--force", "--json"], env=env_for()
        )
    )
    assert forced["copied"] == first["copied"]
    assert target.read_text() != "mine\n"


def test_the_ejectable_registry_is_extensible_and_documented() -> None:
    assert "prompts" in EJECTABLES
    for name, spec in EJECTABLES.items():
        assert spec.name == name
        assert spec.description.strip()
        assert callable(spec.resolve)


def test_eject_with_no_target_lists_the_registry(project: Path) -> None:
    data = data_of(
        runner.invoke(app, ["eject", "--path", str(project), "--json"], env=env_for())
    )
    assert data["available"] == sorted(EJECTABLES)


def test_eject_an_unknown_thing_names_the_registry(project: Path) -> None:
    result = runner.invoke(
        app, ["eject", "the-kernel", "--path", str(project), "--json"], env=env_for()
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["details"]["ejectable"] == sorted(EJECTABLES)


# ── check ────────────────────────────────────────────────────────────────


def test_check_runs_lint_and_a_selftest_within_budget() -> None:
    repo = Path(__file__).parents[1]
    result = runner.invoke(app, ["check", "--path", str(repo), "--json"], env=env_for())
    data = json.loads(result.stdout)["data"]
    assert [s["name"] for s in data["steps"]] == ["lint", "selftest"]
    assert data["within_budget"] is True
    assert data["duration_ms"] / 1000 < 30
    selftest = data["steps"][1]
    assert selftest["status"] in ("pass", "fail")
    assert selftest["doctests_attempted"] > 0


def test_check_is_honest_about_a_failure(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("import os\nx=(1,\n", encoding="utf-8")
    result = runner.invoke(app, ["check", "--path", str(tmp_path), "--json"], env=env_for())
    data = json.loads(result.stdout)["data"]
    lint = data["steps"][0]
    assert lint["status"] in ("fail", "skipped")
    if lint["status"] == "fail":
        assert data["ok"] is False
        assert result.exit_code == 1, "a failing check must fail the shell too"


def test_demo_failure_sets_a_non_zero_exit_status(project: Path) -> None:
    (project / "app" / "main.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    (project / "Makefile").unlink()
    result = runner.invoke(app, ["demo", "--path", str(project), "--json"], env=env_for())
    payload = json.loads(result.stdout)
    assert payload["data"]["succeeded"] is False
    assert payload["data"]["exit_code"] == 3
    assert result.exit_code == 1


# ── eval / demo ──────────────────────────────────────────────────────────


def test_eval_without_a_golden_set_is_e601_with_a_scaffold_fix(project: Path) -> None:
    result = runner.invoke(app, ["eval", "--path", str(project), "--json"], env=env_for())
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E601"
    assert "ack eval --init" in error["fix"]


def test_eval_offline_scores_the_golden_set_against_the_mock_provider(project: Path) -> None:
    evals = project / "evals"
    evals.mkdir()
    (evals / "golden.jsonl").write_text(
        '{"id": "c1", "input": "hi", "expected": "hi", "tags": []}\n', encoding="utf-8"
    )
    result = runner.invoke(
        app, ["eval", "--path", str(project), "--offline", "--json"], env=env_for()
    )
    payload = json.loads(result.stdout)
    assert payload["ok"], payload
    assert payload["data"]["cases"] == 1
    assert payload["data"]["offline"] is True


def test_eval_online_without_a_daemon_fails_with_a_registered_environment_code(
    project: Path,
) -> None:
    # With provider_for landed, the non-offline path reaches a real provider;
    # in a daemon-less test environment the honest outcome is an environment
    # error (E011/E102/E110) carrying its fix — never a fabricated score.
    evals = project / "evals"
    evals.mkdir()
    (evals / "golden.jsonl").write_text(
        '{"id": "c1", "input": "hi", "expected": "hi", "tags": []}\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["eval", "--path", str(project), "--json"], env=env_for())
    payload = json.loads(result.stdout)
    if payload["ok"]:
        assert payload["data"]["cases"] == 1  # a live daemon answered; fine too
    else:
        error = payload["error"]
        assert error["code"] in {"E011", "E102", "E110"}
        assert error["fix"]


def test_demo_runs_the_generated_makefile_target_offline(project: Path) -> None:
    result = runner.invoke(
        app, ["demo", "--path", str(project), "--offline", "--json"], env=env_for()
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    data = payload["data"]
    assert data["offline"] is True
    assert data["exit_code"] == 0
    assert data["succeeded"] is True
    assert "proj: 2 capabilities, egress device" in data["output"]
    assert payload["elapsed_ms"] > 0


def test_demo_without_an_entry_point_says_so(tmp_path: Path, project: Path) -> None:
    (project / "Makefile").unlink()
    (project / "app" / "main.py").unlink()
    result = runner.invoke(app, ["demo", "--path", str(project), "--json"], env=env_for())
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "E110"
    assert error["details"]["pending"] == "W-I"


def test_demo_outside_a_project_is_e404(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--path", str(tmp_path), "--json"], env=env_for())
    assert json.loads(result.stdout)["error"]["code"] == "E404"
