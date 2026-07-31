"""``ack serve`` — the local sidecar (brief §3, Tier 2) and the MCP server (§9).

This is the architectural move that makes "available everywhere" real without
five hand-maintained ports: **the policy boundary, redaction and the trace all
live in this one process.** A thin Go, Swift or Rust client speaks HTTP (or
MCP) to it and therefore *cannot* accidentally bypass PHI enforcement, because
it never touches the enforcement path — it has no provider handle at all. Ports
become convenience, not correctness surface.

Two transports, one implementation:

* **HTTP** — ``agenticcarekit.serve.app.create_app`` builds an OpenAPI-documented
  FastAPI app on loopback. The generated OpenAPI spec is what Tier-2 thin
  clients are generated from.
* **MCP** — ``agenticcarekit.serve.mcp_server`` exposes seven tools over stdio,
  so an agent drives the whole toolkit natively with no shell.

Both call the same functions in :mod:`agenticcarekit.serve.ops`, and both return
the same ``--json`` envelope the CLI emits (``agenticcarekit.cli.output.envelope``).
There is one implementation of every operation, never two.

Nothing in this package touches the network at import time or at startup
(invariant 5). Imports of ``fastapi``/``mcp`` are deferred to the submodules
that need them, so ``import agenticcarekit.serve`` works without the optional
``[serve]`` extra installed.

Launching:

    ack serve                       # HTTP on http://127.0.0.1:4422
    ack serve --mcp                 # MCP over stdio
    python -m agenticcarekit.serve  # same, without the console script
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TokenStore",
    "TraceHub",
    "create_app",
    "build_tools",
    "build_mcp_server",
    "run_http",
    "run_mcp",
]

#: Loopback, always. A non-loopback bind is refused unless it is asked for
#: explicitly (``--allow-remote``) — see :func:`agenticcarekit.serve.runner.check_bind`.
DEFAULT_HOST = "127.0.0.1"

#: The sidecar's port. Deliberately unusual so it does not collide with the
#: usual 8000/8080 crowd on a developer laptop.
DEFAULT_PORT = 4422

_LAZY = {
    "TokenStore": ("agenticcarekit.serve.auth", "TokenStore"),
    "TraceHub": ("agenticcarekit.serve.trace", "TraceHub"),
    "create_app": ("agenticcarekit.serve.app", "create_app"),
    "build_tools": ("agenticcarekit.serve.mcp_server", "build_tools"),
    "build_mcp_server": ("agenticcarekit.serve.mcp_server", "build_mcp_server"),
    "run_http": ("agenticcarekit.serve.runner", "run_http"),
    "run_mcp": ("agenticcarekit.serve.runner", "run_mcp"),
}


def __getattr__(name: str) -> Any:
    """Resolve the public names lazily.

    Keeping ``fastapi``/``mcp`` out of this module's import path is what lets
    ``agenticcarekit.serve`` be imported (and its error message read) on an
    installation without the ``[serve]`` extra.

    Example:
        >>> from agenticcarekit import serve
        >>> serve.DEFAULT_PORT
        4422
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    import importlib

    return getattr(importlib.import_module(module_name), attr)
