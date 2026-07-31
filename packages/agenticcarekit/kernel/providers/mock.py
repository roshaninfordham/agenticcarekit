"""Mock provider — what ``ack demo --offline`` actually runs on.

Deterministic by construction: the same canned responses in the same order,
no clock, no randomness, no socket (invariants 4 and 5). It records every
request it received in ``.requests``, which makes it the natural fixture for
asserting what a caller *would* have sent.

Capability negotiation is enforced here too. A mock that accepts anything
would let a blueprint pass its tests and fail on a real model — the exact
silent degrade invariant 2 exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterator

from agenticcarekit.kernel.contracts import (
    Capabilities,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Modality,
    Usage,
)

from .models import ensure_supported

__all__ = ["DEVICE_FULL", "MockProvider"]

#: Fully-featured, on-device: everything Gemma 4's richest tag declares.
DEVICE_FULL = Capabilities(
    modalities_in=frozenset({Modality.TEXT, Modality.IMAGE, Modality.AUDIO}),
    modalities_out=frozenset({Modality.TEXT}),
    tool_calling=True,
    streaming=True,
    context_tokens=262_144,
    thinking=True,
    egress=EgressClass.DEVICE,
)

_DEFAULT_TEXT = "This is a mock response. No model was called and no bytes left the machine."


class MockProvider:
    """Canned responses, cycled in order. No network, ever.

    Args:
        responses: the responses to return, cycled. Empty/omitted gives one
            obviously-fake response, so a demo never looks like real output.
        capabilities: what this mock declares. Defaults to a fully-featured
            device-class model; narrow it to test negotiation failures.

    Example:
        >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
        >>> p = MockProvider([GenerateResponse(text="one"), GenerateResponse(text="two")])
        >>> req = GenerateRequest(messages=(Message.text("user", "hi"),))
        >>> [p.generate(req).text for _ in range(3)]
        ['one', 'two', 'one']
        >>> len(p.requests)
        3
        >>> p.capabilities().egress
        <EgressClass.DEVICE: 'device'>
    """

    name = "mock"

    def __init__(
        self,
        responses: list[GenerateResponse] | None = None,
        capabilities: Capabilities | None = None,
        *,
        model: str = "mock",
    ) -> None:
        self.model = model
        self.responses: list[GenerateResponse] = list(responses) if responses else [
            GenerateResponse(text=_DEFAULT_TEXT, model=model, usage=Usage(0, 0))
        ]
        self._capabilities = capabilities or DEVICE_FULL
        #: Every request this provider was handed, in order.
        self.requests: list[GenerateRequest] = []
        self._index = 0
        #: No client to expose — a mock has no provider behind it. The
        #: attribute exists so callers can check it uniformly.
        self.client = None

    def capabilities(self) -> Capabilities:
        """Declared capabilities.

        Example:
            >>> MockProvider().capabilities().tool_calling
            True
        """
        return self._capabilities

    def check(self, req: GenerateRequest) -> None:
        """Same pre-flight check a real provider runs.

        Example:
            >>> from agenticcarekit.kernel.contracts import AudioPart, Message
            >>> text_only = Capabilities(
            ...     modalities_in=frozenset({Modality.TEXT}),
            ...     modalities_out=frozenset({Modality.TEXT}),
            ...     tool_calling=False, streaming=True, context_tokens=8192,
            ...     thinking=False, egress=EgressClass.DEVICE)
            >>> req = GenerateRequest(messages=(Message("user", (AudioPart(b"x"),)),))
            >>> MockProvider(capabilities=text_only).check(req)
            Traceback (most recent call last):
            ...
            agenticcarekit.kernel.contracts.errors.CapabilityMismatch: mock does not support audio input
        """
        ensure_supported(self.model, self._capabilities, req)

    def _next(self) -> GenerateResponse:
        """Next canned response, cycling.

        Example:
            >>> p = MockProvider([GenerateResponse(text="a")])
            >>> p._next().text
            'a'
        """
        resp = self.responses[self._index % len(self.responses)]
        self._index += 1
        return resp

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        """Record the request, return the next canned response.

        Example:
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> p = MockProvider()
            >>> p.generate(GenerateRequest(messages=(Message.text("user", "hi"),))).text
            'This is a mock response. No model was called and no bytes left the machine.'
        """
        self.check(req)
        self.requests.append(req)
        return self._next()

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]:
        """Stream the next canned response word by word, then a done chunk.

        Example:
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> p = MockProvider([GenerateResponse(text="two words")])
            >>> chunks = list(p.stream(GenerateRequest(messages=(Message.text("user", "?"),))))
            >>> ([c.delta for c in chunks if not c.done], chunks[-1].done)
            (['two', ' words'], True)
        """
        self.check(req)
        self.requests.append(req)
        resp = self._next()
        if resp.thinking:
            yield Chunk(thinking_delta=resp.thinking)
        words = resp.text.split(" ")
        for i, word in enumerate(words):
            if not word and i:
                continue
            yield Chunk(delta=word if i == 0 else " " + word)
        for call in resp.tool_calls:
            yield Chunk(tool_call=call)
        yield Chunk(done=True, response=resp)
