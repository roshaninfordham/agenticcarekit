"""Operations on an existing generated project.

``manifest`` · ``sync`` · ``add`` · ``swap`` · ``eject``.

All five read ``ack.toml`` as the source of truth (Contract 5) and none of
them destroys a user's edits: unknown keys survive every rewrite, and
``sync`` reports drift rather than silently overwriting hand-edited files.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import AckConfig, AckError, EgressClass, ModelRef

from .blueprints import discover as discover_blueprints
from .recommend import CATALOG
from .render import build_vars, iter_template_files, render_text
from .scaffold import agent_instructions, render_ack_toml, write_agent_surface

__all__ = [
    "EJECTABLES",
    "Ejectable",
    "KNOWN_CAPABILITIES",
    "SWAPPABLE",
    "add_capability",
    "build_manifest",
    "eject",
    "swap",
    "sync_project",
    "write_config",
]

#: First-party capabilities (brief §6, W-D/W-E). Discovery via entry points
#: is additive — this list is the floor, not the ceiling.
KNOWN_CAPABILITIES = ("agents", "extract", "rag", "voice")

_IGNORED_DIRS = frozenset({".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"})


def write_config(dest: Path, cfg: AckConfig) -> None:
    """Rewrite ``ack.toml`` deterministically, preserving unknown tables."""
    (dest / "ack.toml").write_text(render_ack_toml(cfg), encoding="utf-8")


def _refresh_agent_surface(dest: Path, cfg: AckConfig) -> list[str]:
    return write_agent_surface(
        dest,
        agent_instructions(
            project_name=dest.resolve().name,
            blueprint=cfg.blueprint,
            pack=cfg.pack,
            model_primary=str(cfg.model_primary),
            model_fallback=str(cfg.model_fallback) if cfg.model_fallback else None,
            egress=cfg.egress.value,
            redactor=cfg.redactor,
            capabilities=list(cfg.capabilities),
        ),
    )


# ── manifest ─────────────────────────────────────────────────────────────


def project_files(root: Path, limit: int = 500) -> list[str]:
    """Sorted, repo-relative file list — deterministic, no absolute paths."""
    out: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        if path.is_file() or path.is_symlink():
            out.append(rel.as_posix())
        if len(out) >= limit:
            break
    return out


def discover_tools(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Import the project's tool modules and collect their manifests.

    Returns ``(tools, notes)``. A module that fails to import produces a
    note, never an exception: ``ack manifest`` describes a project, it does
    not run it.

    Tool entries conform to ``spec/schemas/tool-manifest.schema.json``, so
    ``{"tools": tools}`` validates against it directly.
    """
    from agenticcarekit.kernel.contracts.tools import Tool

    tools: list[dict[str, Any]] = []
    notes: list[str] = []
    candidates = sorted(
        {
            *root.glob("app/tools/*.py"),
            *root.glob("app/tools/**/*.py"),
            *root.glob("tools/*.py"),
        },
        key=lambda p: p.as_posix(),
    )
    if not candidates:
        return tools, notes
    root_str = str(root.resolve())
    added = root_str not in sys.path
    if added:
        sys.path.insert(0, root_str)
    try:
        for path in candidates:
            if path.name.startswith("_"):
                continue
            mod_name = "ack_project_tools_" + path.stem
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001 - describing, not running
                notes.append(f"{path.relative_to(root).as_posix()}: {type(exc).__name__}: {exc}")
                continue
            for obj in vars(module).values():
                if isinstance(obj, Tool):
                    tools.append(obj.spec.to_manifest())
    finally:
        if added:
            sys.path.remove(root_str)
    tools.sort(key=lambda t: str(t["name"]))
    return tools, notes


def build_manifest(root: Path, cfg: AckConfig) -> dict[str, Any]:
    """The machine-readable description of a generated project (brief §9)."""
    tools, notes = discover_tools(root)
    return {
        "manifest_version": 1,
        "project": {
            "name": root.resolve().name,
            "blueprint": cfg.blueprint,
            "pack": cfg.pack,
        },
        "model": {
            "primary": str(cfg.model_primary),
            "fallback": str(cfg.model_fallback) if cfg.model_fallback else None,
        },
        "policy": {"egress": cfg.egress.value, "redactor": cfg.redactor},
        "capabilities": list(cfg.capabilities),
        "tools": tools,
        "tool_notes": notes,
        "files": project_files(root),
    }


