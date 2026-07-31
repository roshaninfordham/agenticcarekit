"""The model catalog — brief §2, verbatim. Do not invent entries.

Every number here comes from the ground-truth table in ``docs/brief.md``
§2 (current as of July 2026, verified against Ollama's registry and
Google's model card). Where §2 is silent the field carries an explicit
``TODO(verify)`` rather than an extrapolation.

The ``blurb`` on each entry is not decoration: it is the reason string the
recommendation engine prints next to the ``←`` in the plan screen, and the
fixture corpus asserts it exactly (brief §7.4 — "a correct recommendation
with a wrong explanation is a failed test").

Example:
    >>> CATALOG["gemma4:e4b"].size_gb
    9.6
    >>> sorted(t for t in CATALOG if "audio" in CATALOG[t].modalities_in)
    ['gemma4:e2b', 'gemma4:e2b-mlx', 'gemma4:e4b', 'gemma4:e4b-mlx']
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CATALOG", "ModelEntry", "audio_capable_tags", "local_tags", "hosted_tags"]

_CTX_128K = 131_072
_CTX_256K = 262_144

_TIA = frozenset({"text", "image", "audio"})
_TI = frozenset({"text", "image"})


@dataclass(frozen=True)
class ModelEntry:
    """One row of the brief §2 table, plus what ranking needs to know."""

    tag: str
    provider: str
    size_gb: float | None  # None ⇒ hosted, nothing to download
    context_tokens: int
    modalities_in: frozenset[str]
    tool_calling: bool
    quality_tier: int  # 31b > 26b > 12b > e4b > e2b
    blurb: str
    mlx: bool = False
    hosted: bool = False
    verified: bool = True

    @property
    def ref(self) -> str:
        """The ``provider:model`` reference written into ``ack.toml``.

        Example:
            >>> CATALOG["gemma4:e4b-mlx"].ref
            'ollama:gemma4:e4b-mlx'
        """
        return f"{self.provider}:{self.tag}"

    @property
    def context_k(self) -> int:
        """Context window in thousands, for reason strings."""
        return self.context_tokens // 1024


def _e(
    tag: str,
    size_gb: float | None,
    ctx: int,
    mods: frozenset[str],
    tier: int,
    blurb: str,
    *,
    provider: str = "ollama",
    mlx: bool = False,
    hosted: bool = False,
    verified: bool = True,
) -> ModelEntry:
    return ModelEntry(
        tag=tag,
        provider=provider,
        size_gb=size_gb,
        context_tokens=ctx,
        modalities_in=mods,
        tool_calling=True,  # native function calling on every Gemma 4 tag (§2 quirk 7)
        quality_tier=tier,
        blurb=blurb,
        mlx=mlx,
        hosted=hosted,
        verified=verified,
    )


#: Tag → entry. Ordered as in brief §2 so a diff against the table is trivial.
CATALOG: dict[str, ModelEntry] = {
    "gemma4:e2b": _e(
        "gemma4:e2b", 7.2, _CTX_128K, _TIA, 1,
        "e2b: native audio input, ~2.3B effective parameters — the smallest Gemma 4",
    ),
    "gemma4:e4b": _e(
        "gemma4:e4b", 9.6, _CTX_128K, _TIA, 2,
        "e4b: native audio input, ~4.5B effective parameters",
    ),
    "gemma4:e2b-mlx": _e(
        "gemma4:e2b-mlx", 7.2, _CTX_128K, _TIA, 1,
        "e2b: native audio input, ~2.3B effective parameters — the smallest Gemma 4",
        mlx=True,
    ),
    "gemma4:e4b-mlx": _e(
        "gemma4:e4b-mlx", 9.6, _CTX_128K, _TIA, 2,
        "e4b: native audio input, ~4.5B effective parameters",
        mlx=True,
    ),
    "gemma4:12b": _e(
        "gemma4:12b", 7.6, _CTX_256K, _TI, 3,
        "12b: 256K context in 7.6 GB — the best size-to-quality trade in the family",
    ),
    "gemma4:26b": _e(
        "gemma4:26b", 18.0, _CTX_256K, _TI, 4,
        "26b: mixture-of-experts with ~3.8B active parameters at 256K context",
    ),
    "gemma4:31b": _e(
        "gemma4:31b", 20.0, _CTX_256K, _TI, 5,
        "31b: the dense flagship — highest quality tier in the family",
    ),
    # Hosted. No download, and egress leaves the machine.
    # TODO(verify): §2 states these are hosted but not which weights back
    # `gemma4:cloud`; both entries mirror the dense 31b they are served
    # alongside. Confirm against the registry before claiming otherwise.
    "gemma4:cloud": _e(
        "gemma4:cloud", None, _CTX_256K, _TI, 5,
        "gemma4:cloud: hosted, no download — runs off-device",
        hosted=True, verified=False,
    ),
    "gemma4:31b-cloud": _e(
        "gemma4:31b-cloud", None, _CTX_256K, _TI, 5,
        "31b-cloud: hosted 31b, no download — runs off-device",
        hosted=True, verified=False,
    ),
    # The hosted primary named by the fallback rule (brief §7.2) and by the
    # `ack.toml` example in §5.
    # TODO(verify): capability fields mirror the dense 31b Cerebras serves.
    "gemma-4-31b": _e(
        "gemma-4-31b", None, _CTX_256K, _TI, 5,
        "cerebras: hosted Gemma 4 31B — nothing to download, runs off-device",
        provider="cerebras", hosted=True, verified=False,
    ),
}

#: The hosted model the fallback rule promotes to primary (brief §7.2).
HOSTED_PRIMARY = "gemma-4-31b"


def audio_capable_tags() -> list[str]:
    """Sorted tags with native audio input — the payload of every E203.

    Example:
        >>> audio_capable_tags()
        ['gemma4:e2b', 'gemma4:e2b-mlx', 'gemma4:e4b', 'gemma4:e4b-mlx']
    """
    return sorted(t for t, e in CATALOG.items() if "audio" in e.modalities_in)


def local_tags() -> list[str]:
    """Sorted tags that download and run on-device.

    Example:
        >>> "gemma4:31b" in local_tags()
        True
    """
    return sorted(t for t, e in CATALOG.items() if not e.hosted)


def hosted_tags() -> list[str]:
    """Sorted hosted tags (nothing to download, egress leaves the machine)."""
    return sorted(t for t, e in CATALOG.items() if e.hosted)
