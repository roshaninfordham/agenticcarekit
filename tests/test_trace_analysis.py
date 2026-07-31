"""Unit tests for ``agenticcarekit.kernel.trace.analysis``."""

from __future__ import annotations

from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import AckError, EgressClass, TraceEvent
from agenticcarekit.kernel.trace import (
    JsonlSink,
    Tracer,
    assert_zero_egress,
    bytes_egressed,
    read_jsonl,
)


def _event(egress: EgressClass, bytes_out: int, span_id: str = "s1") -> TraceEvent:
    return TraceEvent(
        ts=0.0,
        run_id="r",
        span_id=span_id,
        parent_span_id=None,
        kind="model",
        egress=egress,
        bytes_out=bytes_out,
        payload={},
    )


def test_bytes_egressed_ignores_device_events() -> None:
    events = [_event(EgressClass.DEVICE, 999)]
    assert bytes_egressed(events) == 0


def test_bytes_egressed_sums_non_device_events() -> None:
    events = [
        _event(EgressClass.DEVICE, 0, "s1"),
        _event(EgressClass.PUBLIC_CLOUD, 1234, "s2"),
        _event(EgressClass.TRUSTED_NETWORK, 66, "s3"),
    ]
    assert bytes_egressed(events) == 1300


def test_bytes_egressed_empty_list() -> None:
    assert bytes_egressed([]) == 0


def test_assert_zero_egress_passes_when_clean() -> None:
    events = [_event(EgressClass.DEVICE, 0), _event(EgressClass.PUBLIC_CLOUD, 0)]
    assert assert_zero_egress(events) is None


def test_assert_zero_egress_raises_e303_with_summary() -> None:
    events = [_event(EgressClass.PUBLIC_CLOUD, 1234, "abc123")]
    with pytest.raises(AckError) as excinfo:
        assert_zero_egress(events)
    err = excinfo.value
    assert err.code == "E303"
    assert "1234" in err.message
    assert "abc123" in err.details["offenders"]


def test_read_jsonl_round_trips_every_line(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    t = Tracer(sinks=[JsonlSink(path)], run_id="r1")
    e1 = t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
    e2 = t.emit("redaction", EgressClass.DEVICE, 0, {"redactor": "healthcare.phi"})
    e3 = t.emit("policy", EgressClass.PUBLIC_CLOUD, 1234, {"decision": "allow"})

    events = read_jsonl(path)
    assert events == [e1, e2, e3]


def test_read_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    t = Tracer(sinks=[JsonlSink(path)])
    t.emit("model", EgressClass.DEVICE, 0, {})
    with path.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    assert len(read_jsonl(path)) == 1