# ── sync ─────────────────────────────────────────────────────────────────


def sync_project(root: Path, cfg: AckConfig, *, force: bool = False) -> dict[str, Any]:
    """Reconcile the tree against ``ack.toml``.

    Missing template files are re-created. Files that exist but differ are
    reported as *drift* and left alone unless ``force`` is set — the user's
    edits are the point of the file being ejectable.
    """
    created: list[str] = []
    drifted: list[str] = []
    unchanged: list[str] = []
    blueprints = discover_blueprints()
    spec = blueprints.get(cfg.blueprint)
    variables = build_vars(
        project_name=root.resolve().name,
        blueprint=cfg.blueprint,
        pack=cfg.pack,
        model_primary=str(cfg.model_primary),
        model_fallback=str(cfg.model_fallback) if cfg.model_fallback else None,
        egress=cfg.egress.value,
        redactor=cfg.redactor,
        capabilities=list(cfg.capabilities),
    )
    if spec is not None and spec.templates is not None:
        templates = spec.templates
        for rel in iter_template_files(templates):
            src = templates / rel
            out_rel = rel.with_suffix("") if rel.suffix == ".tmpl" else rel
            target = root / out_rel
            if rel.suffix == ".tmpl":
                want = render_text(
                    src.read_text(encoding="utf-8"), variables, rel.as_posix()
                ).encode("utf-8")
            else:
                want = src.read_bytes()
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(want)
                created.append(out_rel.as_posix())
            elif target.read_bytes() != want:
                if force:
                    target.write_bytes(want)
                    created.append(out_rel.as_posix())
                else:
                    drifted.append(out_rel.as_posix())
            else:
                unchanged.append(out_rel.as_posix())

    write_config(root, cfg)
    refreshed = _refresh_agent_surface(root, cfg)
    return {
        "blueprint": cfg.blueprint,
        "blueprint_found": spec is not None,
        "created": sorted(created),
        "drifted": sorted(drifted),
        "unchanged": sorted(unchanged),
        "refreshed": refreshed,
        "forced": force,
    }


# ── add / swap ───────────────────────────────────────────────────────────


def add_capability(root: Path, cfg: AckConfig, name: str) -> dict[str, Any]:
    """Enable a capability in ``ack.toml`` (idempotent)."""
    if name in cfg.capabilities:
        return {"capability": name, "changed": False, "capabilities": list(cfg.capabilities)}
    if name not in KNOWN_CAPABILITIES:
        raise AckError(
            f"unknown capability '{name}'",
            code="E401",
            why="first-party capabilities: " + ", ".join(KNOWN_CAPABILITIES),
            fix=f"ack add {KNOWN_CAPABILITIES[0]}   # or: ack new capability {name}",
            details={"known": list(KNOWN_CAPABILITIES)},
        )
    updated = AckConfig(
        blueprint=cfg.blueprint,
        pack=cfg.pack,
        model_primary=cfg.model_primary,
        model_fallback=cfg.model_fallback,
        egress=cfg.egress,
        redactor=cfg.redactor,
        capabilities=tuple(sorted({*cfg.capabilities, name})),
        raw=cfg.raw,
    )
    write_config(root, updated)
    _refresh_agent_surface(root, updated)
    return {"capability": name, "changed": True, "capabilities": list(updated.capabilities)}


#: ``ack swap <what> <value>`` — the closed set of swappable slots.
SWAPPABLE = ("model", "fallback", "pack", "redactor", "egress")


