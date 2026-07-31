"""Contract 5 — ``ack.toml``: declarative project state.

The generator writes it, the runtime reads it, agents edit it, and
``ack sync`` reconciles the tree against it — projects are re-generatable,
never one-shot dumps. JSON Schema: ``spec/schemas/ack-toml.schema.json``.

Example ``ack.toml``::

    [project]
    blueprint = "voice-care"
    pack = "healthcare"

    [model]
    primary = "ollama:gemma4:e4b-mlx"
    fallback = "cerebras:gemma-4-31b"

    [policy]
    egress = "device"
    redactor = "healthcare.phi"

    [capabilities]
    enabled = ["voice", "extract"]
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AckError
from .provider import EgressClass

__all__ = ["AckConfig", "ModelRef"]


@dataclass(frozen=True)
class ModelRef:
    """A ``provider:model`` reference, e.g. ``ollama:gemma4:e4b-mlx``.

    Example:
        >>> ref = ModelRef.parse("ollama:gemma4:e4b-mlx")
        >>> ref.provider, ref.model
        ('ollama', 'gemma4:e4b-mlx')
    """

    provider: str
    model: str

    @staticmethod
    def parse(ref: str) -> ModelRef:
        provider, sep, model = ref.partition(":")
        if not sep or not provider or not model:
            raise AckError(
                f"invalid model reference '{ref}'",
                code="E401",
                why="model references have the form provider:model, e.g. ollama:gemma4:e4b",
                fix='set [model] primary = "ollama:gemma4:e4b" in ack.toml',
            )
        return ModelRef(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class AckConfig:
    """Parsed ``ack.toml``. Every field is overridable by the user; the
    runtime never writes this file after ``ack init`` except via
    ``ack sync``/``ack add``, which preserve user edits."""

    blueprint: str
    pack: str
    model_primary: ModelRef
    model_fallback: ModelRef | None = None
    egress: EgressClass = EgressClass.DEVICE
    redactor: str | None = None
    capabilities: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AckConfig:
        """Build from a parsed TOML dict, with E4xx errors naming the
        missing/invalid key exactly.

        Example:
            >>> cfg = AckConfig.from_dict({
            ...     "project": {"blueprint": "on-device", "pack": "healthcare"},
            ...     "model": {"primary": "ollama:gemma4:e4b"},
            ... })
            >>> cfg.egress.value
            'device'
        """
        try:
            project = d["project"]
            model = d["model"]
        except KeyError as k:
            raise AckError(
                f"ack.toml is missing the [{k.args[0]}] section",
                code="E402",
                why="[project] and [model] are required sections.",
                fix="run `ack sync` to regenerate a valid ack.toml, or see docs/CONTRACTS.md",
            ) from None
        policy = d.get("policy", {})
        caps = d.get("capabilities", {})
        try:
            egress = EgressClass(policy.get("egress", "device"))
        except ValueError:
            raise AckError(
                f"invalid [policy] egress '{policy.get('egress')}'",
                code="E403",
                why="egress must be one of: device, trusted-network, public-cloud",
                fix='set [policy] egress = "device"',
            ) from None
        fallback = model.get("fallback")
        return AckConfig(
            blueprint=project.get("blueprint", ""),
            pack=project.get("pack", ""),
            model_primary=ModelRef.parse(model["primary"]),
            model_fallback=ModelRef.parse(fallback) if fallback else None,
            egress=egress,
            redactor=policy.get("redactor"),
            capabilities=tuple(caps.get("enabled", ())),
            raw=d,
        )

    @staticmethod
    def load(path: str | Path) -> AckConfig:
        """Read and parse an ``ack.toml`` file."""
        p = Path(path)
        if not p.is_file():
            raise AckError(
                f"no ack.toml found at {p}",
                code="E404",
                why="this directory is not an agenticcarekit project (or you are in the wrong directory).",
                fix="cd into your project, or create one: ack init",
            )
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise AckError(
                f"ack.toml is not valid TOML: {e}",
                code="E401",
                why="the file was probably hand-edited into an invalid state.",
                fix="fix the TOML syntax, or regenerate with `ack sync`",
            ) from None
        return AckConfig.from_dict(data)

    def to_toml(self) -> str:
        """Serialize deterministically (byte-identical for identical
        configs — invariant 4)."""
        lines = [
            "[project]",
            f'blueprint = "{self.blueprint}"',
            f'pack = "{self.pack}"',
            "",
            "[model]",
            f'primary = "{self.model_primary}"',
        ]
        if self.model_fallback:
            lines.append(f'fallback = "{self.model_fallback}"')
        lines += ["", "[policy]", f'egress = "{self.egress.value}"']
        if self.redactor:
            lines.append(f'redactor = "{self.redactor}"')
        lines += [
            "",
            "[capabilities]",
            "enabled = [" + ", ".join(f'"{c}"' for c in self.capabilities) + "]",
            "",
        ]
        return "\n".join(lines)
