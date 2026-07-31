"""The Gemma 4 model table — declared capabilities, never inferred.

Every fact here comes from ``docs/brief.md`` §2 (ground truth, verified
against Ollama's registry and Google's model card as of July 2026). Nothing
is extrapolated: where §2 is silent the entry carries a ``TODO(verify)``
marker rather than an invented number.

The table is what turns "audio is E2B/E4B only" from a doc footnote into a
startup error with a fix attached (invariant 2).
"""

from __future__ import annotations

from agenticcarekit.kernel.contracts import (
    Capabilities,
    CapabilityMismatch,
    EgressClass,
    GenerateRequest,
    Modality,
)

__all__ = [
    "GEMMA4_MODELS",
    "MODEL_SIZES_GB",
    "UNKNOWN_LOCAL",
    "audio_capable_tags",
    "capabilities_for",
    "ensure_supported",
]

_TEXT_IMAGE_AUDIO = frozenset({Modality.TEXT, Modality.IMAGE, Modality.AUDIO})
_TEXT_IMAGE = frozenset({Modality.TEXT, Modality.IMAGE})
#: Output is text only on every Gemma 4 variant — there is no native speech
#: output (brief §2). Voice output is a separate provider (W-D).
_TEXT_ONLY = frozenset({Modality.TEXT})

_CTX_128K = 131_072
_CTX_256K = 262_144


def _caps(
    modalities_in: frozenset[Modality],
    context_tokens: int,
    egress: EgressClass = EgressClass.DEVICE,
) -> Capabilities:
    """Build a Gemma 4 capability record.

    Every Gemma 4 tag declares text-only output, native function calling,
    streaming and thinking (brief §2, quirks 2/6/7); only the input
    modalities, the context window and the egress class differ.

    Example:
        >>> _caps(_TEXT_IMAGE, _CTX_256K).context_tokens
        262144
    """
    return Capabilities(
        modalities_in=modalities_in,
        modalities_out=_TEXT_ONLY,
        tool_calling=True,
        streaming=True,
        context_tokens=context_tokens,
        thinking=True,
        egress=egress,
    )


#: Tag → declared capabilities. ``-mlx`` variants (Apple Silicon) mirror
#: their base tag exactly; ``-cloud`` tags are hosted, so their egress class
#: is ``public-cloud`` — that single field is what the privacy boundary
#: (Contract 2) reads.
GEMMA4_MODELS: dict[str, Capabilities] = {
    # E2B / E4B — the only tags with native audio input.
    "gemma4:e2b": _caps(_TEXT_IMAGE_AUDIO, _CTX_128K),
    "gemma4:e4b": _caps(_TEXT_IMAGE_AUDIO, _CTX_128K),
    "gemma4:e2b-mlx": _caps(_TEXT_IMAGE_AUDIO, _CTX_128K),
    "gemma4:e4b-mlx": _caps(_TEXT_IMAGE_AUDIO, _CTX_128K),
    # Larger tags — text + image, 256K context.
    "gemma4:12b": _caps(_TEXT_IMAGE, _CTX_256K),
    "gemma4:26b": _caps(_TEXT_IMAGE, _CTX_256K),
    "gemma4:31b": _caps(_TEXT_IMAGE, _CTX_256K),
    # Hosted tags — no download, and egress leaves the machine.
    # TODO(verify): brief §2 states `gemma4:cloud` is hosted but not which
    # weights back it; the entry below mirrors the dense 31b it is served
    # alongside. Confirm against the registry before claiming otherwise.
    "gemma4:cloud": _caps(_TEXT_IMAGE, _CTX_256K, EgressClass.PUBLIC_CLOUD),
    "gemma4:31b-cloud": _caps(_TEXT_IMAGE, _CTX_256K, EgressClass.PUBLIC_CLOUD),
}

#: On-disk size in GB (brief §2). Hosted ``-cloud`` tags are deliberately
#: absent: a missing entry means "nothing to download", which is what the
#: disk-space filter in the recommendation engine (W-G) needs to know.
#: TODO(verify): §2 gives no separate size for ``-mlx`` builds; they mirror
#: their base tag here.
MODEL_SIZES_GB: dict[str, float] = {
    "gemma4:e2b": 7.2,
    "gemma4:e4b": 9.6,
    "gemma4:e2b-mlx": 7.2,
    "gemma4:e4b-mlx": 9.6,
    "gemma4:12b": 7.6,
    "gemma4:26b": 18.0,
    "gemma4:31b": 20.0,
}

