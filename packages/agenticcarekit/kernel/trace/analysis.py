"""Reading back trace JSONL and computing egress totals.

``bytes_egressed`` is the arithmetic behind the "0 bytes egressed" panel;
``assert_zero_egress`` is the enforcement wrapper that raises when a
device-only run is not actually clean.
"""

from __future__ import annotations

import json
from pathlib import Path

from agenticcarekit.kernel.contracts import AckError, EgressClass, TraceEvent

__all__ = ["assert_zero_egress", "bytes_egressed", "read_jsonl"]


def read_jsonl(path: str | Path) -> list[TraceEvent]:
    """Read every line of a trace JSONL file back into ``TraceEvent``\\ s.

    Blank lines are skipped. Each line must round-trip through
    ``TraceEvent.from_dict`` — a malformed line is a bug in whatever wrote
    the file, and raises naturally (``KeyError``/``json.JSONDecodeError``).

    Example:
        >>> import tempfile, os
        >>> from agenticcarekit.kernel.trace.tracer import Tracer
        >>> from agenticcarekit.kernel.trace.sinks import JsonlSink
        >>> path = tempfile.mktemp()
        >>> t = Tracer(sinks=[JsonlSink(path)], run_id="r1")
        >>> _ = t.emit("model", EgressClass.DEVICE, 0, {})
        >>> events = read_jsonl(path)
        >>> len(events), events[0].run_id
        (1, 'r1')
        >>> os.remove(path)
    """
    events: list[TraceEvent] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(TraceEvent.from_dict(json.loads(line)))
    return events


def bytes_egressed(events: list[TraceEvent]) -> int:
    """Total bytes that left the device across ``events``.

    Sums ``bytes_out`` for every event whose egress class is not
    ``DEVICE`` — the exact definition the "0 bytes egressed" panel is
    built on.

    Example:
        >>> e1 = TraceEvent(0.0, "r", "s1", None, "model", EgressClass.DEVICE, 0, {})
        >>> e2 = TraceEvent(0.0, "r", "s2", None, "model", EgressClass.PUBLIC_CLOUD, 1234, {})
        >>> bytes_egressed([e1, e2])
        1234
        >>> bytes_egressed([e1])
        0
    """
    return sum(e.bytes_out for e in events if e.egress != EgressClass.DEVICE)


def assert_zero_egress(events: list[TraceEvent]) -> None:
    """Raise ``AckError`` E303 if any non-device event carries a nonzero
    ``bytes_out``; otherwise return ``None``.

    The offending events are summarized (kind, egress, span id, bytes) in
    ``AckError.details["offenders"]`` — a vague policy error is one nobody
    fixes.

    Example:
        >>> ok = TraceEvent(0.0, "r", "s1", None, "model", EgressClass.DEVICE, 0, {})
        >>> assert_zero_egress([ok]) is None
        True
        >>> bad = TraceEvent(0.0, "r", "s2", None, "model", EgressClass.PUBLIC_CLOUD, 1234, {})
        >>> assert_zero_egress([bad])
        Traceback (most recent call last):
            ...
        agenticcarekit.kernel.contracts.errors.AckError: egress attempted above configured limit: 1 event(s), 1234 bytes
    """
    offenders = [e for e in events if e.egress != EgressClass.DEVICE and e.bytes_out > 0]
    if not offenders:
        return None
    total = sum(e.bytes_out for e in offenders)
    summary = "; ".join(
        f"{e.kind}@{e.egress.value} span={e.span_id} bytes={e.bytes_out}" for e in offenders
    )
    raise AckError(
        f"egress attempted above configured limit: {len(offenders)} event(s), {total} bytes",
        code="E303",
        why="device-only mode requires zero bytes egressed to any non-device provider.",
        fix="use a device/trusted provider, or deliberately raise [policy] egress in ack.toml",
        details={"offenders": summary},
    )
