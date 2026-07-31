"""The recommendation rule table (brief §7.2).

A **declarative** table, not buried conditionals: every rule is an object
with a name, a predicate or scorer, and a reason template. That is what
makes the engine auditable (`ack init` prints the whole thing under
``? why``), testable (the fixture corpus asserts reason strings verbatim),
and — critically — able to explain itself.

Two kinds of rule:

* :class:`HardFilter` — eliminates a candidate outright. ``predicate``
  returns True when the candidate must go.
* :class:`SoftScore` — contributes ``weight × scorer(ctx)`` to the score.
  A non-zero contribution always produces a reason string.

Reason templates are formatted against :meth:`RuleContext.fields`. A key a
template asks for and the context does not have renders as ``unknown``
rather than raising — an unexplained recommendation is a bug, but a
crashed one is worse.

Example:
    >>> from agenticcarekit.cli.detect import MachineFacts
    >>> from .catalog import CATALOG
    >>> ctx = RuleContext(CATALOG["gemma4:31b"], MachineFacts(ram_total_gb=8.0),
    ...                   Requirements(blueprint="voice-care"))
    >>> [f.name for f in HARD_FILTERS if f.predicate(ctx)]
    ['ram']
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from agenticcarekit.cli.detect import MachineFacts

from .catalog import ModelEntry

__all__ = [
    "ETA_THRESHOLD_SECONDS",
    "HARD_FILTERS",
    "HardFilter",
    "Requirements",
    "RuleContext",
    "SOFT_SCORES",
    "SoftScore",
]

#: Past this download ETA the fallback rule promotes a hosted primary
#: (brief §7.2: "steep penalty past 5 minutes").
ETA_THRESHOLD_SECONDS = 300.0

#: A pull needs headroom beyond the blob itself (temp files, manifest).
DISK_HEADROOM = 1.15

#: A CUDA path may use at most this share of VRAM.
VRAM_HEADROOM = 0.9


@dataclass(frozen=True)
class Requirements:
    """What a blueprint needs from a model (``blueprint.toml [requires]``)."""

    blueprint: str = "unknown"
    modalities_in: frozenset[str] = frozenset({"text"})
    tool_calling: bool = True
    context_tokens: int = 0


class _Unknown(dict):
    """``format_map`` backing store: a missing key renders as ``unknown``."""

    def __missing__(self, key: str) -> str:
        return "unknown"


def _num(value: float | int | None) -> str:
    """Format a number for a reason string: ``36``, ``9.6``, ``unknown``.

    Example:
        >>> _num(36.0), _num(9.6), _num(None)
        ('36', '9.6', 'unknown')
    """
    if value is None:
        return "unknown"
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.1f}"


@dataclass
class RuleContext:
    """One (model, machine, blueprint) triple — the input to every rule."""

    entry: ModelEntry
    facts: MachineFacts
    reqs: Requirements
    _fields: dict[str, str] = field(default_factory=dict, repr=False)

    # ── derived facts ──────────────────────────────────────────────────

    @property
    def local(self) -> bool:
        """True when the model downloads and runs on this machine."""
        return not self.entry.hosted and self.entry.size_gb is not None

    @property
    def already_pulled(self) -> bool:
        """Zero download cost — the single largest bonus in the table."""
        return self.facts.has_tag(self.entry.tag)

    @property
    def missing_modalities(self) -> list[str]:
        """Required input modalities this model does not declare.

        Example:
            >>> from .catalog import CATALOG
            >>> RuleContext(CATALOG["gemma4:31b"], MachineFacts(),
            ...     Requirements(modalities_in=frozenset({"audio"}))).missing_modalities
            ['audio']
        """
        return sorted(set(self.reqs.modalities_in) - set(self.entry.modalities_in))

    @property
    def eta_seconds(self) -> float | None:
        """Measured download ETA, or None when there is nothing to download
        (hosted / already pulled) or throughput is unknown."""
        if not self.local or self.already_pulled:
            return None
        mbps = self.facts.network_mbps
        if not mbps or mbps <= 0 or self.entry.size_gb is None:
            return None
        return (self.entry.size_gb * 8_000.0) / mbps

    @property
    def eta_minutes(self) -> float | None:
        eta = self.eta_seconds
        return None if eta is None else eta / 60.0

    # ── reason substitution ────────────────────────────────────────────

    def fields(self) -> dict[str, str]:
        """Every value a reason template may reference, pre-formatted."""
        if self._fields:
            return self._fields
        e, f, r = self.entry, self.facts, self.reqs
        eta_min = self.eta_minutes
        vals: dict[str, str] = {
            "tag": e.tag,
            "provider": e.provider,
            "ref": e.ref,
            "blurb": e.blurb,
            "blueprint": r.blueprint,
            "missing_modalities": ", ".join(self.missing_modalities) or "nothing",
            "model_modalities": ", ".join(sorted(e.modalities_in)),
            "required_modalities": ", ".join(sorted(r.modalities_in)),
            "model_context_k": str(e.context_k),
            "required_context_k": str(r.context_tokens // 1024),
            "size_gb": _num(e.size_gb),
            "disk_needed_gb": _num(round(e.size_gb * DISK_HEADROOM, 1) if e.size_gb else None),
            "disk_free_gb": _num(f.disk_free_gb),
            "model_dir": f.model_dir,
            "ram_total_gb": _num(f.ram_total_gb),
            "ram_usable_gb": _num(f.usable_ram_gb),
            "ram_available_gb": _num(f.ram_available_gb),
            "vram_gb": _num(f.vram_gb),
            "gpu_name": f.gpu_name or "unknown",
            "cpu_model": f.cpu_model,
            "os": f.os,
            "arch": f.arch,
            "throughput_mbps": _num(f.network_mbps),
            "eta_minutes": "<1" if (eta_min is not None and eta_min < 1) else _num(
                round(eta_min) if eta_min is not None else None
            ),
        }
        self._fields = _Unknown(vals)
        return self._fields

    def render(self, template: str) -> str:
        """Format a reason template against :meth:`fields`.

        Example:
            >>> from .catalog import CATALOG
            >>> RuleContext(CATALOG["gemma4:e4b"], MachineFacts(), Requirements()
            ...     ).render("{tag} is {size_gb} GB")
            'gemma4:e4b is 9.6 GB'
        """
        return template.format_map(self.fields())


# ── rule types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HardFilter:
    """Eliminate a candidate. ``predicate`` True ⇒ gone, with a reason."""

    name: str
    predicate: Callable[[RuleContext], bool]
    reason_template: str

    def check(self, ctx: RuleContext) -> str | None:
        """Return the elimination reason, or None when the candidate passes."""
        try:
            hit = bool(self.predicate(ctx))
        except Exception:  # noqa: BLE001 - a broken rule must not break init
            return None
        return ctx.render(self.reason_template) if hit else None


@dataclass(frozen=True)
class SoftScore:
    """Contribute ``weight × scorer(ctx)`` and, when non-zero, a reason.

    ``explain_when`` lets a rule teach even when it does not move the score:
    a download ETA of two minutes changes no ranking, but printing it is
    exactly the mentoring the plan screen exists for (brief §7.3).
    """

    name: str
    weight: float
    scorer: Callable[[RuleContext], float]
    reason_template: str
    explain_when: Callable[[RuleContext], bool] | None = None

    def apply(self, ctx: RuleContext) -> tuple[float, str | None]:
        """Return ``(contribution, reason_or_None)``."""
        try:
            raw = float(self.scorer(ctx))
        except Exception:  # noqa: BLE001
            return 0.0, None
        contribution = round(self.weight * raw, 4) if raw else 0.0
        explain = bool(contribution)
        if not explain and self.explain_when is not None:
            try:
                explain = bool(self.explain_when(ctx))
            except Exception:  # noqa: BLE001
                explain = False
        return contribution, ctx.render(self.reason_template) if explain else None


# ── the hard filter table (brief §7.2, in order) ─────────────────────────

HARD_FILTERS: tuple[HardFilter, ...] = (
    HardFilter(
        "modalities",
        lambda c: bool(c.missing_modalities),
        "{tag}: no {missing_modalities} input — the {blueprint} blueprint requires it",
    ),
    HardFilter(
        "context",
        lambda c: c.entry.context_tokens < c.reqs.context_tokens,
        "{tag}: {model_context_k}K context is under the {required_context_k}K "
        "the {blueprint} blueprint requires",
    ),
    HardFilter(
        "tool_calling",
        lambda c: c.reqs.tool_calling and not c.entry.tool_calling,
        "{tag}: no native function calling — the {blueprint} blueprint requires it",
    ),
    HardFilter(
        "mlx_platform",
        lambda c: c.entry.mlx and not c.facts.apple_silicon,
        "{tag}: -mlx builds need Apple Silicon, this machine is {os}/{arch}",
    ),
    HardFilter(
        "ram",
        lambda c: (
            c.local
            and c.facts.usable_ram_gb is not None
            and c.entry.size_gb > c.facts.usable_ram_gb
        ),
        "{tag}: {size_gb} GB does not fit the {ram_usable_gb} GB of usable RAM "
        "(60% of {ram_total_gb} GB)",
    ),
    HardFilter(
        "vram",
        lambda c: (
            c.local
            and c.facts.cuda
            and c.facts.vram_gb is not None
            and c.entry.size_gb > c.facts.vram_gb * VRAM_HEADROOM
        ),
        "{tag}: {size_gb} GB exceeds 90% of the {vram_gb} GB VRAM on {gpu_name}",
    ),
    HardFilter(
        "disk",
        lambda c: (
            c.local
            and c.facts.disk_free_gb is not None
            and c.facts.disk_free_gb < c.entry.size_gb * DISK_HEADROOM
        ),
        "{tag}: needs {disk_needed_gb} GB free at {model_dir}, "
        "only {disk_free_gb} GB is available",
    ),
)


# ── soft scorers ─────────────────────────────────────────────────────────


def _score_eta(ctx: RuleContext) -> float:
    """Penalise steeply past five minutes; below it, cost nothing.

    A download that finishes inside the threshold is not a *reason* to
    prefer one model over another — the brief asks for a steep penalty past
    five minutes, not a race. Past the threshold the penalty grows without
    a ceiling so that, once the fallback rule fires, the *smallest* local
    model is the one recommended for the background pull.

    Example:
        >>> from .catalog import CATALOG
        >>> from agenticcarekit.cli.detect import MachineFacts
        >>> fast = RuleContext(CATALOG["gemma4:12b"],
        ...     MachineFacts(network_mbps=900.0), Requirements())
        >>> _score_eta(fast)
        0.0
        >>> slow = RuleContext(CATALOG["gemma4:12b"],
        ...     MachineFacts(network_mbps=2.0), Requirements())
        >>> _score_eta(slow) < -50
        True
    """
    eta = ctx.eta_seconds
    if eta is None or eta <= ETA_THRESHOLD_SECONDS:
        return 0.0
    return -(eta - ETA_THRESHOLD_SECONDS) / ETA_THRESHOLD_SECONDS


def _score_capability_headroom(ctx: RuleContext) -> float:
    """Input modalities beyond the blueprint's minimum."""
    extra = len(set(ctx.entry.modalities_in) - set(ctx.reqs.modalities_in))
    return min(1.0, extra / 3.0)


