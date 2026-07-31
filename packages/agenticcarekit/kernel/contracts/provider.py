"""Contract 1 — ``Capabilities`` and the ``Provider`` protocol.

Providers declare what they can do; the runtime negotiates. Capability
negotiation is the highest-leverage abstraction in the design: it turns
"audio is E2B/E4B only" from a doc footnote into a startup error with a
fix attached (invariant 2: never silently degrade).

Nothing here hides the provider — every concrete provider exposes its raw
client, and the kernel is callable directly (invariant 3: ejectable).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "AudioPart",
    "Capabilities",
    "Chunk",
    "EgressClass",
    "GenerateRequest",
    "GenerateResponse",
    "ImageDetail",
    "ImagePart",
    "Message",
    "Modality",
    "Part",
    "Provider",
    "Role",
    "TextPart",
    "ToolCall",
    "Usage",
    "VISION_TOKEN_BUDGETS",
]


class Modality(StrEnum):
    """An input or output modality a model can consume or produce."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class EgressClass(StrEnum):
    """Where data goes when it reaches a provider. The privacy boundary
    (Contract 2) is defined in terms of these classes."""

    DEVICE = "device"                    # never leaves the machine
    TRUSTED_NETWORK = "trusted-network"  # self-hosted, user-controlled
    PUBLIC_CLOUD = "public-cloud"        # third-party API


#: Gemma 4 vision token budgets, exposed as named presets.
#: (quirk 5 in docs/brief.md §2 — raw budgets: 70, 140, 280, 560, 1120)
VISION_TOKEN_BUDGETS: dict[str, int] = {
    "minimal": 70,
    "caption": 140,
    "default": 280,
    "detail": 560,
    "ocr": 1120,
}

ImageDetail = Literal["minimal", "caption", "default", "detail", "ocr"]


@dataclass(frozen=True)
class Capabilities:
    """What a provider/model pair can do. Declared, never inferred.

    Example:
        >>> caps = Capabilities(
        ...     modalities_in=frozenset({Modality.TEXT, Modality.AUDIO}),
        ...     modalities_out=frozenset({Modality.TEXT}),
        ...     tool_calling=True, streaming=True,
        ...     context_tokens=131072, thinking=True,
        ...     egress=EgressClass.DEVICE,
        ... )
        >>> caps.missing(modalities_in=frozenset({Modality.IMAGE}))
        ['image input']
    """

    modalities_in: frozenset[Modality]
    modalities_out: frozenset[Modality]
    tool_calling: bool
    streaming: bool
    context_tokens: int
    thinking: bool
    egress: EgressClass

    def missing(
        self,
        *,
        modalities_in: frozenset[Modality] = frozenset(),
        modalities_out: frozenset[Modality] = frozenset(),
        tool_calling: bool = False,
        streaming: bool = False,
        context_tokens: int = 0,
        thinking: bool = False,
    ) -> list[str]:
        """Human-readable list of requirements this capability set lacks.

        Empty list means every requirement is met. The strings feed
        directly into ``CapabilityMismatch`` messages — they are part of
        the error contract, not decoration.
        """
        gaps: list[str] = []
        for m in sorted(modalities_in - self.modalities_in, key=lambda m: m.value):
            gaps.append(f"{m.value} input")
        for m in sorted(modalities_out - self.modalities_out, key=lambda m: m.value):
            gaps.append(f"{m.value} output")
        if tool_calling and not self.tool_calling:
            gaps.append("tool calling")
        if streaming and not self.streaming:
            gaps.append("streaming")
        if context_tokens > self.context_tokens:
            gaps.append(
                f"context window ({context_tokens} needed, {self.context_tokens} available)"
            )
        if thinking and not self.thinking:
            gaps.append("thinking")
        return gaps


# ── Messages ─────────────────────────────────────────────────────────────

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    """Image content. ``data`` is raw bytes or a filesystem path/base64 string.

    ``detail`` selects a Gemma 4 vision token budget preset
    (see ``VISION_TOKEN_BUDGETS``).
    """

    data: bytes | str
    detail: ImageDetail = "default"


@dataclass(frozen=True)
class AudioPart:
    """Audio content. ``data`` is raw bytes or a filesystem path/base64 string."""

    data: bytes | str
    format: str = "wav"


Part = TextPart | ImagePart | AudioPart


@dataclass(frozen=True)
class ToolCall:
    """A function call requested by the model (native function calling)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One conversation turn.

    ``thinking`` holds the model's thought block for assistant turns. The
    message builder strips prior-turn thought blocks from history
    automatically (quirk 3 — a silent correctness bug otherwise); keeping
    thinking OUT of ``parts`` is what makes that stripping structural
    rather than string surgery.
    """

    role: Role
    parts: tuple[Part, ...]
    thinking: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @staticmethod
    def text(role: Role, text: str) -> Message:
        """Convenience constructor for a plain text turn.

        Example:
            >>> Message.text("user", "hi").parts[0].text
            'hi'
        """
        return Message(role=role, parts=(TextPart(text),))

    def required_modalities(self) -> frozenset[Modality]:
        """The input modalities this message needs from a model."""
        mods = set()
        for p in self.parts:
            if isinstance(p, TextPart):
                mods.add(Modality.TEXT)
            elif isinstance(p, ImagePart):
                mods.add(Modality.IMAGE)
            elif isinstance(p, AudioPart):
                mods.add(Modality.AUDIO)
        return frozenset(mods)


# ── Requests and responses ───────────────────────────────────────────────


@dataclass(frozen=True)
class GenerateRequest:
    """A single generation request.

    Sampling fields default to ``None`` meaning "apply the model's known
    good defaults" (for Gemma 4: temperature=1.0, top_p=0.95, top_k=64 —
    quirk 1). Set a value only to override deliberately.

    ``think=True`` enables thinking (quirk 2: the provider injects the
    ``<|think|>`` token at the start of the system prompt).
    """

    messages: tuple[Message, ...]
    model: str | None = None
    tools: tuple[Any, ...] = ()  # tuple[ToolSpec, ...]; Any avoids an import cycle
    think: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()

    def required_modalities(self) -> frozenset[Modality]:
        """Union of input modalities across all messages."""
        mods: frozenset[Modality] = frozenset()
        for m in self.messages:
            mods |= m.required_modalities()
        return mods


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class GenerateResponse:
    """A completed generation. ``raw`` always carries the unmodified
    provider payload — the escape hatch that keeps this a toolkit, not a
    framework."""

    text: str
    thinking: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """One streaming increment. The final chunk has ``done=True`` and
    carries the assembled ``GenerateResponse``."""

    delta: str = ""
    thinking_delta: str = ""
    tool_call: ToolCall | None = None
    done: bool = False
    response: GenerateResponse | None = None


@runtime_checkable
class Provider(Protocol):
    """The provider contract. Concrete implementations live in
    ``agenticcarekit.kernel.providers`` (W-A); anything satisfying this
    protocol — including third-party plugins — plugs in identically.
    """

    name: str

    def capabilities(self) -> Capabilities: ...

    def generate(self, req: GenerateRequest) -> GenerateResponse: ...

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]: ...
