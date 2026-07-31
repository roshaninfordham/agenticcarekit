"""The blueprint template renderer (docs/CONTRACTS.md — "Blueprint layout").

Rules, all of them:

* Files ending ``.tmpl`` are rendered by simple ``{{var}}`` substitution
  and the suffix is stripped. Everything else is copied verbatim (bytes,
  so binary assets survive).
* Exactly nine variables substitute: ``project_name``, ``blueprint``,
  ``pack``, ``model_primary``, ``model_fallback``, ``egress``,
  ``redactor``, ``capabilities_list``, ``ack_version``.
* An unknown ``{{...}}`` in a ``.tmpl`` file is an **E501**, not silence.
* Generation is deterministic: sorted iteration, no timestamps, no
  absolute paths — identical inputs produce a byte-identical tree
  (invariant 4).

Example:
    >>> vars_ = build_vars(project_name="demo", blueprint="voice-care",
    ...                    pack="healthcare", model_primary="ollama:gemma4:e4b")
    >>> render_text("hello {{project_name}}", vars_, "x.tmpl")
    'hello demo'
    >>> render_text("{{nope}}", vars_, "x.tmpl")
    Traceback (most recent call last):
    ...
    agenticcarekit.kernel.contracts.errors.AckError: unknown template variable '{{nope}}' in x.tmpl
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from agenticcarekit import __version__
from agenticcarekit.kernel.contracts import AckError

__all__ = [
    "TEMPLATE_VARS",
    "build_vars",
    "iter_template_files",
    "render_text",
    "render_tree",
]

#: The closed set of substitutable variables. Adding one means amending
#: docs/CONTRACTS.md in the same commit.
TEMPLATE_VARS: tuple[str, ...] = (
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

_VAR_RE = re.compile(r"\{\{([^{}]*)\}\}")

#: Never copied into a generated project.
SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", ".git", ".pytest_cache", ".ruff_cache"})


def build_vars(
    *,
    project_name: str,
    blueprint: str,
    pack: str = "",
    model_primary: str = "",
    model_fallback: str | None = None,
    egress: str = "device",
    redactor: str | None = None,
    capabilities: list[str] | tuple[str, ...] = (),
) -> dict[str, str]:
    """Build the substitution context.

    ``capabilities_list`` renders as a bracketed, double-quoted list — valid
    TOML *and* valid Python, so a template can drop it into either.

    Example:
        >>> build_vars(project_name="p", blueprint="b",
        ...            capabilities=["voice", "extract"])["capabilities_list"]
        '["voice", "extract"]'
        >>> build_vars(project_name="p", blueprint="b")["model_fallback"]
        ''
    """
    return {
        "ack_version": __version__,
        "blueprint": blueprint,
        "capabilities_list": "[" + ", ".join(f'"{c}"' for c in capabilities) + "]",
        "egress": egress,
        "model_fallback": model_fallback or "",
        "model_primary": model_primary,
        "pack": pack,
        "project_name": project_name,
        "redactor": redactor or "",
    }


def render_text(text: str, variables: dict[str, str], source: str) -> str:
    """Substitute ``{{var}}``; an unknown variable raises E501."""

    def sub(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in variables:
            raise AckError(
                f"unknown template variable '{{{{{key}}}}}' in {source}",
                code="E501",
                why=(
                    "the renderer substitutes exactly: " + ", ".join(TEMPLATE_VARS) + "."
                ),
                fix=f"fix the template at {source}, or add the variable to the contract",
                details={"template": source, "variable": key, "known": list(TEMPLATE_VARS)},
            )
        return variables[key]

    return _VAR_RE.sub(sub, text)


def iter_template_files(templates: Path) -> list[Path]:
    """Every template file, sorted, relative to ``templates``.

    Sorted iteration is what makes generation byte-identical across runs
    and platforms (invariant 4).
    """
    files: list[Path] = []
    for path in templates.rglob("*"):
        if any(part in SKIP_NAMES for part in path.relative_to(templates).parts):
            continue
        if path.is_file():
            files.append(path.relative_to(templates))
    return sorted(files, key=lambda p: p.as_posix())


def render_tree(templates: Path, dest: Path, variables: dict[str, str]) -> list[str]:
    """Render a template tree into ``dest``. Returns written paths, sorted.

    Directories are created as needed; existing files are overwritten (the
    generator owns the blueprint tree — user files live outside it and
    ``ack sync`` is what reconciles them).
    """
    written: list[str] = []
    for rel in iter_template_files(templates):
        src = templates / rel
        if rel.suffix == ".tmpl":
            out_rel = rel.with_suffix("")
            out = dest / out_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            out.write_text(render_text(text, variables, rel.as_posix()), encoding="utf-8")
        else:
            out_rel = rel
            out = dest / out_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            # copyfile (not copy2): mtimes are not content and must not leak
            # into a tree that is supposed to be byte-identical.
            shutil.copyfile(src, out)
            shutil.copymode(src, out)
        written.append(out_rel.as_posix())
    return sorted(written)