def _score_context_headroom(ctx: RuleContext) -> float:
    """Context window beyond the blueprint's minimum, log-scaled."""
    need = ctx.reqs.context_tokens
    if need <= 0 or ctx.entry.context_tokens <= 0:
        return 0.0
    ratio = ctx.entry.context_tokens / need
    if ratio <= 1.0:
        return 0.0
    return min(1.0, math.log2(ratio) / 3.0)


SOFT_SCORES: tuple[SoftScore, ...] = (
    SoftScore(
        "already_pulled",
        60.0,
        lambda c: 1.0 if c.already_pulled else 0.0,
        "already pulled: no download, ready now",
    ),
    SoftScore(
        "quality_tier",
        20.0,
        lambda c: c.entry.quality_tier / 5.0,
        "{blurb}",
    ),
    SoftScore(
        "platform_fit",
        15.0,
        lambda c: 1.0 if (c.entry.mlx and c.facts.apple_silicon) else 0.0,
        "-mlx build: native Apple Silicon acceleration on {cpu_model}",
    ),
    SoftScore(
        "download_eta",
        40.0,
        _score_eta,
        "download ETA ~{eta_minutes} min for {size_gb} GB at {throughput_mbps} Mbps",
        explain_when=lambda c: c.eta_seconds is not None,
    ),
    SoftScore(
        "capability_headroom",
        6.0,
        _score_capability_headroom,
        "handles {model_modalities} input — headroom over the {required_modalities} required",
    ),
    SoftScore(
        "context_headroom",
        8.0,
        _score_context_headroom,
        "{model_context_k}K context against the {required_context_k}K required",
    ),
    SoftScore(
        "stay_on_device",
        25.0,
        lambda c: -1.0 if c.entry.hosted else 0.0,
        "{tag}: hosted — data leaves the machine, and agenticcarekit prefers on-device",
    ),
    SoftScore(
        "ollama_present",
        10.0,
        lambda c: 0.0 if (c.entry.hosted or c.facts.ollama_installed) else -1.0,
        "ollama is not installed yet — install it first: brew install ollama",
    ),
    SoftScore(
        "ram_fit",
        3.0,
        lambda c: (
            1.0
            if (
                c.local
                and c.facts.usable_ram_gb is not None
                and c.entry.size_gb is not None
                and c.entry.size_gb <= c.facts.usable_ram_gb * 0.5
            )
            else 0.0
        ),
        "{size_gb} GB fits comfortably in {ram_total_gb} GB of RAM",
    ),
)
