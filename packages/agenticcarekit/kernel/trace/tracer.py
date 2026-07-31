"""``Tracer`` — the single emission point for ``TraceEvent`` (Contract 4).

Every model call, tool call, redaction, and policy decision goes through
one ``Tracer.emit`` (directly, or via ``Tracer.span``). The tracer never
extends the frozen ``TraceEvent`` shape — it only assigns the identity
fields (``ts``, ``run_id``, ``span_id``) and passes the rest straight
through to sinks.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from agenticcarekit.kernel.contracts import EgressClass, EventKind, TraceEvent

__all__ = ["Tracer"]


@runtime_checkable
class _Sink(Protocol):
    """Anything with a ``write(event)`` method can be a sink."""

    def write(self, event: TraceEvent) -> None: ...


def _short_id() -> str:
    """A short, URL-safe span/run id.

    Example:
        >>> len(_short_id())
        12
    """
    return uuid.uuid4().hex[:12]


class Tracer:
    """Emits ``TraceEvent`` records into sinks and retains them in memory.

    Thread-safe: a lock guards both the in-memory ``events`` list and the
    sink fan-out, so concurrent callers (e.g. a tool-calling loop running
    alongside a background stream reader) never interleave writes or
    corrupt event ordering.

    Example:
        >>> t = Tracer(sinks=[], run_id="r1")
        >>> ev = t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
        >>> ev.kind, ev.run_id
        ('model', 'r1')
        >>> len(t.events)
        1
    """

    def __init__(self, sinks: list[_Sink], run_id: str | None = None) -> None:
        self.sinks: list[_Sink] = list(sinks)
        self.run_id: str = run_id or _short_id()
        self.events: list[TraceEvent] = []
        self._lock = threading.Lock()

    def _record(self, event: TraceEvent) -> TraceEvent:
        """Append to memory and fan out to every sink under one lock."""
        with self._lock:
            self.events.append(event)
            for sink in self.sinks:
                sink.write(event)
        return event

    def emit(
        self,
        kind: EventKind,
        egress: EgressClass,
        bytes_out: int,
        payload: dict[str, Any],
        parent_span_id: str | None = None,
    ) -> TraceEvent:
        """Create, record, and sink one ``TraceEvent``.

        Example:
            >>> t = Tracer(sinks=[])
            >>> ev = t.emit("policy", EgressClass.DEVICE, 0, {"decision": "allow"})
            >>> ev.egress is EgressClass.DEVICE
            True
        """
        event = TraceEvent(
            ts=time.time(),
            run_id=self.run_id,
            span_id=_short_id(),
            parent_span_id=parent_span_id,
            kind=kind,
            egress=egress,
            bytes_out=bytes_out,
            payload=dict(payload),
        )
        return self._record(event)

    @contextmanager
    def span(
        self,
        kind: EventKind,
        egress: EgressClass,
        payload: dict[str, Any],
    ) -> Iterator[str]:
        """Context manager that emits exactly one event on exit.

        Yields the ``span_id`` of the event that *will* be emitted, before
        it is emitted — so code nested inside the ``with`` block can pass
        that id as ``parent_span_id`` to nested ``emit``/``span`` calls.

        ``duration_ms`` (wall-clock time inside the block) is added to the
        payload automatically. If ``payload`` contains a ``bytes_out`` key
        it is used (and removed from the stored payload) as the event's
        ``bytes_out``; otherwise ``bytes_out`` defaults to 0.

        Example:
            >>> t = Tracer(sinks=[])
            >>> with t.span("tool", EgressClass.DEVICE, {"tool": "web_search"}) as span_id:
            ...     child = t.emit("model", EgressClass.DEVICE, 0, {}, parent_span_id=span_id)
            >>> t.events[-1].payload["duration_ms"] >= 0.0
            True
            >>> t.events[-1].span_id == child.parent_span_id
            True
        """
        span_id = _short_id()
        start = time.perf_counter()
        try:
            yield span_id
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            full_payload = dict(payload)
            bytes_out = full_payload.pop("bytes_out", 0)
            full_payload["duration_ms"] = duration_ms
            event = TraceEvent(
                ts=time.time(),
                run_id=self.run_id,
                span_id=span_id,
                parent_span_id=None,
                kind=kind,
                egress=egress,
                bytes_out=bytes_out,
                payload=full_payload,
            )
            self._record(event)
