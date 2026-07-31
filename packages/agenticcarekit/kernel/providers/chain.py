"""Fallback chains — primary first, fallback on failure, never a downgrade.

A fallback is a resilience mechanism, not a capability escape hatch. Two
rules keep it honest:

1. A request must pass the **primary's** pre-network check. If the primary
   cannot do the job, that is a configuration error the user must see — the
   chain does not quietly route around it.
2. If the primary fails at runtime and the fallback lacks something the
   request needs, the chain raises ``CapabilityMismatch`` rather than
   sending a degraded request (invariant 2: never silently degrade).

``capabilities()`` answers the intersection question honestly: what the
chain can guarantee is what *both* providers declare — except egress, which
is the **broadest** of the two, because that is what may actually happen.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from agenticcarekit.kernel.contracts import (
    AckError,
    Capabilities,
    CapabilityMismatch,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Provider,
    TraceEvent,
)

from .models import audio_capable_tags

__all__ = ["FallbackChain"]

#: Failures a fallback is allowed to answer: anything the provider layer
#: coded (E1xx transport/model errors), plus raw transport faults from an
#: ejected client. Capability mismatches are deliberately NOT in here.
_RETRYABLE = (AckError, httpx.HTTPError, TimeoutError, ConnectionError)

_EGRESS_ORDER = {
    EgressClass.DEVICE: 0,
    EgressClass.TRUSTED_NETWORK: 1,
    EgressClass.PUBLIC_CLOUD: 2,
}


def _broadest(a: EgressClass, b: EgressClass) -> EgressClass:
    """The riskier of two egress classes — what the chain must declare.

    Example:
        >>> _broadest(EgressClass.DEVICE, EgressClass.PUBLIC_CLOUD)
        <EgressClass.PUBLIC_CLOUD: 'public-cloud'>
    """
    return a if _EGRESS_ORDER[a] >= _EGRESS_ORDER[b] else b


def _model_of(provider: Provider) -> str:
    """Best available model name for a provider, for error and trace text.

    Example:
        >>> from .mock import MockProvider
        >>> _model_of(MockProvider())
        'mock'
    """
    return str(getattr(provider, "model", "") or provider.name)


class FallbackChain:
    """Try ``primary``; on a runtime failure, try ``fallback``.

    Args:
        primary: the provider that should normally answer.
        fallback: the provider used when the primary errors or times out.
        emit: optional trace hook. It receives an ``error`` event for the
            primary failure followed by a ``model`` event recording the
            fallback decision — so "why did this answer come from the cloud?"
            is answerable from the trace alone.

    Example:
        >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message, GenerateResponse
        >>> from .mock import MockProvider
        >>> class Flaky(MockProvider):
        ...     name = "flaky"
        ...     def generate(self, req):
        ...         raise TimeoutError("primary timed out")
        >>> events = []
        >>> chain = FallbackChain(Flaky(), MockProvider([GenerateResponse(text="from fallback")]),
        ...                       emit=events.append)
        >>> chain.generate(GenerateRequest(messages=(Message.text("user", "hi"),))).text
        'from fallback'
        >>> [e.kind for e in events]
        ['error', 'model']
    """

    def __init__(
        self,
        primary: Provider,
        fallback: Provider,
        *,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.emit = emit
        self.name = f"{primary.name}->{fallback.name}"
        self.run_id = uuid.uuid4().hex[:12]
        #: The primary's raw client, so the escape hatch survives wrapping.
        self.client = getattr(primary, "client", None)

    def capabilities(self) -> Capabilities:
        """What the chain can guarantee: the intersection, with the broadest egress.

        Example:
            >>> from .mock import MockProvider
            >>> from .cerebras import CerebrasProvider
            >>> caps = FallbackChain(MockProvider(), CerebrasProvider("gemma-4-31b")).capabilities()
            >>> sorted(m.value for m in caps.modalities_in)
            ['text']
            >>> caps.egress
            <EgressClass.PUBLIC_CLOUD: 'public-cloud'>
        """
        a, b = self.primary.capabilities(), self.fallback.capabilities()
        return Capabilities(
            modalities_in=a.modalities_in & b.modalities_in,
            modalities_out=a.modalities_out & b.modalities_out,
            tool_calling=a.tool_calling and b.tool_calling,
            streaming=a.streaming and b.streaming,
            context_tokens=min(a.context_tokens, b.context_tokens),
            thinking=a.thinking and b.thinking,
            egress=_broadest(a.egress, b.egress),
        )

    def _event(self, kind: str, egress: EgressClass, payload: dict[str, Any]) -> None:
        """Emit one trace event, if a hook was supplied."""
        if self.emit is None:
            return
        self.emit(
            TraceEvent(
                ts=time.time(),
                run_id=self.run_id,
                span_id=uuid.uuid4().hex[:12],
                parent_span_id=None,
                kind=kind,  # type: ignore[arg-type]
                egress=egress,
                bytes_out=0,  # the providers account for their own bytes
                payload=payload,
            )
        )

    def _guard_fallback(self, req: GenerateRequest, cause: Exception) -> None:
        """Refuse to degrade: the fallback must meet the request too.

        Example:
            >>> from agenticcarekit.kernel.contracts import AudioPart, Capabilities, Message, Modality
            >>> from .mock import MockProvider
            >>> text_only = Capabilities(
            ...     modalities_in=frozenset({Modality.TEXT}),
            ...     modalities_out=frozenset({Modality.TEXT}), tool_calling=True,
            ...     streaming=True, context_tokens=8192, thinking=False,
            ...     egress=EgressClass.PUBLIC_CLOUD)
            >>> chain = FallbackChain(MockProvider(), MockProvider(capabilities=text_only))
            >>> req = GenerateRequest(messages=(Message("user", (AudioPart(b"x"),)),))
            >>> chain._guard_fallback(req, TimeoutError("boom"))
            Traceback (most recent call last):
            ...
            agenticcarekit.kernel.contracts.errors.CapabilityMismatch: fallback mock cannot serve this request after mock failed: audio input
        """
        caps = self.fallback.capabilities()
        gaps = caps.missing(
            modalities_in=req.required_modalities(),
            tool_calling=bool(req.tools),
        )
        if not gaps:
            return
        raise CapabilityMismatch(
            f"fallback {_model_of(self.fallback)} cannot serve this request after "
            f"{_model_of(self.primary)} failed: {', '.join(gaps)}",
            code="E203" if any(g.endswith(" input") for g in gaps) else "E202",
            missing=gaps,
            candidates=audio_capable_tags(),
            why=(
                f"the primary failed ({type(cause).__name__}: {cause}) and the fallback "
                "does not declare what this request needs — degrading silently would "
                "produce a wrong answer instead of an error."
            ),
            fix='set a capable fallback in ack.toml: [model] fallback = "ollama:gemma4:e4b"',
        )

    def _switch(self, req: GenerateRequest, exc: Exception) -> None:
        """Record the failure, verify the fallback, record the switch."""
        code = getattr(exc, "code", None)
        self._event(
            "error",
            self.primary.capabilities().egress,
            {
                "provider": self.primary.name,
                "model": _model_of(self.primary),
                "error": f"{type(exc).__name__}: {exc}",
                "code": code,
            },
        )
        self._guard_fallback(req, exc)
        self._event(
            "model",
            self.fallback.capabilities().egress,
            {
                "provider": self.fallback.name,
                "model": _model_of(self.fallback),
                "reason": f"fallback after {self.primary.name} failed ({type(exc).__name__})",
            },
        )

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        """Primary, then fallback. Capability errors from the primary propagate.

        Example:
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, GenerateResponse, Message
            >>> from .mock import MockProvider
            >>> ok = FallbackChain(MockProvider([GenerateResponse(text="primary")]),
            ...                    MockProvider([GenerateResponse(text="fallback")]))
            >>> ok.generate(GenerateRequest(messages=(Message.text("user", "hi"),))).text
            'primary'
        """
        try:
            return self.primary.generate(req)
        except CapabilityMismatch:
            # A mismatch is a configuration error, not a transient failure.
            raise
        except _RETRYABLE as exc:
            self._switch(req, exc)
            return self.fallback.generate(req)

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]:
        """Stream from the primary; fall back only if it failed before emitting.

        Once a chunk has reached the caller, switching providers would splice
        two different completions together — so a mid-stream failure is
        raised, not papered over.

        Example:
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, GenerateResponse, Message
            >>> from .mock import MockProvider
            >>> class Dead(MockProvider):
            ...     name = "dead"
            ...     def stream(self, req):
            ...         raise TimeoutError("no daemon")
            ...         yield
            >>> chain = FallbackChain(Dead(), MockProvider([GenerateResponse(text="ok")]))
            >>> list(chain.stream(GenerateRequest(messages=(Message.text("user", "?"),))))[-1].response.text
            'ok'
        """
        emitted = False
        try:
            for chunk in self.primary.stream(req):
                emitted = True
                yield chunk
            return
        except CapabilityMismatch:
            raise
        except _RETRYABLE as exc:
            if emitted:
                raise
            self._switch(req, exc)
        yield from self.fallback.stream(req)
