"""``kernel/trace`` — ``TraceEvent`` emission, sinks, and analysis (W-C).

Consumes ``TraceEvent``/``EventKind`` from ``agenticcarekit.kernel.contracts``
(Contract 4) and never extends that shape. New needs go to the contract
first, never a local patch.
"""

from .analysis import assert_zero_egress, bytes_egressed, read_jsonl
from .sinks import ConsoleSink, JsonlSink
from .tracer import Tracer

__all__ = [
    "ConsoleSink",
    "JsonlSink",
    "Tracer",
    "assert_zero_egress",
    "bytes_egressed",
    "read_jsonl",
]
