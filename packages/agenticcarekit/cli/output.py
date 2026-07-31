"""Terminal and ``--json`` output policy for every ``ack`` command.

Rules encoded here, once, so no command can drift from them:

* **Header on every human-facing run** — one line, name + version +
  "No telemetry, ever." (invariant 6).
* **``--json`` everywhere** — a stable envelope, no rich markup, printed
  as the *only* thing on stdout (invariant 10).
* **Append-only output** — at most one transient live region, never a
  full-screen TUI (invariant 9).
* **``NO_COLOR`` / ``FORCE_COLOR``** honoured, and the layout degrades
  below 80 columns.
* **Never fake progress** — elapsed times are measured, not simulated
  (invariant 7).

The envelope is versioned and its keys are stable::

    {"ok": true, "command": "doctor", "version": "0.1.0",
     "elapsed_ms": 182.4, "data": {...}, "error": null}
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from rich.console import Console

from agenticcarekit import __version__
from agenticcarekit.kernel.contracts import AckError

__all__ = [
    "ENVELOPE_VERSION",
    "Emitter",
    "NARROW_WIDTH",
    "envelope",
    "make_console",
]

#: Bumped when the ``--json`` envelope shape changes (documented in spec/).
ENVELOPE_VERSION = 1

#: Below this width the renderers switch to their compact form.
NARROW_WIDTH = 80

_HEADER = f"agenticcarekit {__version__} · ack — No telemetry, ever."


def make_console(*, stderr: bool = False) -> Console:
    """Build a :class:`rich.console.Console` honouring the colour env vars.

    ``NO_COLOR`` wins over ``FORCE_COLOR`` — a user who asked for no colour
    gets no colour, whatever else is set.

    Example:
        >>> import os
        >>> os.environ["NO_COLOR"] = "1"
        >>> make_console().no_color
        True
        >>> del os.environ["NO_COLOR"]
    """
    no_color = bool(os.environ.get("NO_COLOR"))
    force_color = bool(os.environ.get("FORCE_COLOR")) and not no_color
    width = None
    columns = os.environ.get("COLUMNS")
    if columns and columns.isdigit():
        width = int(columns)
    return Console(
        stderr=stderr,
        no_color=no_color,
        force_terminal=True if force_color else None,
        color_system="auto" if not no_color else None,
        width=width,
        soft_wrap=False,
        highlight=False,
        emoji=False,
    )


def envelope(
    command: str,
    *,
    ok: bool = True,
    data: Any = None,
    error: AckError | None = None,
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    """The one ``--json`` shape every command emits.

    Example:
        >>> e = envelope("doctor", data={"os": "Darwin"})
        >>> sorted(e)
        ['command', 'data', 'elapsed_ms', 'envelope_version', 'error', 'ok', 'version']
        >>> e["ok"], e["command"]
        (True, 'doctor')
    """
    return {
        "envelope_version": ENVELOPE_VERSION,
        "ok": ok,
        "command": command,
        "version": __version__,
        "elapsed_ms": elapsed_ms,
        "data": data,
        "error": error.to_dict() if error is not None else None,
    }


class Emitter:
    """Per-invocation output channel. One is created by each command.

    In ``--json`` mode nothing but the envelope reaches stdout; human
    chatter is suppressed entirely rather than redirected, so piping is
    always safe.
    """

    def __init__(self, command: str, json_mode: bool = False) -> None:
        self.command = command
        self.json_mode = json_mode
        self.console = make_console()
        self._start = time.monotonic()
        self._header_printed = False
        if not json_mode:
            self.header()

    # ── plumbing ────────────────────────────────────────────────────────

    @property
    def width(self) -> int:
        """Effective terminal width (never smaller than 20)."""
        return max(20, self.console.width)

    @property
    def narrow(self) -> bool:
        """True when the layout must degrade (below 80 columns)."""
        return self.width < NARROW_WIDTH

    def elapsed_ms(self) -> float:
        """Milliseconds since this emitter was created — measured, never faked."""
        return (time.monotonic() - self._start) * 1000.0

    def header(self) -> None:
        """Print the one-line header (once per run, human mode only)."""
        if self.json_mode or self._header_printed:
            return
        self._header_printed = True
        self.console.print(_HEADER, style="dim")

    # ── human output ────────────────────────────────────────────────────

    def print(self, *args: Any, **kw: Any) -> None:
        """Append a line of human output (no-op under ``--json``)."""
        if not self.json_mode:
            self.console.print(*args, **kw)

    def blank(self) -> None:
        if not self.json_mode:
            self.console.print("")

    def rule(self, title: str) -> None:
        if self.json_mode:
            return
        self.console.print(f"  [bold]{title}[/bold]")

    #: Column at which the ``←`` annotation starts on a wide terminal.
    NOTE_COLUMN = 4 + 14 + 24

    def field(self, label: str, value: str, note: str | None = None) -> None:
        """A ``label   value   ← note`` row, degrading below 80 columns.

        The ``←`` annotation is where the recommendation engine teaches
        (brief §7.3): it carries the reason, not decoration. Long reasons
        are wrapped and hanging-indented under the arrow rather than left
        to reflow to column zero.
        """
        if self.json_mode:
            return
        if self.narrow:
            self.console.print(f"    {label:<13}[bold]{value}[/bold]")
            self._note_lines(note, indent=6)
            return
        left = f"    {label:<14}{value}"
        if not note:
            self.console.print(left)
            return
        gap = max(2, self.NOTE_COLUMN - len(left))
        self._note_lines(note, indent=len(left) + gap, first_prefix=left + " " * gap)

    def note_continuation(self, note: str) -> None:
        """A second ``←`` line, aligned under the previous :meth:`field`."""
        if self.json_mode:
            return
        self._note_lines(note, indent=6 if self.narrow else self.NOTE_COLUMN)

    def _note_lines(self, note: str | None, *, indent: int, first_prefix: str = "") -> None:
        """Print an arrow-annotated note, wrapped to the terminal width."""
        if not note:
            return
        import textwrap

        available = max(16, self.width - indent - 2)
        lines = textwrap.wrap(note, width=available) or [note]
        head = first_prefix if first_prefix else " " * indent
        self.console.print(f"{head}[dim]←[/dim] {lines[0]}")
        for extra in lines[1:]:
            self.console.print(f"{' ' * (indent + 2)}{extra}")

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
        """A plain, append-only table. No boxes below 80 columns."""
        if self.json_mode:
            return
        from rich.table import Table

        box = None if self.narrow else None  # append-only: never a heavy box
        t = Table(box=box, show_header=True, header_style="bold", pad_edge=False, padding=(0, 1))
        for h in headers:
            t.add_column(h, overflow="fold")
        for r in rows:
            t.add_row(*[str(c) for c in r])
        self.console.print(t)

    @contextmanager
    def live_status(self, message: str) -> Iterator[Any]:
        """The single transient live region allowed per run (invariant 9).

        Degrades to a plain printed line when output is not a terminal or
        under ``--json``, so logs stay append-only.
        """
        if self.json_mode or not self.console.is_terminal:
            if not self.json_mode:
                self.console.print(f"  {message}")

            class _Null:
                def update(self, _msg: str) -> None:
                    return None

            yield _Null()
            return
        with self.console.status(message, spinner="line") as status:
            yield status

    # ── terminal states ─────────────────────────────────────────────────

    def ok(self, data: Any = None, *, elapsed: bool = False) -> dict[str, Any]:
        """Emit success. Returns the envelope (also printed under ``--json``)."""
        env = envelope(
            self.command,
            data=data,
            elapsed_ms=round(self.elapsed_ms(), 3) if elapsed else None,
        )
        if self.json_mode:
            sys.stdout.write(json.dumps(env, sort_keys=True, default=str) + "\n")
        elif elapsed:
            self.console.print(f"\n  [dim]done in {self.elapsed_ms() / 1000:.2f}s[/dim]")
        return env

    def fail(self, err: AckError, *, elapsed: bool = False) -> dict[str, Any]:
        """Emit an :class:`AckError` in the canonical shape and return the envelope."""
        env = envelope(
            self.command,
            ok=False,
            error=err,
            elapsed_ms=round(self.elapsed_ms(), 3) if elapsed else None,
        )
        if self.json_mode:
            sys.stdout.write(json.dumps(env, sort_keys=True, default=str) + "\n")
        else:
            self.console.print("")
            self.console.print(f"  [bold red]✗ {err.code}[/bold red]  {err.message}")
            if err.why:
                self.console.print(f"          [dim]{err.why}[/dim]")
            if err.fix:
                self.console.print("")
                self.console.print(f"          [bold]{err.fix}[/bold]")
            self.console.print("")
            self.console.print(f"  [dim]ack explain {err.code}[/dim]")
        return env
