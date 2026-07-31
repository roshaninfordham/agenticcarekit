"""Contract 4 — ``TraceEvent``: one spine, four surfaces.

Every model call, tool call, redaction, and policy decision emits exactly
this shape. The debug console, audit log, eval harness, and demo UI all
read it — including the "0 bytes egressed" panel, which is just
``sum(e.bytes_out for e in events if e.egress != DEVICE) == 0``.

The wire format is JSONL; the schema is ``spec/schemas/trace-event.schema.json``.
The emitter and sinks live in ``agenticcarekit.kernel.trace`` (W-C).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .provider import EgressClass

__all__ = ["EventKind", "TraceEvent"]

EventKind = Literal["model", "tool", "redaction", "policy", "error"]


@dataclass(frozen=True)
class TraceEvent:
    """One trace record.

    Example:
        >>> e = TraceEvent(ts=0.0, run_id="r1", span_id="s1",
        ...                parent_span_id=None, kind="model",
        ...                egress=EgressClass.DEVICE, bytes_out=0,
        ...                payload={"model": "gemma4:e4b"})
        >>> json.loads(e.to_json())["kind"]
        'model'
    """

    ts: float
    run_id: str
    span_id: str
    parent_span_id: str | None
    kind: EventKind
    egress: EgressClass
    bytes_out: int          # powers the "0 bytes egressed" panel
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "run_id": self.run_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind,
            "egress": self.egress.value,
            "bytes_out": self.bytes_out,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        """Canonical JSONL line: sorted keys, no whitespace drift —
        determinism is invariant 4."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraceEvent:
        return TraceEvent(
            ts=d["ts"],
            run_id=d["run_id"],
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id"),
            kind=d["kind"],
            egress=EgressClass(d["egress"]),
            bytes_out=d["bytes_out"],
            payload=d.get("payload", {}),
        )
