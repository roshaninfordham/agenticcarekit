"""``python -m agenticcarekit.serve`` — run the sidecar without the console script.

    python -m agenticcarekit.serve                  # HTTP on http://127.0.0.1:4422
    python -m agenticcarekit.serve --mcp            # MCP over stdio
    python -m agenticcarekit.serve --path ./proj    # serve a specific project

Equivalent to ``ack serve``; this form exists because MCP clients launch a
command, and ``python -m ...`` is the one form that works from any virtualenv
without a console script on PATH.
"""

from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
