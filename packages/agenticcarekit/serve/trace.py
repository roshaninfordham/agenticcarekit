"""The server's tracer and its SSE fan-out.

One :class:`agenticcarekit.kernel.trace.Tracer` per sidecar process. Every
policy decision, redaction and model call made on behalf of a thin client
lands in it, which is the whole point of Tier 2: the client gets an audit
trail it could not have produced itself, in the frozen ``TraceEvent`` shape
(Contract 4) and nothing else.

:class:`TraceHub` is a *sink* (it has ``write(event)``), so it plugs into the
kernel tracer through the documented seam rather than around it. Subscribers
are ``asyncio.Queue``s owned by an SSE request; events are handed to them with
``loop.call_soon_threadsafe`` because generation runs in FastAPI's worker
thread while the queue belongs to the event loop.

Nothing here writes payload *data* — the trace records decisions, never the
values a decision was about (see ``kernel/policy/THREATMODEL.md``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agenticcarekit.kernel.contracts import TraceEvent
from agenticcarekit.kernel.trace import Tracer, bytes_egressed

__all__ = ["TraceHub", "stream_events"]

#: How long the SSE generator waits for the next event before checking whether
#: the client is still there. Not a delay the user pays for — events are
#: delivered the moment they arrive; this only bounds shutdown latency.
POLL_SECONDS = 0.25


class TraceHub:
    """The sidecar's tracer plus a live fan-out to SSE subscribers.

    Example:
        >>> from agenticcarekit.kernel.contracts import EgressClass
        >>> hub = TraceHub(run_id="r1")
        >>> ev = hub.tracer.emit("policy", EgressClass.DEVICE, 0, {"decision": "allow"})
        >>> hub.snapshot()["count"]
        1
        >>> hub.snapshot()["events"][0]["kind"]
        'policy'
        >>> hub.snapshot()["bytes_egressed"]
        0
    """

    def __init__(self, run_id: str | None = None, *, max_events: int = 2000) -> None:
        #: The kernel tracer. Hand this to ``Policy(emit=...)`` adapters and to
        #: anything else that needs to emit — there is one per process.
        self.tracer = Tracer(sinks=[self], run_id=run_id)
        self.max_events = max_events
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[TraceEvent]]] = []

    # ── sink protocol ───────────────────────────────────────────────────

    def write(self, event: TraceEvent) -> None:
        """Sink entry point: fan one event out to every live subscriber.

        Called from whatever thread emitted the event (FastAPI runs sync
        endpoints in a worker thread), so the hand-off into each subscriber's
        loop goes through ``call_soon_threadsafe``. A subscriber whose loop has
        already closed is dropped rather than raising into the caller — a
        disconnected trace viewer must never break a generation.
        """
        for loop, queue in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # pragma: no cover - loop closed mid-flight
                self._subscribers = [s for s in self._subscribers if s[1] is not queue]

    # ── the policy adapter ──────────────────────────────────────────────

    def policy_emitter(self):
        """An ``emit`` callable for :class:`agenticcarekit.kernel.policy.Policy`.

        ``Policy`` builds a complete ``TraceEvent``; the hub re-stamps its
        identity fields (``ts``/``run_id``/``span_id``) through the process
        tracer so every event in one sidecar shares a run id and a single
        ordering. Kind, egress, ``bytes_out`` and payload pass through
        untouched — the contract shape is never extended or edited.

        Example:
            >>> from agenticcarekit.kernel.contracts import EgressClass
            >>> hub = TraceHub(run_id="r2")
            >>> emit = hub.policy_emitter()
            >>> emit(TraceEvent(ts=0.0, run_id="other", span_id="s", parent_span_id=None,
            ...                 kind="policy", egress=EgressClass.DEVICE, bytes_out=0,
            ...                 payload={"decision": "deny"}))
            >>> hub.tracer.events[-1].run_id
            'r2'
        """

        def emit(event: TraceEvent) -> None:
            self.tracer.emit(event.kind, event.egress, event.bytes_out, dict(event.payload))

        return emit

    # ── reads ───────────────────────────────────────────────────────────

    def events(self) -> list[TraceEvent]:
        """Every event this process has emitted, oldest first."""
        return list(self.tracer.events)

    def snapshot(self, limit: int | None = None) -> dict[str, Any]:
        """The ``GET /v1/trace`` payload: recent events plus the egress total."""
        events = self.events()
        recent = events[-limit:] if limit else events
        return {
            "run_id": self.tracer.run_id,
            "count": len(recent),
            "total": len(events),
            "bytes_egressed": bytes_egressed(events),
            "events": [e.to_dict() for e in recent],
        }

    # ── subscriptions ───────────────────────────────────────────────────

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[TraceEvent]:
        """Register a queue that receives every event emitted from now on."""
        queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
        self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TraceEvent]) -> None:
        """Drop a subscriber. Idempotent."""
        self._subscribers = [s for s in self._subscribers if s[1] is not queue]

    @property
    def subscriber_count(self) -> int:
        """How many SSE clients are currently attached."""
        return len(self._subscribers)


async def stream_events(
    hub: TraceHub,
    *,
    replay: bool = True,
    limit: int | None = None,
    idle_timeout: float | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield ``sse-starlette`` event dicts for every trace event.

    ``replay`` sends the events already recorded before the client connected
    (the default: an agent that attaches after a generation still sees what
    happened). ``limit`` closes the stream after N events and ``idle_timeout``
    closes it after N seconds with nothing to send — both exist so a client,
    a test, or a shell pipeline can get a *terminating* stream instead of one
    that must be killed. With neither, this is a live stream: it waits for the
    next event for as long as the client stays connected.

    Example:
        >>> import asyncio
        >>> from agenticcarekit.kernel.contracts import EgressClass
        >>> hub = TraceHub(run_id="r3")
        >>> _ = hub.tracer.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
        >>> async def first():
        ...     return [e async for e in stream_events(hub, limit=1)]
        >>> out = asyncio.run(first())
        >>> out[0]["event"]
        'trace'
        >>> '"kind":"model"' in out[0]["data"]
        True
    """
    loop = asyncio.get_running_loop()
    queue = hub.subscribe(loop)
    sent = 0
    waited = 0.0
    try:
        if replay:
            for event in hub.events():
                yield {"event": "trace", "data": event.to_json()}
                sent += 1
                if limit is not None and sent >= limit:
                    return
        while limit is None or sent < limit:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
            except TimeoutError:
                waited += POLL_SECONDS
                if idle_timeout is not None and waited >= idle_timeout:
                    return
                continue
            waited = 0.0
            yield {"event": "trace", "data": event.to_json()}
            sent += 1
    finally:
        hub.unsubscribe(queue)
