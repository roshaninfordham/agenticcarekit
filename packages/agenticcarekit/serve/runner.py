"""Starting the sidecar: bind rules, the honest banner, and the two transports.

Two rules live here and nowhere else:

**Loopback only, unless you say otherwise out loud.** The sidecar holds the
privacy boundary for every thin client on the machine. Binding it to
``0.0.0.0`` turns a laptop into a PHI relay, so a non-loopback host is refused
unless ``--allow-remote`` is passed *and* token auth is on.

**Honest startup.** The banner prints the address it actually bound, the path
to the token file, and the docs URL — measured facts, no progress theatre, no
telemetry line, because there is no telemetry to disclose.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path
from typing import Any

from agenticcarekit import __version__
from agenticcarekit.kernel.contracts import AckError

from . import DEFAULT_HOST, DEFAULT_PORT

__all__ = [
    "check_bind",
    "main",
    "parse_args",
    "run_http",
    "run_mcp",
    "startup_info",
    "startup_plan",
]

#: The optional dependencies the two transports need. They are imported inside
#: :func:`run_http` / :func:`run_mcp`, never at module import, so that this
#: module can be imported (and this error raised) without them.
SERVE_EXTRA = ("fastapi", "uvicorn", "sse-starlette", "mcp")


def _missing_extra(exc: ImportError) -> AckError:
    """Turn a missing optional dependency into a coded, fixable error.

    Example:
        >>> _missing_extra(ImportError("No module named 'uvicorn'")).fix
        'uv pip install "agenticcarekit[serve]"'
    """
    return AckError(
        "the sidecar needs the optional [serve] extra, which is not installed",
        code="E013",
        why=f"{type(exc).__name__}: {exc}",
        fix='uv pip install "agenticcarekit[serve]"',
        details={"missing": list(SERVE_EXTRA)},
    )


def is_loopback(host: str) -> bool:
    """True when ``host`` can only be reached from this machine.

    Example:
        >>> is_loopback("127.0.0.1"), is_loopback("::1"), is_loopback("localhost")
        (True, True, True)
        >>> is_loopback("0.0.0.0"), is_loopback("192.168.1.4")
        (False, False)
    """
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, *, allow_remote: bool, require_auth: bool = True) -> str:
    """Return ``host``, or refuse to bind it.

    Example:
        >>> check_bind("127.0.0.1", allow_remote=False)
        '127.0.0.1'
        >>> check_bind("0.0.0.0", allow_remote=False)
        Traceback (most recent call last):
            ...
        agenticcarekit.kernel.contracts.errors.AckError: refusing to bind the sidecar to 0.0.0.0
        >>> check_bind("0.0.0.0", allow_remote=True)
        '0.0.0.0'
    """
    if is_loopback(host) or (allow_remote and require_auth):
        return host
    if allow_remote and not require_auth:
        raise AckError(
            f"refusing to bind the sidecar to {host} without token auth",
            code="E303",
            why=(
                "--allow-remote exposes the policy boundary to the network; "
                "unauthenticated, it would relay PHI to anyone who can route to it."
            ),
            fix="drop the flag that disabled auth, or bind 127.0.0.1",
        )
    raise AckError(
        f"refusing to bind the sidecar to {host}",
        code="E303",
        why=(
            "the sidecar holds the privacy boundary for every client on this "
            "machine; a non-loopback bind exposes it to the network."
        ),
        fix=f"ack serve --host 127.0.0.1   # or, deliberately: --host {host} --allow-remote",
    )


def startup_plan(
    root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    mcp: bool = False,
) -> dict[str, Any]:
    """What ``ack serve`` *would* do — without binding or writing anything.

    The bind rule is checked (so a refused host fails here, loudly, before any
    socket exists) but no token is minted and no port is opened. This is what
    ``--dry-run`` runs: a CI job or an agent can validate a sidecar
    configuration without starting one.

    Example:
        >>> plan = startup_plan(Path("/srv/proj"), port=4422)
        >>> plan["url"], plan["would_serve"]
        ('http://127.0.0.1:4422', True)
        >>> plan["token_exists"]
        False
    """
    from .auth import TokenStore

    root = Path(root)
    if not mcp:
        host = check_bind(host, allow_remote=allow_remote)
    store = TokenStore(root)
    info = startup_info(root, host, port, token_path=store.path, mcp=mcp)
    info["would_serve"] = True
    info["token_exists"] = store.path.is_file()
    info["dry_run"] = True
    return info


def startup_info(
    root: Path,
    host: str,
    port: int,
    *,
    token_path: Path,
    mcp: bool = False,
) -> dict[str, Any]:
    """The facts the banner prints and ``--json`` returns. Never the token.

    Example:
        >>> info = startup_info(Path("/srv"), "127.0.0.1", 4422,
        ...                     token_path=Path("/srv/.ack/serve.token"))
        >>> info["url"], info["docs_url"]
        ('http://127.0.0.1:4422', 'http://127.0.0.1:4422/docs')
        >>> info["telemetry"]
        False
    """
    base = f"http://{host}:{port}"
    return {
        "mode": "mcp" if mcp else "http",
        "version": __version__,
        "root": str(root),
        "host": host,
        "port": port,
        "url": base,
        "docs_url": f"{base}/docs",
        "openapi_url": f"{base}/openapi.json",
        "token_path": str(token_path),
        "telemetry": False,
    }


def banner_lines(info: dict[str, Any]) -> list[str]:
    """The startup banner, one measured fact per line.

    Example:
        >>> info = startup_info(Path("/srv"), "127.0.0.1", 4422,
        ...                     token_path=Path("/srv/.ack/serve.token"))
        >>> banner_lines(info)[0]
        'listening   http://127.0.0.1:4422'
    """
    if info["mode"] == "mcp":
        return [
            "transport   stdio (MCP)",
            f"root        {info['root']}",
        ]
    return [
        f"listening   {info['url']}",
        f"docs        {info['docs_url']}",
        f"openapi     {info['openapi_url']}",
        f"token       {info['token_path']}   (mode 0600; send it as: Authorization: Bearer ...)",
        f"root        {info['root']}",
    ]


def run_http(
    root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Bind and serve HTTP. Blocks until the server stops.

    Returns the startup info once the server exits, so a caller can render a
    final envelope.
    """
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise _missing_extra(exc) from None
    from .auth import TokenStore

    root = Path(root)
    host = check_bind(host, allow_remote=allow_remote)
    store = TokenStore(root)
    store.ensure()
    app = create_app(root, store.read())
    info = startup_info(root, host, port, token_path=store.path)
    if not quiet:
        for line in banner_lines(info):
            print(f"  {line}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return info


def run_mcp(root: Path, *, quiet: bool = False) -> dict[str, Any]:
    """Serve MCP over stdio. Blocks until the client disconnects.

    stdout **is** the transport, so the banner goes to stderr. Anything else
    printed on stdout would corrupt the protocol.
    """
    from .auth import TokenStore
    from .mcp_server import build_mcp_server

    root = Path(root)
    try:
        server = build_mcp_server(root)
    except ImportError as exc:  # pragma: no cover - mcp ships with the extra
        raise _missing_extra(exc) from None
    info = startup_info(root, DEFAULT_HOST, DEFAULT_PORT, token_path=TokenStore(root).path, mcp=True)
    if not quiet:
        for line in banner_lines(info):
            print(f"  {line}", file=sys.stderr)
    server.run(transport="stdio")
    return info


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Argument parser for ``python -m agenticcarekit.serve``.

    Example:
        >>> ns = parse_args(["--mcp"])
        >>> ns.mcp, ns.host, ns.port
        (True, '127.0.0.1', 4422)
    """
    parser = argparse.ArgumentParser(
        prog="python -m agenticcarekit.serve",
        description="agenticcarekit sidecar: local HTTP (OpenAPI) or MCP over stdio.",
    )
    parser.add_argument("--path", default=None, help="Project root (default: cwd).")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (loopback only).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument("--mcp", action="store_true", help="Serve MCP over stdio instead.")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit a non-loopback bind. Exposes the policy boundary to the network.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be served and exit. Binds nothing, writes nothing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry
    """``python -m agenticcarekit.serve`` entry point."""
    ns = parse_args(argv)
    root = Path(ns.path).expanduser() if ns.path else Path.cwd()
    try:
        if ns.dry_run:
            plan = startup_plan(
                root, host=ns.host, port=ns.port, allow_remote=ns.allow_remote, mcp=ns.mcp
            )
            for line in banner_lines(plan):
                print(f"  {line}", file=sys.stderr)
        elif ns.mcp:
            run_mcp(root)
        else:
            run_http(root, host=ns.host, port=ns.port, allow_remote=ns.allow_remote)
    except AckError as err:
        print(err.render(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
