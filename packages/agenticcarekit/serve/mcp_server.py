"""The MCP server — the single biggest agent-native lever (brief §9).

Seven tools, no shell. An agent with only MCP access can diagnose a machine,
scaffold a project, describe it, run its eval, choose a model, and look up any
error code — the full ``ack`` loop, natively.

    init_project · add_capability · doctor · run_eval
    get_manifest · search_models · explain_error

Every tool is a thin wrapper over :mod:`agenticcarekit.serve.ops` — the same
functions the HTTP sidecar calls. One implementation, two transports; a fix
lands once.

Every tool returns the **same envelope** the CLI's ``--json`` prints::

    {"envelope_version": 1, "ok": true, "command": "doctor",
     "version": "0.1.0", "elapsed_ms": null, "data": {...}, "error": null}

and on failure ``ok: false`` with ``error`` = ``AckError.to_dict()`` — code,
message, why, fix, details. An agent never has to parse prose to find out what
to do next.

Launching (stdio transport)::

    ack serve --mcp
    python -m agenticcarekit.serve --mcp
    python -m agenticcarekit.serve.mcp_server        # equivalent

Claude Desktop / any MCP client, in its server config::

    {"command": "ack", "args": ["serve", "--mcp", "--path", "/path/to/project"]}

Nothing here touches the network at import or at startup. ``doctor`` and
``search_models`` probe only when *called*, and both take ``offline``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agenticcarekit.cli.output import envelope
from agenticcarekit.kernel.contracts import AckError

from . import ops

__all__ = ["TOOL_NAMES", "build_mcp_server", "build_tools", "main"]

#: The exact tool surface. Adding one is a product decision, not a refactor —
#: an agent's mental model of this toolkit is this list.
TOOL_NAMES = (
    "init_project",
    "add_capability",
    "doctor",
    "run_eval",
    "get_manifest",
    "search_models",
    "explain_error",
)

#: Environment variable naming the project root when the MCP client cannot
#: pass ``--path`` (some clients only support ``env``).
ROOT_ENV = "ACK_SERVE_ROOT"


def _envelope(command: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one tool body and wrap success or failure in the CLI envelope."""
    try:
        return envelope(command, data=fn())
    except AckError as err:
        return envelope(command, ok=False, error=err)


