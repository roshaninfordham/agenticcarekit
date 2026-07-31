"""Per-blueprint content checks for W-I (Blueprints).

Complements ``test_blueprints_templates.py`` (the generic structural
sweep) with checks specific to what each blueprint's brief section (docs/
brief.md §6 W-I) says it must generate. Still template-content level:
string/AST inspection only, no import of the pinned runtime API.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BLUEPRINTS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "agenticcarekit" / "blueprints"


def read(*parts: str) -> str:
    return (BLUEPRINTS_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def top_level_imports(source: str) -> set[str]:
    """Every dotted module path named in an import/import-from statement."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ─────────────────────────────── voice-care ───────────────────────────────


def test_voice_care_main_wires_voice_loop_with_mocks_and_a_real_seam() -> None:
    text = read("voice-care", "templates", "app", "main.py")
    imports = top_level_imports(text)
    assert "agenticcarekit.capabilities.voice" in imports
    for name in ("VoiceLoop", "MockASR", "MockTTS"):
        assert name in text
    # A real ASR/TTS/provider seam must be visible, not just mocks.
    assert "seam" in text.lower()


def test_voice_care_scribe_defines_intake_note_and_uses_extract() -> None:
    text = read("voice-care", "templates", "app", "scribe.py")
    tree = ast.parse(text)
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "IntakeNote" in class_names

    imports = top_level_imports(text)
    assert "agenticcarekit.capabilities.extract" in imports
    assert "extract" in text


def test_voice_care_has_example_tool_with_mock() -> None:
    text = read("voice-care", "templates", "app", "tools", "__init__.py")
    assert "@tool(" in text
    assert "mock=" in text


def test_voice_care_makefile_demo_runs_offline_module() -> None:
    text = read("voice-care", "templates", "Makefile")
    assert "python3 -m app.main --offline" in text


def test_voice_care_has_synthetic_transcript_fixtures() -> None:
    text = read("voice-care", "templates", "app", "fixtures", "sample_transcripts.py")
    assert "synthetic" in text.lower()
    assert "SAMPLE_TRANSCRIPTS" in text


# ─────────────────────────────── care-copilot ─────────────────────────────


REQUIRED_TOOLS = {
    "check_eligibility": "eligibility.py",
    "draft_prior_auth": "prior_auth.py",
    "find_referral_slots": "referrals.py",
    "schedule_appointment": "scheduling.py",
}


@pytest.mark.parametrize("tool_name,filename", list(REQUIRED_TOOLS.items()))
def test_care_copilot_tool_present_with_mock(tool_name: str, filename: str) -> None:
    text = read("care-copilot", "templates", "app", "tools", filename)
    tree = ast.parse(text)
    func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert tool_name in func_names, f"{filename} does not define {tool_name}"
    assert f"mock_{tool_name}" in func_names, f"{filename} does not define a mock for {tool_name}"
    assert "@tool(" in text
    assert "mock=" in text


def test_care_copilot_prior_auth_never_submits() -> None:
    text = read("care-copilot", "templates", "app", "tools", "prior_auth.py").lower()
    assert "draft" in text
    assert "never submit" in text or "not submitted" in text or "no submission path" in text
    # There must be no evidence of a submission call in this template.
    assert "submit_prior_auth" not in text
    assert "requests.post" not in text
    assert "httpx.post" not in text


def test_care_copilot_main_wires_agent_loop_with_all_tools() -> None:
    text = read("care-copilot", "templates", "app", "main.py")
    imports = top_level_imports(text)
    assert "agenticcarekit.capabilities.agents" in imports
    assert "AgentLoop" in text
    for tool_name in REQUIRED_TOOLS:
        assert tool_name in read("care-copilot", "templates", "app", "tools", "__init__.py")


def test_care_copilot_makefile_demo_runs_offline_module() -> None:
    text = read("care-copilot", "templates", "Makefile")
    assert "python3 -m app.main --offline" in text


# ──────────────────────────────── on-device ───────────────────────────────


def test_on_device_main_uses_sensitive_policy_device_and_tracer() -> None:
    text = read("on-device", "templates", "app", "main.py")
    imports = top_level_imports(text)
    assert "agenticcarekit.kernel.policy" in imports
    assert "agenticcarekit.kernel.trace" in imports
    assert "Sensitive(" in text
    assert "Policy(egress=EgressClass.DEVICE)" in text
    assert "JsonlSink" in text
    assert "Tracer(" in text


def test_on_device_renders_zero_bytes_egressed_panel() -> None:
    text = read("on-device", "templates", "app", "main.py")
    assert "bytes_egressed(" in text
    assert "0 bytes egressed" in text
    # The honest-failure branch must exist too — not just the happy path.
    assert "bytes egressed" in text.lower()
    tree = ast.parse(text)
    has_if_else_on_egress = any(
        isinstance(n, ast.If) and "egressed" in ast.dump(n)
        for n in ast.walk(tree)
    )
    assert has_if_else_on_egress, "expected an if/else branching on the egress total"


def test_on_device_makefile_demo_has_no_online_mode() -> None:
    text = read("on-device", "templates", "Makefile")
    assert "python3 -m app.main" in text
    # This blueprint has no network mode to opt out of, so no --offline flag needed.
    assert "--offline" not in text
