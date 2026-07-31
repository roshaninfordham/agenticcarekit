"""Ranking, the fallback rule, and ``? why`` (brief §7.2 / §7.3).

The engine is deliberately thin: it walks the declarative tables in
:mod:`.rules`, collects every reason each rule contributes, and sorts. All
of the judgment lives in the tables, where it can be read, diffed and
tested — not in this file.

Output is a :class:`Recommendation` carrying *every* candidate, winners and
losers alike, each with its reasons and (for losers) the filter that
eliminated it. ``ack init``'s ``? why`` prints that table verbatim, and so
does ``--json``.

Example:
    >>> from agenticcarekit.cli.detect import MachineFacts
    >>> facts = MachineFacts(os="Darwin", arch="arm64", ram_total_gb=36.0,
    ...                      disk_free_gb=400.0, ollama_installed=True,
    ...                      network_mbps=800.0)
    >>> rec = recommend(facts, Requirements(blueprint="voice-care",
    ...                 modalities_in=frozenset({"text", "audio"}),
    ...                 context_tokens=32768))
    >>> rec.model
    'gemma4:e4b-mlx'
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agenticcarekit.cli.detect import MachineFacts
from agenticcarekit.kernel.contracts import AckError, CapabilityMismatch

from .catalog import CATALOG, HOSTED_PRIMARY, ModelEntry, audio_capable_tags
from .rules import (
    ETA_THRESHOLD_SECONDS,
    HARD_FILTERS,
    SOFT_SCORES,
    Requirements,
    RuleContext,
)

__all__ = [
    "Candidate",
    "Recommendation",
    "Requirements",
    "explain_ranking",
    "rank",
    "recommend",
    "validate_choice",
]

#: Hard filters that map onto a *registered* error code (spec/errors.json).
#: A filter absent from this map degrades to a warning when the user forces
#: the model with ``--model``: raising an unregistered code is a test
#: failure (docs/CONTRACTS.md), and inventing one here would be exactly that.
#: When *everything* is eliminated, this is the order in which a filter is
#: reported as "the binding constraint". A resource the user can free comes
#: before a capability they cannot — an error that names a fixable cause is
#: worth more than one that names the most common cause.
_BINDING_PRIORITY = ("disk", "ram", "vram", "context", "tool_calling", "modalities", "mlx_platform")

_FILTER_CODES: dict[str, str] = {
    "modalities": "E203",
    "context": "E201",
    "tool_calling": "E202",
    "disk": "E020",
}


class Candidate(BaseModel):
    """One ranked model, with the reasons that put it there."""

    tag: str
    provider: str
    ref: str
    hosted: bool
    size_gb: float | None = None
    context_tokens: int = 0
    already_pulled: bool = False
    eta_seconds: float | None = None
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    eliminated_by: str | None = None
    eliminated_reasons: list[str] = Field(default_factory=list)

    @property
    def eliminated(self) -> bool:
        return self.eliminated_by is not None


class Recommendation(BaseModel):
    """The plan the ``init`` screen renders and ``--json`` emits."""

    blueprint: str
    pack: str = ""
    capabilities: list[str] = Field(default_factory=list)
    model: str
    model_ref: str
    providers: list[str] = Field(default_factory=list)
    fallback_ref: str | None = None
    egress: str = "device"
    redactor: str | None = None
    reasons: list[str] = Field(default_factory=list)
    provider_reason: str = ""
    background_pull: str | None = None
    forced: bool = False
    warnings: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)

    @property
    def survivors(self) -> list[Candidate]:
        return [c for c in self.candidates if not c.eliminated]

    @property
    def eliminated(self) -> list[Candidate]:
        return [c for c in self.candidates if c.eliminated]

    def top_reasons(self, n: int = 2) -> list[str]:
        """The ``←`` annotations for the plan screen (brief §7.3)."""
        return self.reasons[:n]


def _evaluate(entry: ModelEntry, facts: MachineFacts, reqs: Requirements) -> Candidate:
    """Run every rule against one catalog entry."""
    ctx = RuleContext(entry, facts, reqs)
    cand = Candidate(
        tag=entry.tag,
        provider=entry.provider,
        ref=entry.ref,
        hosted=entry.hosted,
        size_gb=entry.size_gb,
        context_tokens=entry.context_tokens,
        already_pulled=ctx.already_pulled,
        eta_seconds=round(ctx.eta_seconds, 1) if ctx.eta_seconds is not None else None,
    )
    for hf in HARD_FILTERS:
        reason = hf.check(ctx)
        if reason:
            if cand.eliminated_by is None:
                cand.eliminated_by = hf.name
            cand.eliminated_reasons.append(reason)
    if cand.eliminated_by is not None:
        return cand

    scored: list[tuple[float, str]] = []
    total = 0.0
    for ss in SOFT_SCORES:
        contribution, reason = ss.apply(ctx)
        if contribution:
            cand.score_breakdown[ss.name] = contribution
            total += contribution
        if reason:
            scored.append((abs(contribution), reason))
    cand.score = round(total, 4)
    # Strongest contribution first: the top two become the `←` annotations.
    cand.reasons = [r for _w, r in sorted(scored, key=lambda t: -t[0])]
    return cand


def rank(facts: MachineFacts, reqs: Requirements) -> list[Candidate]:
    """Every catalog entry, survivors first (best score first), then losers.

    Example:
        >>> facts = MachineFacts(ram_total_gb=8.0, disk_free_gb=200.0)
        >>> ranked = rank(facts, Requirements(blueprint="on-device"))
        >>> ranked[0].eliminated
        False
    """
    cands = [_evaluate(e, facts, reqs) for e in CATALOG.values()]
    survivors = sorted(
        (c for c in cands if not c.eliminated), key=lambda c: (-c.score, c.tag)
    )
    losers = sorted((c for c in cands if c.eliminated), key=lambda c: (c.eliminated_by or "", c.tag))
    return survivors + losers


def validate_choice(tag: str, facts: MachineFacts, reqs: Requirements) -> list[str]:
    """Check an explicitly requested ``--model`` against the hard filters.

    Raises a registered ``AckError`` for a capability or disk problem;
    returns the remaining problems as warning strings (the user asked for
    this model by name, so a fit warning is not a refusal).

    Example:
        >>> from agenticcarekit.cli.detect import MachineFacts
        >>> validate_choice("gemma4:31b", MachineFacts(),
        ...     Requirements(blueprint="voice-care",
        ...                  modalities_in=frozenset({"audio"})))
        Traceback (most recent call last):
        ...
        agenticcarekit.kernel.contracts.errors.CapabilityMismatch: gemma4:31b does not support audio input
    """
    entry = CATALOG.get(tag)
    if entry is None:
        raise AckError(
            f"unknown model tag '{tag}'",
            code="E401",
            why="known tags: " + ", ".join(sorted(CATALOG)),
            fix="ack init --model gemma4:e4b",
            details={"known_tags": sorted(CATALOG)},
        )
    ctx = RuleContext(entry, facts, reqs)
    warnings: list[str] = []
    for hf in HARD_FILTERS:
        reason = hf.check(ctx)
        if not reason:
            continue
        code = _FILTER_CODES.get(hf.name)
        if code is None:
            warnings.append(reason)
            continue
        if hf.name == "modalities":
            missing = ", ".join(f"{m} input" for m in ctx.missing_modalities)
            raise CapabilityMismatch(
                f"{tag} does not support {missing}",
                code=code,
                missing=ctx.missing_modalities,
                candidates=audio_capable_tags(),
                why=reason,
                fix="ack init --model gemma4:e4b-mlx",
            )
        raise AckError(
            reason,
            code=code,
            why=f"hard filter '{hf.name}' eliminated {tag} on this machine.",
            fix="ack init   # let the recommender pick, or pass a model that fits",
            details={"filter": hf.name, "tag": tag},
        )
    return warnings


def recommend(
    facts: MachineFacts,
    reqs: Requirements,
    *,
    pack: str = "",
    capabilities: list[str] | None = None,
    force_model: str | None = None,
    allow_hosted_fallback: bool = False,
    default_redactor: str | None = None,
) -> Recommendation:
    """Rank, apply the fallback rule, and assemble the plan.

    The fallback rule (brief §7.2): when the best local candidate would take
    longer than five minutes to download, or when no local candidate fits at
    all, the primary becomes hosted and the local pull is recommended in the
    background — and the reason string says exactly that.
    """
    candidates = rank(facts, reqs)
    survivors = [c for c in candidates if not c.eliminated]
    warnings: list[str] = []

    if force_model:
        warnings = validate_choice(force_model, facts, reqs)
        entry = CATALOG[force_model]
        chosen = next(
            (c for c in candidates if c.tag == force_model),
            _evaluate(entry, facts, reqs),
        )
        reasons = [f"chosen explicitly with --model {force_model}", *chosen.reasons]
        return _assemble(
            reqs, pack, capabilities, chosen, reasons, None,
            allow_hosted_fallback, default_redactor, candidates, warnings, forced=True,
        )

    if not survivors:
        by_filter: dict[str, int] = {}
        for c in candidates:
            by_filter[c.eliminated_by or "?"] = by_filter.get(c.eliminated_by or "?", 0) + 1
        binding = next(
            (name for name in _BINDING_PRIORITY if name in by_filter),
            max(by_filter, key=lambda k: by_filter[k]),
        )
        first = next(c for c in candidates if c.eliminated_by == binding)
        raise AckError(
            f"no model fits the {reqs.blueprint} blueprint on this machine",
            code="E203",
            why=(
                f"every candidate was eliminated; the binding constraint is '{binding}' "
                f"({by_filter[binding]} of {len(candidates)} models). "
                f"Example: {first.eliminated_reasons[0]}"
            ),
            fix="ack doctor --json   # free the resource named above, or use a lighter blueprint",
            details={
                "binding_filter": binding,
                "eliminated": [
                    {"tag": c.tag, "by": c.eliminated_by, "reasons": c.eliminated_reasons}
                    for c in candidates
                ],
            },
        )

    locals_ = [c for c in survivors if not c.hosted]
    best = survivors[0]
    background_pull: str | None = None
    reasons: list[str]

    fallback_reason = _fallback_reason(locals_, candidates, facts)
    if fallback_reason is not None:
        hosted = next(
            (c for c in survivors if c.tag == HOSTED_PRIMARY),
            next((c for c in survivors if c.hosted), None),
        )
        if hosted is not None:
            background_pull = locals_[0].tag if locals_ else None
            reasons = [fallback_reason, *hosted.reasons]
            return _assemble(
                reqs, pack, capabilities, hosted, reasons, background_pull,
                allow_hosted_fallback, default_redactor, candidates, warnings,
            )
        # The fallback rule wanted a hosted primary and there isn't one that
        # meets the blueprint's requirements. Say so rather than silently
        # recommending a download that will take all day.
        needs = ", ".join(sorted(reqs.modalities_in))
        eta = locals_[0].eta_seconds if locals_ else None
        detail = (
            f" — pulling {locals_[0].tag} will take about {max(1, round(eta / 60))} min here"
            if eta is not None
            else ""
        )
        warnings.append(
            f"no hosted model accepts {needs} input, so a local pull is the only path{detail}"
        )

    reasons = list(best.reasons)
    return _assemble(
        reqs, pack, capabilities, best, reasons, background_pull,
        allow_hosted_fallback, default_redactor, candidates, warnings,
    )


def _fallback_reason(
    locals_: list[Candidate], candidates: list[Candidate], facts: MachineFacts
) -> str | None:
    """The brief §7.2 fallback rule, as a single reason string or None."""
    if not locals_:
        ram_blocked = [c for c in candidates if c.eliminated_by == "ram"]
        if ram_blocked and facts.ram_total_gb:
            return (
                f"hosted primary: {_g(facts.ram_total_gb)} GB of RAM cannot hold any "
                "local Gemma 4 variant, so the model runs off-device"
            )
        return (
            "hosted primary: no local Gemma 4 variant survives this machine's limits, "
            "so the model runs off-device"
        )
    best_local = locals_[0]
    if best_local.eta_seconds is not None and best_local.eta_seconds > ETA_THRESHOLD_SECONDS:
        minutes = max(1, round(best_local.eta_seconds / 60))
        return (
            f"hosted primary: pulling {best_local.tag} would take ~{minutes} min on this "
            f"connection — serving from cerebras now and pulling it in the background"
        )
    return None


def _g(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _assemble(
    reqs: Requirements,
    pack: str,
    capabilities: list[str] | None,
    chosen: Candidate,
    reasons: list[str],
    background_pull: str | None,
    allow_hosted_fallback: bool,
    default_redactor: str | None,
    candidates: list[Candidate],
    warnings: list[str],
    *,
    forced: bool = False,
) -> Recommendation:
    """Turn a winning candidate into a full plan (providers, egress, redactor)."""
    if chosen.hosted:
        providers = [chosen.provider]
        fallback_ref = None
        if background_pull:
            providers.append("ollama")
            fallback_ref = f"ollama:{background_pull}"
        egress = "public-cloud"
        redactor = default_redactor
        provider_reason = (
            f"hosted primary with a local fallback once {background_pull} finishes pulling"
            if background_pull
            else "hosted primary: this machine cannot serve the model locally"
        )
        if redactor:
            provider_reason += f" — egress is public-cloud, so the {redactor} redactor is required"
    elif allow_hosted_fallback:
        providers = [chosen.provider, "cerebras"]
        fallback_ref = f"cerebras:{HOSTED_PRIMARY}"
        egress = "public-cloud"
        redactor = default_redactor
        provider_reason = (
            "local primary, hosted fallback — the fallback raises egress to public-cloud"
        )
        if redactor:
            provider_reason += f", so the {redactor} redactor is required"
    else:
        providers = [chosen.provider]
        fallback_ref = None
        egress = "device"
        redactor = default_redactor
        provider_reason = (
            "local primary, no hosted fallback: egress stays on device and nothing leaves "
            "the machine (add one with --providers ollama,cerebras)"
        )

    return Recommendation(
        blueprint=reqs.blueprint,
        pack=pack,
        capabilities=list(capabilities or []),
        model=chosen.tag,
        model_ref=chosen.ref,
        providers=providers,
        fallback_ref=fallback_ref,
        egress=egress,
        redactor=redactor,
        reasons=reasons,
        provider_reason=provider_reason,
        background_pull=background_pull,
        forced=forced,
        warnings=warnings,
        candidates=candidates,
    )


def explain_ranking(rec: Recommendation) -> dict[str, Any]:
    """The full ``? why`` table, as data. The CLI renders it; MCP returns it.

    Every surviving candidate carries its score and reasons; every
    eliminated one carries the filter that removed it and why.
    """
    return {
        "blueprint": rec.blueprint,
        "chosen": rec.model_ref,
        "reasons": rec.reasons,
        "provider_reason": rec.provider_reason,
        "ranked": [
            {
                "rank": i + 1,
                "tag": c.tag,
                "ref": c.ref,
                "score": c.score,
                "already_pulled": c.already_pulled,
                "eta_seconds": c.eta_seconds,
                "reasons": c.reasons,
                "score_breakdown": c.score_breakdown,
            }
            for i, c in enumerate(rec.survivors)
        ],
        "eliminated": [
            {
                "tag": c.tag,
                "ref": c.ref,
                "eliminated_by": c.eliminated_by,
                "reasons": c.eliminated_reasons,
            }
            for c in rec.eliminated
        ],
    }
