"""``ack`` — the agenticcarekit command line (W-G).

Commands: ``init add swap eject doctor eval demo sync manifest explain new
check``.

Nothing here imports a parallel workstream (providers, policy, trace,
blueprints, packs) at module import time: ``ack init``/``doctor``/
``explain``/``new`` must work in a checkout where those directories are
still empty. Where a real implementation is needed at runtime it is
imported lazily and its absence degrades to a registered ``AckError``.

No telemetry, ever.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:  # pragma: no cover - thin re-export for console scripts
    """Console-script entry point (``ack`` / ``agenticcarekit``)."""
    from agenticcarekit.cli.main import main as _main

    _main()
