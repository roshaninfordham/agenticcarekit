"""Error contract — stable codes, honest messages, literal fixes.

Every agenticcarekit error carries a stable searchable code (``E203``),
what happened, why, and the literal command that fixes it. The registry of
long-form explanations lives in ``spec/errors.json`` and is shared by every
implementation and by the MCP server (``ack explain E203``).

Code ranges:
    E0xx  bootstrap / environment
    E1xx  model / provider / network
    E2xx  capability mismatch
    E3xx  policy and privacy violations
    E4xx  project config
    E5xx  generation / templates
    E6xx  eval
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "AckError",
    "CapabilityMismatch",
    "PolicyViolation",
    "error_registry",
    "explain",
]


@dataclass(frozen=True)
class ErrorEntry:
    """One entry from the shared error registry (``spec/errors.json``)."""

    code: str
    title: str
    what: str
    why: str
    fix: str


class AckError(Exception):
    """Base for every agenticcarekit error.

    Renders in the canonical CLI shape::

        ✗ E203  gemma4:31b does not support audio input
                The voice-care blueprint needs an audio-capable model.
                Native audio is available on E2B and E4B only.

                ack init --model gemma4:e4b-mlx

    Example:
        >>> err = AckError("boom", code="E999", why="because", fix="ack doctor")
        >>> err.code
        'E999'
        >>> "ack doctor" in err.render()
        True
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "E000",
        why: str | None = None,
        fix: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.why = why
        self.fix = fix
        self.details = details or {}

    def render(self) -> str:
        """Plain-text rendering (no color; the CLI layers color on top)."""
        lines = [f"✗ {self.code}  {self.message}"]
        if self.why:
            lines.append(f"       {self.why}")
        if self.fix:
            lines.append("")
            lines.append(f"       {self.fix}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable shape used by ``--json`` output and MCP."""
        return {
            "code": self.code,
            "message": self.message,
            "why": self.why,
            "fix": self.fix,
            "details": self.details,
        }


class CapabilityMismatch(AckError):
    """E2xx — a blueprint or request requires something the model lacks.

    Raised at startup / before any network call. Must name what is missing
    and which models have it (invariant 2: never silently degrade).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "E200",
        missing: list[str] | None = None,
        candidates: list[str] | None = None,
        **kw: Any,
    ) -> None:
        details = kw.pop("details", {})
        details.update({"missing": missing or [], "candidates": candidates or []})
        super().__init__(message, code=code, details=details, **kw)
        self.missing = missing or []
        self.candidates = candidates or []


class PolicyViolation(AckError):
    """E3xx — sensitive data was about to cross a disallowed egress boundary.

    Must name the exact call site and field (a vague policy error is one
    nobody fixes).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "E301",
        field_name: str | None = None,
        call_site: str | None = None,
        provider: str | None = None,
        **kw: Any,
    ) -> None:
        details = kw.pop("details", {})
        details.update(
            {"field": field_name, "call_site": call_site, "provider": provider}
        )
        super().__init__(message, code=code, details=details, **kw)
        self.field_name = field_name
        self.call_site = call_site
        self.provider = provider


def _registry_path() -> Path:
    """Locate ``errors.json``: packaged copy first, then repo checkout."""
    packaged = Path(__file__).resolve().parents[3] / "spec" / "errors.json"
    if packaged.is_file():
        return packaged
    # Development checkout: <repo>/packages/agenticcarekit/kernel/contracts/
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "spec" / "errors.json"


_REGISTRY: dict[str, ErrorEntry] | None = None


def error_registry() -> dict[str, ErrorEntry]:
    """Load (and cache) the shared error registry from ``spec/errors.json``."""
    global _REGISTRY
    if _REGISTRY is None:
        raw = json.loads(_registry_path().read_text(encoding="utf-8"))
        _REGISTRY = {
            e["code"]: ErrorEntry(
                code=e["code"],
                title=e["title"],
                what=e["what"],
                why=e["why"],
                fix=e["fix"],
            )
            for e in raw["errors"]
        }
    return _REGISTRY


def explain(code: str) -> ErrorEntry | None:
    """Long-form explanation for a code, or None if unregistered.

    Example:
        >>> explain("E203").title
        'Model does not support a required input modality'
    """
    return error_registry().get(code.upper())
