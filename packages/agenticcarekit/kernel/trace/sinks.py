"""Trace sinks — where ``TraceEvent`` records go after emission.

Both sinks are append-only (invariant 9): ``JsonlSink`` appends one line
per event and flushes immediately (crash-safe — a process killed mid-run
loses at most the event currently being written, never corrupts prior
lines); ``ConsoleSink`` prints one compact line per event and never
redraws or clears previous output. Neither sink is a full-screen TUI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import TraceEvent

__all__ = ["ConsoleSink", "JsonlSink"]

_GLYPHS: dict[str, str] = {
    "model": "◆",  # ◆
    "tool": "▶",  # ▶
    "redaction": "▪",  # ▪
    "policy": "●",  # ●
    "error": "✗",  # ✗
}


class JsonlSink:
    """Appends each ``TraceEvent`` as one canonical JSON line to ``path``.

    The parent directory is created if needed. Every write opens in
    append mode and flushes before returning, so a crash mid-run never
    leaves a torn line for events already written.

    Example:
        >>> import tempfile, os
        >>> from agenticcarekit.kernel.contracts import EgressClass
        >>> from agenticcarekit.kernel.trace.tracer import Tracer
        >>> path = tempfile.mktemp()
        >>> sink = JsonlSink(path)
        >>> t = Tracer(sinks=[sink], run_id="r1")
        >>> _ = t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
        >>> lines = open(path, encoding="utf-8").read().splitlines()
        >>> len(lines)
        1
        >>> lines[0].startswith("{") and lines[0].endswith("}")
        True
        >>> os.remove(path)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        parent = self.path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: TraceEvent) -> None:
        """Append one canonical JSONL line for ``event``, flushed to disk."""
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.to_json())
            f.write("\n")
            f.flush()


class ConsoleSink:
    """Compact, append-only ``rich`` rendering: exactly one new line per
    event (ts delta since the first event, a kind glyph, egress class,
    ``bytes_out``, and a short payload summary).

    Never clears or rewrites prior lines (invariant 9) — safe to run
    alongside normal scrollback.

    Example:
        >>> from io import StringIO
        >>> from rich.console import Console
        >>> from agenticcarekit.kernel.contracts import EgressClass
        >>> from agenticcarekit.kernel.trace.tracer import Tracer
        >>> buf = StringIO()
        >>> sink = ConsoleSink(console=Console(file=buf, width=100, no_color=True))
        >>> t = Tracer(sinks=[sink], run_id="r1")
        >>> _ = t.emit("model", EgressClass.DEVICE, 0, {"model": "gemma4:e4b"})
        >>> _ = t.emit("policy", EgressClass.DEVICE, 0, {"decision": "allow"})
        >>> len([line for line in buf.getvalue().splitlines() if line.strip()])
        2
    """

    def __init__(self, console: Any | None = None) -> None:
        if console is None:
            from rich.console import Console

            console = Console()
        self.console = console
        self._start: float | None = None

    def write(self, event: TraceEvent) -> None:
        """Print exactly one new line summarizing ``event``."""
        if self._start is None:
            self._start = event.ts
        delta = event.ts - self._start
        glyph = _GLYPHS.get(event.kind, "?")
        summary = _summarize_payload(event.payload)
        self.console.print(
            f"[dim]+{delta:8.3f}s[/dim] {glyph} {event.kind:<9} "
            f"[cyan]{event.egress.value:<15}[/cyan] "
            f"{event.bytes_out:>6}B  {summary}"
        )


def _summarize_payload(payload: dict[str, Any], max_len: int = 80) -> str:
    """Render a payload dict as a short single-line ``key=value`` summary.

    Example:
        >>> _summarize_payload({"model": "gemma4:e4b", "duration_ms": 12.5})
        'duration_ms=12.5, model=gemma4:e4b'
    """
    parts = [f"{k}={v}" for k, v in sorted(payload.items())]
    text = ", ".join(parts)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text