def build_tools(root: Path) -> dict[str, Callable[..., dict[str, Any]]]:
    """The seven tool functions, bound to ``root``.

    Returned as plain callables so they can be tested — and embedded — without
    an MCP transport in the way. :func:`build_mcp_server` registers exactly
    these.

    Example:
        >>> import tempfile
        >>> tools = build_tools(Path(tempfile.mkdtemp()))
        >>> sorted(tools) == sorted(TOOL_NAMES)
        True
        >>> tools["explain_error"](code="E203")["data"]["code"]
        'E203'
        >>> tools["explain_error"](code="E999")["error"]["code"]
        'E401'
    """

    def init_project(
        path: str,
        blueprint: str | None = None,
        model: str | None = None,
        pack: str | None = None,
        capabilities: str | None = None,
        name: str | None = None,
        git: bool = False,
    ) -> dict[str, Any]:
        """Generate a production-shaped agenticcarekit project.

        Probes the machine, picks a model that actually fits it, renders the
        blueprint, and writes ``ack.toml`` plus the agent surface
        (``AGENTS.md``, ``.cursor/rules/``, copilot instructions). Generation
        is deterministic: identical inputs give a byte-identical tree.

        No model is downloaded — the returned ``pull`` block names the exact
        ``ollama pull`` command instead of pretending it happened.

        Args:
            path: Where to generate. **Required** — relative paths resolve
                against the sidecar's root, never a process working directory.
            blueprint: ``on-device`` (fully offline intake summariser),
                ``care-copilot`` (tool-calling admin agent), or ``voice-care``
                (voice intake + scribe). Omit to let the machine decide.
            model: Force an Ollama tag, e.g. ``gemma4:e4b``. Omit to get the
                recommendation, which explains itself in ``plan.reasons``.
            pack: Domain pack, e.g. ``healthcare``.
            capabilities: Comma-separated, from: agents, extract, rag, voice.
            name: Project name (default: the directory name).
            git: Run ``git init`` in the generated tree. Off by default.

        Returns:
            The ``ack init --json`` envelope: the chosen ``plan`` with its
            reasons, every generated file, and the ``rerun`` command that
            reproduces it exactly.
        """
        return _envelope(
            "init",
            lambda: ops.init_project(
                root,
                path,
                blueprint=blueprint,
                model=model,
                pack=pack,
                capabilities=capabilities,
                name=name,
                git=git,
                offline=True,
            ),
        )

    def add_capability(capability: str, path: str | None = None) -> dict[str, Any]:
        """Enable a capability in a project's ``ack.toml``.

        Idempotent, and it preserves keys you or a human added to the file.

        Args:
            capability: One of ``agents``, ``extract``, ``rag``, ``voice``.
            path: Project directory (default: the sidecar's root).

        Returns:
            ``{"capability", "changed", "capabilities"}``. ``changed`` is
            false when it was already enabled — that is success, not an error.
        """
        return _envelope("add", lambda: ops.add_capability(root, capability, path))

    def doctor(offline: bool = False) -> dict[str, Any]:
        """Report this machine honestly, with problems as fixable error codes.

        Read this **before** guessing why something failed: it reports the OS,
        RAM, GPU/VRAM, free disk, whether Ollama is installed and running,
        which model tags are already pulled, and which provider API keys are
        present (presence only — a key value is never read or transmitted).

        Args:
            offline: Skip every network probe.

        Returns:
            ``{"facts": {...}, "problems": [{"code", "title", "what", "fix"}]}``.
            Each problem's ``fix`` is a literal command.
        """
        return _envelope("doctor", lambda: ops.doctor(offline=offline))

    def run_eval(path: str | None = None, offline: bool = True) -> dict[str, Any]:
        """Score a project against its committed golden set.

        Fails with **E601** when a golden set or the provider chain is
        genuinely missing. That failure is the honest answer; do not work
        around it by inventing a score.

        Args:
            path: Project directory (default: the sidecar's root).
            offline: Run against mocks with networking disabled.

        Returns:
            ``{"golden_set", "cases", "exact_match_rate", "judge_score_avg",
            "rows"}``.
        """
        return _envelope("eval", lambda: ops.run_eval(root, path, offline=offline))

    def get_manifest(path: str | None = None) -> dict[str, Any]:
        """Describe a generated project: config, tools, capabilities, files.

        The machine-readable answer to "what is this project?" — blueprint,
        pack, model refs, egress class and redactor, every ``@tool`` with its
        JSON schema and declared permissions, and the file list.

        Args:
            path: Project directory (default: the sidecar's root).

        Returns:
            The ``ack manifest --json`` payload. ``tool_notes`` lists modules
            that failed to import — describing a project never runs it.
        """
        return _envelope("manifest", lambda: ops.get_manifest(root, path))

    def search_models(
        modality: str | None = None,
        min_context_tokens: int | None = None,
        max_size_gb: float | None = None,
        include_hosted: bool = True,
        already_pulled_only: bool = False,
        offline: bool = False,
    ) -> dict[str, Any]:
        """Find a Gemma 4 model by what it can do — and what is already here.

        Use this before recommending a model. Native **audio input is E2B/E4B
        only**; the 12b/26b/31b tags are text+image with a 256K context.
        ``already_pulled`` tells you which tags need no download.

        Args:
            modality: Required input modality: ``text``, ``image`` or ``audio``.
            min_context_tokens: Minimum context window, e.g. ``131072``.
            max_size_gb: Largest on-disk size to consider.
            include_hosted: Include ``-cloud`` tags (these leave the machine).
            already_pulled_only: Only tags present on this machine now.
            offline: Do not probe for pulled tags (they report as empty).

        Returns:
            ``{"models": [...], "installed_tags": [...], "filters": {...}}``.
            Entries with ``verified: false`` are declared, untested.
        """
        return _envelope(
            "models",
            lambda: ops.search_models(
                modality=modality,
                min_context_tokens=min_context_tokens,
                max_size_gb=max_size_gb,
                include_hosted=include_hosted,
                already_pulled_only=already_pulled_only,
                offline=offline,
            ),
        )

    def explain_error(code: str | None = None) -> dict[str, Any]:
        """Look up an agenticcarekit error code: what, why, and the fix.

        Every error the toolkit raises carries a stable code (``E203``,
        ``E301``, ...). Call this with the code you were handed rather than
        guessing at a cause.

        Args:
            code: The code, e.g. ``E301``. Omit to list every registered code.

        Returns:
            ``{"code", "title", "what", "why", "fix"}`` — or ``{"codes": [...]}``
            when no code was given.
        """
        return _envelope("explain", lambda: ops.explain_error(code))

    return {
        "init_project": init_project,
        "add_capability": add_capability,
        "doctor": doctor,
        "run_eval": run_eval,
        "get_manifest": get_manifest,
        "search_models": search_models,
        "explain_error": explain_error,
    }


_INSTRUCTIONS = """\
agenticcarekit generates and operates open-model health AI projects that run
on the user's own machine, with a privacy boundary enforced at runtime.

Suggested loop:
  1. doctor()                     — read the machine before diagnosing anything
  2. search_models(modality=...)  — pick a model that fits it; audio is E2B/E4B only
  3. init_project(path=...)       — scaffold; the plan explains every choice
  4. get_manifest(path=...)       — see what you generated
  5. run_eval(path=...)           — score it; an E601 is an honest gap, not a bug
  6. explain_error(code=...)      — any code you are handed, before guessing

Every tool returns {ok, command, data, error}. On ok:false read error.fix — it
is a literal command. Never invent an error code; look it up.

No telemetry, ever.
"""


def build_mcp_server(root: Path | None = None) -> Any:
    """Build the MCP server exposing :data:`TOOL_NAMES` over stdio.

    Importing ``mcp`` is deferred to this call so the rest of the package —
    and ``ack``'s error message about the missing extra — works without it.
    """
    from mcp.server import MCPServer

    from agenticcarekit import __version__

    target = Path(root) if root is not None else Path(os.environ.get(ROOT_ENV) or Path.cwd())
    server = MCPServer(
        name="agenticcarekit",
        title="agenticcarekit",
        version=__version__,
        instructions=_INSTRUCTIONS,
    )
    for name, fn in build_tools(target).items():
        server.add_tool(fn, name=name)
    return server


def main() -> None:  # pragma: no cover - the transport is the process
    """``python -m agenticcarekit.serve.mcp_server`` — serve MCP over stdio."""
    build_mcp_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