def swap(root: Path, cfg: AckConfig, what: str, value: str) -> dict[str, Any]:
    """Swap one declared slot in ``ack.toml``. Every default is overridable."""
    if what not in SWAPPABLE:
        raise AckError(
            f"cannot swap '{what}'",
            code="E401",
            why="swappable slots: " + ", ".join(SWAPPABLE),
            fix="ack swap model gemma4:12b",
            details={"swappable": list(SWAPPABLE)},
        )
    fields: dict[str, Any] = {
        "blueprint": cfg.blueprint,
        "pack": cfg.pack,
        "model_primary": cfg.model_primary,
        "model_fallback": cfg.model_fallback,
        "egress": cfg.egress,
        "redactor": cfg.redactor,
        "capabilities": cfg.capabilities,
        "raw": cfg.raw,
    }
    before = {
        "model": str(cfg.model_primary),
        "fallback": str(cfg.model_fallback) if cfg.model_fallback else None,
        "pack": cfg.pack,
        "redactor": cfg.redactor,
        "egress": cfg.egress.value,
    }[what]

    if what in ("model", "fallback"):
        if value.lower() in ("none", "", "-") and what == "fallback":
            fields["model_fallback"] = None
        else:
            ref = _normalise_ref(value)
            fields["model_primary" if what == "model" else "model_fallback"] = ref
    elif what == "pack":
        fields["pack"] = value
    elif what == "redactor":
        fields["redactor"] = None if value.lower() in ("none", "", "-") else value
    elif what == "egress":
        try:
            fields["egress"] = EgressClass(value)
        except ValueError:
            raise AckError(
                f"invalid egress class '{value}'",
                code="E403",
                why="egress must be one of: device, trusted-network, public-cloud",
                fix='ack swap egress device',
            ) from None

    updated = AckConfig(**fields)
    write_config(root, updated)
    _refresh_agent_surface(root, updated)
    after = {
        "model": str(updated.model_primary),
        "fallback": str(updated.model_fallback) if updated.model_fallback else None,
        "pack": updated.pack,
        "redactor": updated.redactor,
        "egress": updated.egress.value,
    }[what]
    return {"what": what, "from": before, "to": after}


def _normalise_ref(value: str) -> ModelRef:
    """Accept ``gemma4:e4b`` or ``ollama:gemma4:e4b``; resolve via the catalog."""
    if value in CATALOG:
        return ModelRef.parse(CATALOG[value].ref)
    return ModelRef.parse(value)


# ── eject ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ejectable:
    """One thing ``ack eject`` can inline into user source.

    ``ack eject <thing>`` is the promise that depending on agenticcarekit is
    reversible (brief §10) — which is precisely what makes people willing to
    depend on it. The registry is a table so new ejectables are one entry,
    not a new command.
    """

    name: str
    description: str
    resolve: Callable[[Path], list[tuple[Path, str]]]


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_prompts(_project: Path) -> list[tuple[Path, str]]:
    """Every packaged prompt ``.md``, mapped to ``prompts/<subpath>``."""
    pkg = _package_root()
    pairs: list[tuple[Path, str]] = []
    for prompts_dir in sorted(pkg.rglob("prompts"), key=lambda p: p.as_posix()):
        if not prompts_dir.is_dir():
            continue
        owner = prompts_dir.parent.relative_to(pkg).as_posix() or "kernel"
        for md in sorted(prompts_dir.rglob("*.md"), key=lambda p: p.as_posix()):
            rel = md.relative_to(prompts_dir).as_posix()
            pairs.append((md, f"prompts/{owner}/{rel}"))
    return pairs


#: The ejectable registry. Extend by adding an entry here — nothing else.
EJECTABLES: dict[str, Ejectable] = {
    "prompts": Ejectable(
        name="prompts",
        description="copy every packaged prompt .md into ./prompts/ so behaviour "
        "changes without touching logic",
        resolve=_resolve_prompts,
    ),
}


def eject(root: Path, thing: str, *, force: bool = False) -> dict[str, Any]:
    """Inline a packaged abstraction into the project. Never clobbers."""
    spec = EJECTABLES.get(thing)
    if spec is None:
        raise AckError(
            f"nothing to eject called '{thing}'",
            code="E401",
            why="ejectable things: " + ", ".join(sorted(EJECTABLES)),
            fix="ack eject prompts",
            details={"ejectable": sorted(EJECTABLES)},
        )
    pairs = spec.resolve(root)
    copied: list[str] = []
    skipped: list[str] = []
    for src, rel in pairs:
        target = root / rel
        if target.exists() and not force:
            skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        copied.append(rel)
    return {
        "ejected": thing,
        "description": spec.description,
        "copied": sorted(copied),
        "skipped": sorted(skipped),
        "available": sorted(EJECTABLES),
    }
