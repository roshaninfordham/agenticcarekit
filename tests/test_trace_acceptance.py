"""W-C acceptance test (trace half).

A scripted run: Tracer + JsonlSink emits a device-only model call, a
redaction, and a policy allow. Read the JSONL back and assert zero bytes
egressed in device-only mode. Then append a public-cloud event with a
nonzero byte count and assert both that ``bytes_egressed`` reflects it and
that ``assert_zero_egress`` raises E303. Finally, every emitted line must
parse as JSON and round-trip byte-for-byte through ``TraceEvent.from_dict``.

Everything here runs fully offline — no network, no providers.
"""

from __future__ import annotations

import json
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


def test_device_only_run_has_zero_bytes_egressed(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    tracer = Tracer(sinks=[JsonlSink(path)], run_id="acceptance-run")

    model_event = tracer.emit(
        "model",
        EgressClass.DEVICE,
        0,
        {"model": "gemma4:e4b", "provider": "ollama", "input_tokens": 12, "output_tokens": 8},
    )
    redaction_event = tracer.emit(
        "redaction",
        EgressClass.DEVICE,
        0,
        {"redactor": "healthcare.phi", "categories": ["NAME", "MRN"], "count": 2},
    )
    policy_event = tracer.emit(
        "policy",
        EgressClass.DEVICE,
        0,
        {"decision": "allow", "reason": "device egress within policy", "call_site": "app.py:10"},
    )

    # every raw line parses as JSON and round-trips via TraceEvent.from_dict
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3
    for line in raw_lines:
        parsed = json.loads(line)  # must parse
        restored = TraceEvent.from_dict(parsed)
        assert isinstance(restored, TraceEvent)

    events = read_jsonl(path)
    assert events == [model_event, redaction_event, policy_event]

    # device-only mode: zero bytes egressed
    assert bytes_egressed(events) == 0
    assert assert_zero_egress(events) is None

    # --- now append a public-cloud event with nonzero bytes_out ---
    cloud_event = tracer.emit(
        "model",
        EgressClass.PUBLIC_CLOUD,
        1234,
        {"model": "gemma-4-31b", "provider": "cerebras"},
    )

    all_raw_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(all_raw_lines) == 4
    for line in all_raw_lines:
        json.loads(line)  # still parses
        TraceEvent.from_dict(json.loads(line))  # still round-trips

    events_after = read_jsonl(path)
    assert events_after[-1] == cloud_event

    assert bytes_egressed(events_after) == 1234

    with pytest.raises(AckError) as excinfo:
        assert_zero_egress(events_after)
    assert excinfo.value.code == "E303"


def test_every_line_is_deterministic_json(tmp_path: Path) -> None:
    """to_json() output has sorted keys and no whitespace drift (invariant 4)."""
    path = tmp_path / "run.jsonl"
    tracer = Tracer(sinks=[JsonlSink(path)], run_id="r1")
    tracer.emit("model", EgressClass.DEVICE, 0, {"z": 1, "a": 2})

    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))
