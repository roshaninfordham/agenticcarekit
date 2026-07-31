"""Template-content acceptance tests for W-I (Blueprints).

These tests operate at the *template* level only: blueprint.toml shape,
the {{var}} no-drift guarantee the renderer (W-G) relies on, presence of
required files, that Python template files compile, and that the
decision-support scope statement is present everywhere it must be. The
end-to-end ``make demo`` run (against the real renderer and runtime)
happens in integration, once W-G/W-D/W-E/W-F land — it is intentionally
out of scope here.

Nothing here imports from ``agenticcarekit.kernel`` or
``agenticcarekit.capabilities`` — those land in parallel workstreams.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

BLUEPRINTS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "agenticcarekit" / "blueprints"

BLUEPRINT_NAMES = ["voice-care", "care-copilot", "on-device"]

# Renderer (W-G) substitutes exactly these vars — CONTRACTS.md, "Blueprint
# layout (binds W-G and W-I)". Anything else in a .tmpl file is E501.
ALLOWED_VARS = {
    "project_name",
    "blueprint",
    "pack",
    "model_primary",
    "model_fallback",
    "egress",
    "redactor",
    "capabilities_list",
    "ack_version",
}

SCOPE_COMMENT = "# Decision support only — not diagnosis, not treatment. Synthetic/public data only."
SCOPE_STATEMENT_MARKERS = ("Decision support only", "not diagnosis", "not treatment")

VAR_RE = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")

# Requirements a blueprint.toml must declare, per CONTRACTS.md.
REQUIRED_TOP_KEYS = {"blueprint", "requires", "defaults"}
REQUIRED_BLUEPRINT_KEYS = {"name", "description", "track"}
REQUIRED_REQUIRES_KEYS = {"modalities_in", "tool_calling", "context_tokens"}
REQUIRED_DEFAULTS_KEYS = {"capabilities", "pack"}


def blueprint_dir(name: str) -> Path:
    return BLUEPRINTS_ROOT / name


def all_files(root: Path) -> list[Path]:
    """Every regular file under ``root``, excluding bytecode caches —
    ``__pycache__``/``.pyc`` are build byproducts, never part of a
    blueprint's own template tree (and are excluded by its own
    ``.gitignore``)."""
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


# ─────────────────────────── (a) blueprint.toml ──────────────────────────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_blueprint_toml_exists_and_parses(name: str) -> None:
    toml_path = blueprint_dir(name) / "blueprint.toml"
    assert toml_path.is_file(), f"missing {toml_path}"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert REQUIRED_TOP_KEYS <= data.keys(), f"{name}: missing top-level keys"

    bp = data["blueprint"]
    assert REQUIRED_BLUEPRINT_KEYS <= bp.keys(), f"{name}: [blueprint] missing keys"
    assert bp["name"] == name

    requires = data["requires"]
    assert REQUIRED_REQUIRES_KEYS <= requires.keys(), f"{name}: [requires] missing keys"
    assert isinstance(requires["modalities_in"], list)
    assert isinstance(requires["tool_calling"], bool)
    assert isinstance(requires["context_tokens"], int)

    defaults = data["defaults"]
    assert REQUIRED_DEFAULTS_KEYS <= defaults.keys(), f"{name}: [defaults] missing keys"
    assert isinstance(defaults["capabilities"], list)
    assert isinstance(defaults["pack"], str)


@pytest.mark.parametrize(
    "name,modalities_in,tool_calling,context_tokens",
    [
        ("voice-care", {"text", "audio"}, True, 32768),
        ("care-copilot", {"text"}, True, 65536),
        ("on-device", {"text"}, False, 8192),
    ],
)
def test_blueprint_toml_matches_brief_requirements(
    name: str, modalities_in: set[str], tool_calling: bool, context_tokens: int
) -> None:
    data = tomllib.loads((blueprint_dir(name) / "blueprint.toml").read_text(encoding="utf-8"))
    requires = data["requires"]
    assert set(requires["modalities_in"]) == modalities_in
    assert requires["tool_calling"] is tool_calling
    assert requires["context_tokens"] == context_tokens


# ───────────────────────── (b) {{var}} no-drift sweep ─────────────────────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_every_tmpl_var_is_allowed(name: str) -> None:
    templates_dir = blueprint_dir(name) / "templates"
    tmpl_files = sorted(templates_dir.rglob("*.tmpl"))
    assert tmpl_files, f"{name}: expected at least one .tmpl file"

    offenders: list[str] = []
    for f in tmpl_files:
        text = f.read_text(encoding="utf-8")
        for match in VAR_RE.finditer(text):
            var = match.group(1)
            if var not in ALLOWED_VARS:
                offenders.append(f"{f.relative_to(BLUEPRINTS_ROOT)}: {{{{{var}}}}}")
    assert not offenders, "unknown {{var}} occurrences (would be E501 at render time):\n" + "\n".join(
        offenders
    )


# ───────────────── (c) non-.tmpl files contain no {{ at all ───────────────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_non_tmpl_files_have_no_braces(name: str) -> None:
    templates_dir = blueprint_dir(name) / "templates"
    offenders: list[str] = []
    for f in all_files(templates_dir):
        if f.suffix == ".tmpl":
            continue
        text = f.read_text(encoding="utf-8")
        if "{{" in text:
            offenders.append(str(f.relative_to(BLUEPRINTS_ROOT)))
    assert not offenders, "non-.tmpl files must not contain {{ (no drift):\n" + "\n".join(offenders)


# ───────── (d) Makefile w/ demo target, README w/ scope, prompts/*.md ─────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_templates_tree_has_required_files(name: str) -> None:
    templates_dir = blueprint_dir(name) / "templates"

    makefile = templates_dir / "Makefile"
    assert makefile.is_file(), f"{name}: missing templates/Makefile"
    makefile_text = makefile.read_text(encoding="utf-8")
    assert re.search(r"^demo:", makefile_text, re.MULTILINE), f"{name}: Makefile missing 'demo' target"

    readme_candidates = [templates_dir / "README.md", templates_dir / "README.md.tmpl"]
    readmes = [p for p in readme_candidates if p.is_file()]
    assert readmes, f"{name}: templates/ missing a README (README.md or README.md.tmpl)"
    readme_text = readmes[0].read_text(encoding="utf-8")
    for marker in SCOPE_STATEMENT_MARKERS:
        assert marker in readme_text, f"{name}: templates README missing scope marker {marker!r}"

    prompts_dir = templates_dir / "app" / "prompts"
    assert prompts_dir.is_dir(), f"{name}: missing templates/app/prompts/"
    prompt_files = sorted(prompts_dir.glob("*.md"))
    assert prompt_files, f"{name}: templates/app/prompts/ has no *.md files"


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_blueprint_level_readme_states_scope(name: str) -> None:
    readme = blueprint_dir(name) / "README.md"
    assert readme.is_file(), f"{name}: missing top-level README.md"
    text = readme.read_text(encoding="utf-8")
    for marker in SCOPE_STATEMENT_MARKERS:
        assert marker in text, f"{name}: top-level README missing scope marker {marker!r}"
    assert "ejectable" in text.lower(), f"{name}: top-level README missing ejectable framing"


# ─────────────── (e) all Python template files compile ───────────────────


def _dummy_vars() -> dict[str, str]:
    return {
        "project_name": "demo_project",
        "blueprint": "demo-blueprint",
        "pack": "healthcare",
        "model_primary": "ollama:gemma4:e4b-mlx",
        "model_fallback": "cerebras:gemma-4-31b",
        "egress": "device",
        "redactor": "healthcare.phi",
        "capabilities_list": "voice, extract",
        "ack_version": "0.1.0",
    }


def render_tmpl(text: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return variables[match.group(1)]

    return VAR_RE.sub(repl, text)


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_all_python_template_files_compile(name: str) -> None:
    templates_dir = blueprint_dir(name) / "templates"
    py_files = [f for f in all_files(templates_dir) if f.name.endswith(".py") or f.name.endswith(".py.tmpl")]
    assert py_files, f"{name}: expected at least one Python template file"

    for f in py_files:
        text = f.read_text(encoding="utf-8")
        if f.name.endswith(".tmpl"):
            text = render_tmpl(text, _dummy_vars())
        compile(text, str(f), "exec")


# ────────────────── (f) scope comment in every generated .py ──────────────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_scope_comment_in_every_python_file(name: str) -> None:
    templates_dir = blueprint_dir(name) / "templates"
    py_files = [f for f in all_files(templates_dir) if f.name.endswith(".py") or f.name.endswith(".py.tmpl")]
    offenders = []
    for f in py_files:
        text = f.read_text(encoding="utf-8")
        if SCOPE_COMMENT not in text:
            offenders.append(str(f.relative_to(BLUEPRINTS_ROOT)))
    assert not offenders, "missing exact scope comment:\n" + "\n".join(offenders)


# ───────────────────────── structural sanity checks ───────────────────────


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_blueprint_directory_shape(name: str) -> None:
    d = blueprint_dir(name)
    assert (d / "blueprint.toml").is_file()
    assert (d / "templates").is_dir()
    assert (d / "README.md").is_file()


def test_only_expected_blueprints_present() -> None:
    present = {p.name for p in BLUEPRINTS_ROOT.iterdir() if p.is_dir()}
    assert set(BLUEPRINT_NAMES) <= present


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_gitignore_present(name: str) -> None:
    assert (blueprint_dir(name) / "templates" / ".gitignore").is_file()


@pytest.mark.parametrize("name", BLUEPRINT_NAMES)
def test_pyproject_template_depends_on_agenticcarekit(name: str) -> None:
    pyproject = blueprint_dir(name) / "templates" / "pyproject.toml.tmpl"
    assert pyproject.is_file(), f"{name}: missing templates/pyproject.toml.tmpl"
    text = pyproject.read_text(encoding="utf-8")
    assert "agenticcarekit" in text
