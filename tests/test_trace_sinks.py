"""Unit tests for ``JsonlSink`` and ``ConsoleSink``."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from agenticcarekit.kernel.contracts import EgressClass, TraceEvent
from agenticcarekit.kernel.trace import ConsoleSink, JsonlSink, Tracer
from rich.console import Console


def test_jsonl_sink_appends_one_line_per_event(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    sink = JsonlSink(path)
    t = Tracer(sinks=[sink], run_id="r1")

    t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
    t.emit("redaction", EgressClass.DEVICE, 0, {"redactor": "healthcare.phi"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert set(parsed.keys()) == {
            "ts",
            "run_id",
            "span_id",
            "parent_span_id",
            "kind",
            "egress",
            "bytes_out",
            "payload",
        }


def test_jsonl_sink_lines_round_trip_via_from_dict(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    sink = JsonlSink(path)
    t = Tracer(sinks=[sink], run_id="r1")
    original = t.emit("policy", EgressClass.DEVICE, 0, {"decision": "allow"})

    line = path.read_text(encoding="utf-8").splitlines()[0]
    restored = TraceEvent.from_dict(json.loads(line))

    assert restored == original


def test_jsonl_sink_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "trace.jsonl"
    sink = JsonlSink(path)
    t = Tracer(sinks=[sink])
    t.emit("model", EgressClass.DEVICE, 0, {})
    assert path.exists()


def test_jsonl_sink_is_deterministic_sorted_keys(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    sink = JsonlSink(path)
    t = Tracer(sinks=[sink], run_id="r1")
    t.emit("model", EgressClass.DEVICE, 0, {"b": 1, "a": 2})

    line = path.read_text(encoding="utf-8").splitlines()[0]
    # sorted keys, no whitespace drift (to_json's own contract)
    assert " " not in line
    keys = list(json.loads(line).keys())
    assert keys == sorted(keys)


def test_console_sink_prints_one_line_per_event_append_only() -> None:
    buf = StringIO()
    console = Console(file=buf, width=120, no_color=True, force_terminal=False)
    sink = ConsoleSink(console=console)
    t = Tracer(sinks=[sink], run_id="r1")

    t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
    t.emit("redaction", EgressClass.DEVICE, 0, {"count": 3})
    t.emit("policy", EgressClass.PUBLIC_CLOUD, 1234, {"decision": "deny"})

    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    assert "model" in lines[0]
    assert "redaction" in lines[1]
    assert "policy" in lines[2]
    assert "1234" in lines[2]


def test_console_sink_default_console_constructs() -> None:
    # Just verify the no-arg path builds a real rich.Console without error.
    sink = ConsoleSink()
    assert sink.console is not None
