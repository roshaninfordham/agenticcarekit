"""Unit tests for ``agenticcarekit.kernel.trace.Tracer``."""

from __future__ import annotations

import threading

from agenticcarekit.kernel.contracts import EgressClass, TraceEvent
from agenticcarekit.kernel.trace import Tracer


def test_emit_assigns_identity_fields() -> None:
    t = Tracer(sinks=[], run_id="run-1")
    ev = t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})

    assert isinstance(ev, TraceEvent)
    assert ev.run_id == "run-1"
    assert ev.kind == "model"
    assert ev.egress is EgressClass.DEVICE
    assert ev.bytes_out == 0
    assert ev.payload == {"model": "gemma4:e4b"}
    assert ev.span_id
    assert ev.ts > 0
    assert t.events == [ev]


def test_emit_generates_run_id_when_absent() -> None:
    t = Tracer(sinks=[])
    assert t.run_id


def test_emit_distinct_span_ids() -> None:
    t = Tracer(sinks=[])
    a = t.emit("model", EgressClass.DEVICE, 0, {})
    b = t.emit("model", EgressClass.DEVICE, 0, {})
    assert a.span_id != b.span_id


def test_emit_parent_span_id_passthrough() -> None:
    t = Tracer(sinks=[])
    parent = t.emit("tool", EgressClass.DEVICE, 0, {})
    child = t.emit("model", EgressClass.DEVICE, 0, {}, parent_span_id=parent.span_id)
    assert child.parent_span_id == parent.span_id


def test_span_emits_one_event_with_duration() -> None:
    t = Tracer(sinks=[])
    with t.span("tool", EgressClass.DEVICE, {"tool": "web_search"}):
        pass
    assert len(t.events) == 1
    ev = t.events[0]
    assert ev.kind == "tool"
    assert ev.payload["tool"] == "web_search"
    assert "duration_ms" in ev.payload
    assert ev.payload["duration_ms"] >= 0.0


def test_span_yields_id_usable_as_parent() -> None:
    t = Tracer(sinks=[])
    with t.span("tool", EgressClass.DEVICE, {}) as span_id:
        child = t.emit("model", EgressClass.DEVICE, 0, {}, parent_span_id=span_id)
    tool_event = t.events[-1]
    assert tool_event.kind == "tool"
    assert tool_event.span_id == span_id
    assert child.parent_span_id == span_id


def test_span_extracts_bytes_out_from_payload() -> None:
    t = Tracer(sinks=[])
    with t.span("model", EgressClass.PUBLIC_CLOUD, {"bytes_out": 42}):
        pass
    ev = t.events[-1]
    assert ev.bytes_out == 42
    assert "bytes_out" not in ev.payload


def test_span_emits_even_on_exception() -> None:
    t = Tracer(sinks=[])
    try:
        with t.span("error", EgressClass.DEVICE, {"note": "boom"}):
            raise ValueError("boom")
    except ValueError:
        pass
    assert len(t.events) == 1
    assert t.events[0].kind == "error"


def test_concurrent_emit_is_thread_safe() -> None:
    t = Tracer(sinks=[])
    n_threads = 8
    per_thread = 50

    def worker() -> None:
        for _ in range(per_thread):
            t.emit("model", EgressClass.DEVICE, 0, {})

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(t.events) == n_threads * per_thread
    span_ids = {e.span_id for e in t.events}
    assert len(span_ids) == n_threads * per_thread


def test_multiple_sinks_all_receive_event() -> None:
    received: list[list[TraceEvent]] = [[], []]

    class Recorder:
        def __init__(self, bucket: list[TraceEvent]) -> None:
            self.bucket = bucket

        def write(self, event: TraceEvent) -> None:
            self.bucket.append(event)

    t = Tracer(sinks=[Recorder(received[0]), Recorder(received[1])])
    ev = t.emit("model", EgressClass.DEVICE, 0, {})

    assert received[0] == [ev]
    assert received[1] == [ev]