#: Deliberately conservative fallback for tags that are not in the table.
#: Declaring less than a model can do costs a loud, fixable error; declaring
#: more costs a silent wrong answer (invariant 2).
UNKNOWN_LOCAL = Capabilities(
    modalities_in=_TEXT_ONLY,
    modalities_out=_TEXT_ONLY,
    tool_calling=True,
    streaming=True,
    context_tokens=8_192,
    thinking=False,
    egress=EgressClass.DEVICE,
)


def audio_capable_tags() -> list[str]:
    """Sorted tags that declare native audio input.

    This list is the payload of every E203 audio error — the point of the
    error is that it names the models that would work.

    Example:
        >>> audio_capable_tags()
        ['gemma4:e2b', 'gemma4:e2b-mlx', 'gemma4:e4b', 'gemma4:e4b-mlx']
    """
    return sorted(
        tag for tag, caps in GEMMA4_MODELS.items() if Modality.AUDIO in caps.modalities_in
    )


def capabilities_for(tag: str, default: Capabilities | None = None) -> Capabilities:
    """Declared capabilities for a model tag.

    Unknown tags get ``UNKNOWN_LOCAL`` (text-only) unless a ``default`` is
    supplied — a provider may always declare its own.

    Example:
        >>> capabilities_for("gemma4:e4b-mlx").context_tokens
        131072
        >>> sorted(capabilities_for("llama9:1t").modalities_in)
        [<Modality.TEXT: 'text'>]
    """
    caps = GEMMA4_MODELS.get(tag)
    if caps is not None:
        return caps
    return default if default is not None else UNKNOWN_LOCAL


def _pick_code(gaps: list[str]) -> str:
    """Map capability gaps to the registered error code that names them.

    Codes come from ``spec/errors.json``: E203 input modality, E204 output
    modality, E202 tool calling, E201 context window.

    Example:
        >>> _pick_code(["audio input"])
        'E203'
        >>> _pick_code(["tool calling"])
        'E202'
    """
    if any(g.endswith(" input") for g in gaps):
        return "E203"
    if any(g.endswith(" output") for g in gaps):
        return "E204"
    if "tool calling" in gaps:
        return "E202"
    if any(g.startswith("context window") for g in gaps):
        return "E201"
    return "E200"


def ensure_supported(model: str, caps: Capabilities, req: GenerateRequest) -> None:
    """Pre-network capability check. Raises before a byte is sent.

    Compares what the request needs (input modalities; tool calling when
    tools are attached) against what the model *declares*. A gap is a
    ``CapabilityMismatch`` naming the model, the gaps, and the tags that
    would work — never a silent degrade (invariant 2).

    Example:
        >>> from agenticcarekit.kernel.contracts import AudioPart, Message
        >>> req = GenerateRequest(
        ...     messages=(Message("user", (AudioPart(b"RIFF"),)),))
        >>> ensure_supported("gemma4:31b", GEMMA4_MODELS["gemma4:31b"], req)
        Traceback (most recent call last):
        ...
        agenticcarekit.kernel.contracts.errors.CapabilityMismatch: gemma4:31b does not support audio input
        >>> ensure_supported("gemma4:e4b", GEMMA4_MODELS["gemma4:e4b"], req) is None
        True
    """
    gaps = caps.missing(
        modalities_in=req.required_modalities(),
        tool_calling=bool(req.tools),
    )
    if not gaps:
        return
    candidates = audio_capable_tags()
    code = _pick_code(gaps)
    why = (
        f"the request needs {', '.join(gaps)}; {model} declares "
        f"{', '.join(sorted(m.value for m in caps.modalities_in))} input."
    )
    if "audio input" in gaps:
        why = "Native audio input is available on E2B and E4B only: " + ", ".join(candidates)
    raise CapabilityMismatch(
        f"{model} does not support {', '.join(gaps)}",
        code=code,
        missing=gaps,
        candidates=candidates,
        why=why,
        fix="ack init --model gemma4:e4b-mlx",
    )
