"""Blueprint discovery and parsing (docs/CONTRACTS.md — "Blueprint layout").

A blueprint is a directory containing ``blueprint.toml``, a ``templates/``
tree, and a ``README.md``. This module is the *reader*; :mod:`.render` is
the writer.

Search order, highest precedence first:

1. an explicit path passed to :func:`discover` / ``--blueprint-path``
2. ``ACK_BLUEPRINT_PATH`` (``os.pathsep``-separated directories)
3. the packaged ``agenticcarekit/blueprints/`` directory

The packaged directory is always searched last, never skipped, so
first-party blueprints stay discoverable while a checkout or a test can
shadow them. Where the packaged directory is empty — as it is until W-I
lands — discovery simply returns what exists.

Example:
    >>> isinstance(packaged_dir().name, str)
    True
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from agenticcarekit.cli.recommend import Requirements
from agenticcarekit.kernel.contracts import AckError

__all__ = [
    "BlueprintSpec",
    "BLUEPRINT_PATH_ENV",
    "discover",
    "load_blueprint",
    "packaged_dir",
    "search_paths",
]

BLUEPRINT_PATH_ENV = "ACK_BLUEPRINT_PATH"


@dataclass(frozen=True)
class BlueprintSpec:
    """One parsed ``blueprint.toml`` plus the location of its templates."""

    name: str
    description: str
    track: str
    root: Path
    requires: Requirements
    default_capabilities: tuple[str, ...] = ()
    default_pack: str = ""

    @property
    def templates(self) -> Path | None:
        """The ``templates/`` tree, or None when the blueprint ships none."""
        t = self.root / "templates"
        return t if t.is_dir() else None

    @property
    def readme(self) -> Path | None:
        r = self.root / "README.md"
        return r if r.is_file() else None

    def to_dict(self) -> dict[str, object]:
        """Serializable form for ``--json``."""
        return {
            "name": self.name,
            "description": self.description,
            "track": self.track,
            "root": str(self.root),
            "requires": {
                "modalities_in": sorted(self.requires.modalities_in),
                "tool_calling": self.requires.tool_calling,
                "context_tokens": self.requires.context_tokens,
            },
            "defaults": {
                "capabilities": list(self.default_capabilities),
                "pack": self.default_pack,
            },
            "has_templates": self.templates is not None,
        }


def packaged_dir() -> Path:
    """The first-party blueprints directory shipped inside the package."""
    return Path(__file__).resolve().parent.parent / "blueprints"


def search_paths(explicit: str | Path | None = None) -> list[Path]:
    """Directories to scan, highest precedence first.

    Example:
        >>> import os
        >>> os.environ["ACK_BLUEPRINT_PATH"] = "/tmp/bp"
        >>> [str(p) for p in search_paths()][0]
        '/tmp/bp'
        >>> del os.environ["ACK_BLUEPRINT_PATH"]
    """
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    env = os.environ.get(BLUEPRINT_PATH_ENV, "")
    paths.extend(Path(p).expanduser() for p in env.split(os.pathsep) if p.strip())
    paths.append(packaged_dir())
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        key = p.absolute()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def discover(explicit: str | Path | None = None) -> dict[str, BlueprintSpec]:
    """Every blueprint on the search path, name → spec, sorted by name.

    Earlier search paths win on a name collision. Directories that do not
    exist are skipped silently — an empty packaged directory is a normal
    state, not an error.
    """
    found: dict[str, BlueprintSpec] = {}
    for base in search_paths(explicit):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or not (child / "blueprint.toml").is_file():
                continue
            try:
                spec = load_blueprint(child)
            except AckError:
                continue
            found.setdefault(spec.name, spec)
    return dict(sorted(found.items()))


def load_blueprint(root: str | Path) -> BlueprintSpec:
    """Parse one blueprint directory. Raises E410 when it is malformed."""
    root = Path(root)
    manifest = root / "blueprint.toml"
    if not manifest.is_file():
        raise AckError(
            f"no blueprint.toml in {root}",
            code="E410",
            why="a blueprint is a directory containing blueprint.toml and templates/.",
            fix="ack new blueprint my-blueprint",
        )
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise AckError(
            f"{manifest} is not valid TOML: {e}",
            code="E410",
            why="the blueprint manifest could not be parsed.",
            fix=f"fix the TOML in {manifest}",
        ) from None
    bp = data.get("blueprint", {})
    name = str(bp.get("name") or root.name)
    requires = data.get("requires", {})
    defaults = data.get("defaults", {})
    mods = requires.get("modalities_in") or ["text"]
    return BlueprintSpec(
        name=name,
        description=str(bp.get("description", "")),
        track=str(bp.get("track", "")),
        root=root,
        requires=Requirements(
            blueprint=name,
            modalities_in=frozenset(str(m) for m in mods),
            tool_calling=bool(requires.get("tool_calling", True)),
            context_tokens=int(requires.get("context_tokens", 0)),
        ),
        default_capabilities=tuple(str(c) for c in defaults.get("capabilities", ())),
        default_pack=str(defaults.get("pack", "")),
    )


def resolve(name: str | None, explicit_path: str | Path | None = None) -> BlueprintSpec:
    """Pick a blueprint by name, or the first available one.

    Raises E410 naming what *is* installed — an error that lists the valid
    answers is the difference between a dead end and a fix.
    """
    available = discover(explicit_path)
    if not available:
        raise AckError(
            "no blueprints are installed",
            code="E410",
            why="none of the blueprint search paths contain a blueprint.toml.",
            fix=f"ack new blueprint my-blueprint   # or set {BLUEPRINT_PATH_ENV}",
            details={"search_paths": [str(p) for p in search_paths(explicit_path)]},
        )
    if name is None:
        return next(iter(available.values()))
    if name not in available:
        raise AckError(
            f"unknown blueprint '{name}'",
            code="E410",
            why="installed blueprints: " + ", ".join(available),
            fix=f"ack init --blueprint {next(iter(available))}",
            details={"available": sorted(available)},
        )
    return available[name]
