"""``ack`` — the agenticcarekit command line.

    ack init      generate a project: probe, recommend, render, pull
    ack doctor    honest environment report, with fixes as error codes
    ack explain   the long form of any error code
    ack new       scaffold one of the five extension points
    ack manifest  machine-readable description of a generated project
    ack sync      reconcile the tree against ack.toml
    ack add       enable a capability
    ack swap      swap model / fallback / pack / redactor / egress
    ack eject     inline a packaged abstraction into your source
    ack check     lint + fast selftest, under 30 seconds
    ack eval      score against the project's golden set
    ack demo      run the project's demo (``--offline`` uses mocks)
    ack serve     the local sidecar: HTTP + OpenAPI, or MCP over stdio

Every command accepts ``--json``. ``NO_COLOR`` and ``FORCE_COLOR`` are
honoured, the layout degrades below 80 columns, output is append-only, and
there is no telemetry — ever.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from agenticcarekit import __version__
from agenticcarekit.kernel.contracts import AckError, error_registry
from agenticcarekit.kernel.contracts import explain as explain_code

from .checks import run_check, run_demo, run_eval_command
from .flows import (
    do_pull,
    doctor_report,
    generate_project,
    machine_facts,
    plan,
    render_plan,
    render_why,
    require_project,
    rerun_command,
)
from .output import Emitter
from .project_ops import (
    EJECTABLES,
    KNOWN_CAPABILITIES,
    SWAPPABLE,
    add_capability,
    build_manifest,
    eject,
    swap,
    sync_project,
)
from .recommend import explain_ranking
from .scaffolds import KINDS, scaffold

__all__ = ["app", "main"]

app = typer.Typer(
    name="ack",
    help="agenticcarekit — the open-model stack for health AI. No telemetry, ever.",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

# ── shared option types ──────────────────────────────────────────────────

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output (stable schema).")]
PathOpt = Annotated[str | None, typer.Option("--path", help="Project directory (default: .).")]
OfflineOpt = Annotated[bool, typer.Option("--offline", help="No network: mocks and local only.")]


def _emit(
    command: str,
    json_mode: bool,
    fn: Callable[[Emitter], Any],
    *,
    elapsed: bool = False,
) -> dict[str, Any]:
    """Run a command body, rendering success or an ``AckError`` uniformly.

    Returns the emitted envelope so a command can set a non-zero process
    exit code from its own result (``check`` and ``demo`` do: an agent's
    verification loop reads the shell status, not just the JSON).
    """
    em = Emitter(command, json_mode)
    try:
        data = fn(em)
    except AckError as err:
        em.fail(err, elapsed=elapsed)
        raise typer.Exit(code=1) from None
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        em.fail(
            AckError(
                "interrupted",
                code="E101",
                why="you pressed Ctrl-C; nothing was left in a half-written state.",
                fix="re-run the same command — work resumes where it stopped",
            ),
            elapsed=elapsed,
        )
        raise typer.Exit(code=130) from None
    return em.ok(data, elapsed=elapsed)


def _root(path: str | None) -> Path:
    return Path(path).expanduser() if path else Path.cwd()


def _interactive(yes: bool, json_mode: bool) -> bool:
    return not yes and not json_mode and sys.stdin.isatty() and sys.stdout.isatty()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Print the version and exit.")
    ] = False,
) -> None:
    """agenticcarekit — runs on your laptop, privacy boundary built in."""
    if version:
        typer.echo(f"agenticcarekit {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


# ── init ─────────────────────────────────────────────────────────────────


@app.command()
def init(
    path: Annotated[str | None, typer.Argument(help="Where to generate (default: .).")] = None,
    blueprint: Annotated[str | None, typer.Option("--blueprint", "-b")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    providers: Annotated[str | None, typer.Option("--providers")] = None,
    pack: Annotated[str | None, typer.Option("--pack")] = None,
    capabilities: Annotated[str | None, typer.Option("--capabilities")] = None,
    name: Annotated[str | None, typer.Option("--name", help="Project name.")] = None,
    blueprint_path: Annotated[str | None, typer.Option("--blueprint-path")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Ask nothing.")] = False,
    why: Annotated[bool, typer.Option("--why", help="Print the full ranked table.")] = False,
    no_pull: Annotated[bool, typer.Option("--no-pull", help="Do not download the model.")] = False,
    no_git: Annotated[bool, typer.Option("--no-git", help="Do not run git init.")] = False,
    offline: OfflineOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Probe the machine, recommend a model, and generate a project."""

    def body(em: Emitter) -> dict[str, Any]:
        dest = _root(path)
        project_name = name or (dest.resolve().name if str(dest) != "." else Path.cwd().name)

        with em.live_status("detecting this machine") as status:
            facts = machine_facts(offline=offline)
            status.update("detecting this machine — done")
        em.print(
            "  [dim]probed in "
            + f"{sum(p.duration_ms for p in facts.probes):.0f} ms across "
            + f"{len(facts.probes)} probes[/dim]"
        )

        spec, rec = plan(
            facts,
            blueprint=blueprint,
            blueprint_path=blueprint_path,
            model=model,
            providers=providers,
            pack=pack,
            capabilities=capabilities,
        )
        rerun = rerun_command(rec, capabilities_overridden=capabilities is not None)
        render_plan(em, rec, rerun)
        if why:
            render_why(em, rec)

        if _interactive(yes, json_out):
            rec, rerun = _ask(em, facts, rec, blueprint, blueprint_path, providers, pack,
                              capabilities)

        generated = generate_project(
            dest, spec, rec, project_name=project_name, git=not no_git
        )
        em.blank()
        em.rule("Generated")
        for rel in generated["files"]:
            em.print(f"    [green]+[/green] {rel}")
        if generated["git"]:
            em.print(f"    [dim]{generated['git']}[/dim]")

        pull_info: dict[str, Any] = {"status": "skipped", "tag": rec.model}
        pull_tag = rec.background_pull or (None if rec.model.endswith("cloud") else rec.model)
        if no_pull or offline:
            pull_info["message"] = "skipped (--no-pull/--offline)"
        elif pull_tag is None:
            pull_info["message"] = "hosted model — nothing to download"
        elif not facts.ollama_daemon:
            pull_info["message"] = (
                f"the ollama daemon is not running — start it and run: ollama pull {pull_tag}"
            )
        else:
            em.blank()
            result = do_pull(em, pull_tag)
            pull_info = result.to_dict()
            em.print(f"  {result.message or result.status}")

        if pull_info.get("message"):
            em.print(f"  [dim]{pull_info['message']}[/dim]")

        em.blank()
        em.print("  Re-run this exactly:")
        em.print(f"    [bold]{rerun}[/bold]")
        em.blank()
        em.print("  Next:")
        em.print("    [bold]ack check[/bold]        lint + selftest")
        em.print("    [bold]ack demo --offline[/bold]  run with networking disabled")

        return {
            "blueprint": spec.to_dict(),
            "plan": rec.model_dump(),
            "ranking": explain_ranking(rec),
            "generated": generated,
            "pull": pull_info,
            "rerun": rerun,
            "facts": facts.model_dump(),
        }

    _emit("init", json_out, body, elapsed=True)


def _ask(
    em: Emitter,
    facts: Any,
    rec: Any,
    blueprint: str | None,
    blueprint_path: str | None,
    providers: str | None,
    pack: str | None,
    capabilities: str | None,
) -> tuple[Any, str]:
    """At most two questions (brief §7.3). ``? why`` re-asks the first."""
    import questionary

    rerun = rerun_command(rec, capabilities_overridden=capabilities is not None)
    for _ in range(4):
        answer = questionary.select(
            "Accept this plan?",
            choices=["accept", "edit the model", "why these?"],
            default="accept",
        ).ask()
        if answer is None:
            raise AckError(
                "cancelled",
                code="E404",
                why="nothing was generated.",
                fix="ack init --yes   # accept the recommended plan without questions",
            )
        if answer.startswith("why"):
            render_why(em, rec)
            continue
        if answer.startswith("edit"):
            choices = [c.tag for c in rec.survivors[:8]] or [rec.model]
            picked = questionary.select("Model", choices=choices, default=rec.model).ask()
            if picked and picked != rec.model:
                _spec, rec = plan(
                    facts,
                    blueprint=blueprint,
                    blueprint_path=blueprint_path,
                    model=picked,
                    providers=providers,
                    pack=pack,
                    capabilities=capabilities,
                )
                rerun = rerun_command(rec, capabilities_overridden=capabilities is not None)
                render_plan(em, rec, rerun)
        break
    return rec, rerun


# ── doctor / explain ─────────────────────────────────────────────────────


@app.command()
def doctor(offline: OfflineOpt = False, json_out: JsonOpt = False) -> None:
    """Report this machine honestly, with problems as fixable error codes."""

    def body(em: Emitter) -> dict[str, Any]:
        facts = machine_facts(offline=offline)
        return doctor_report(em, facts)

    _emit("doctor", json_out, body)


@app.command()
def explain(
    code: Annotated[str | None, typer.Argument(help="An error code, e.g. E203.")] = None,
    json_out: JsonOpt = False,
) -> None:
    """Print the long form of an error code."""

    def body(em: Emitter) -> dict[str, Any]:
        registry = error_registry()
        if code is None:
            em.blank()
            em.rule("Error code ranges")
            for rng, domain in _RANGES.items():
                em.field(rng, domain)
            em.blank()
            em.rule("Registered codes")
            em.table(
                ["code", "title"],
                [[e.code, e.title] for e in sorted(registry.values(), key=lambda e: e.code)],
            )
            return {
                "ranges": _RANGES,
                "codes": [
                    {"code": e.code, "title": e.title}
                    for e in sorted(registry.values(), key=lambda e: e.code)
                ],
            }
        entry = explain_code(code)
        if entry is None:
            raise AckError(
                f"'{code}' is not a registered error code",
                code="E401",
                why="ranges: " + "; ".join(f"{k} {v}" for k, v in _RANGES.items()),
                fix="ack explain   # lists every registered code",
                details={"ranges": _RANGES, "known": sorted(registry)},
            )
        em.blank()
        em.print(f"  [bold]{entry.code}[/bold]  {entry.title}")
        em.blank()
        em.field("what", entry.what)
        em.field("why", entry.why)
        em.blank()
        em.print("  Fix:")
        em.print(f"    [bold]{entry.fix}[/bold]")
        return {
            "code": entry.code,
            "title": entry.title,
            "what": entry.what,
            "why": entry.why,
            "fix": entry.fix,
        }

    _emit("explain", json_out, body)


_RANGES = {
    "E0xx": "bootstrap / environment",
    "E1xx": "model / provider / network",
    "E2xx": "capability mismatch",
    "E3xx": "policy and privacy violations",
    "E4xx": "project config",
    "E5xx": "generation / templates",
    "E6xx": "eval",
}


# ── new ──────────────────────────────────────────────────────────────────


@app.command()
def new(
    kind: Annotated[str | None, typer.Argument(help=f"One of: {', '.join(KINDS)}.")] = None,
    name: Annotated[str | None, typer.Argument(help="Name for the new thing.")] = None,
    path: PathOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Scaffold a provider, redactor, pack, capability or blueprint."""

    def body(em: Emitter) -> dict[str, Any]:
        if kind is None:
            em.blank()
            em.rule("Extension points")
            em.table(
                ["kind", "interface", "command"],
                [
                    ["provider", "Provider protocol", "ack new provider my-provider"],
                    ["redactor", "Redactor protocol", "ack new redactor my-redactor"],
                    ["pack", "pack manifest + schemas", "ack new pack my-pack"],
                    ["capability", "capability manifest", "ack new capability my-capability"],
                    ["blueprint", "templates + blueprint.toml", "ack new blueprint my-blueprint"],
                ],
            )
            return {"kinds": list(KINDS)}
        result = scaffold(kind, name or f"my-{kind}", _root(path))
        em.blank()
        em.rule(f"New {result['kind']}: {result['name']}")
        for rel in result["files"]:
            em.print(f"    [green]+[/green] {rel}")
        em.blank()
        em.print(f"  Next: [bold]{result['next']}[/bold]")
        return result

    _emit("new", json_out, body)


# ── project operations ───────────────────────────────────────────────────


@app.command()
def manifest(path: PathOpt = None, json_out: JsonOpt = False) -> None:
    """Machine-readable description of a generated project."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        cfg = require_project(root)
        data = build_manifest(root, cfg)
        em.blank()
        em.rule(f"{data['project']['name']}")
        em.field("blueprint", cfg.blueprint)
        em.field("pack", cfg.pack or "(none)")
        em.field("model", str(cfg.model_primary))
        em.field("egress", cfg.egress.value)
        em.field("capabilities", ", ".join(cfg.capabilities) or "(none)")
        em.field("tools", str(len(data["tools"])))
        em.field("files", str(len(data["files"])))
        for note in data["tool_notes"]:
            em.print(f"    [yellow]![/yellow] {note}")
        em.blank()
        em.print("  [dim]--json is the primary output of this command[/dim]")
        return data

    _emit("manifest", json_out, body)


@app.command()
def sync(
    path: PathOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite drifted files.")] = False,
    json_out: JsonOpt = False,
) -> None:
    """Reconcile the project tree against ``ack.toml``."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        cfg = require_project(root)
        result = sync_project(root, cfg, force=force)
        em.blank()
        em.rule("Sync")
        em.field("blueprint", cfg.blueprint + ("" if result["blueprint_found"] else "  (not installed)"))
        em.field("created", str(len(result["created"])))
        em.field("unchanged", str(len(result["unchanged"])))
        em.field("drifted", str(len(result["drifted"])))
        for rel in result["created"]:
            em.print(f"    [green]+[/green] {rel}")
        for rel in result["drifted"]:
            em.print(f"    [yellow]~[/yellow] {rel}  [dim](your edit kept; --force to replace)[/dim]")
        return result

    _emit("sync", json_out, body)


@app.command()
def add(
    capability: Annotated[str | None, typer.Argument(help="Capability to enable.")] = None,
    path: PathOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Enable a capability in ``ack.toml``."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        if capability is None:
            em.blank()
            em.rule("Capabilities")
            for cap in KNOWN_CAPABILITIES:
                em.field(cap, f"ack add {cap}")
            return {"known": list(KNOWN_CAPABILITIES)}
        cfg = require_project(root)
        result = add_capability(root, cfg, capability)
        em.blank()
        if result["changed"]:
            em.print(f"  [green]+[/green] {capability} enabled")
        else:
            em.print(f"  [dim]{capability} was already enabled[/dim]")
        em.field("capabilities", ", ".join(result["capabilities"]))
        return result

    _emit("add", json_out, body)


@app.command("swap")
def swap_cmd(
    what: Annotated[str | None, typer.Argument(help=f"One of: {', '.join(SWAPPABLE)}.")] = None,
    value: Annotated[str | None, typer.Argument(help="The new value.")] = None,
    path: PathOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Swap the model, fallback, pack, redactor or egress class."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        if what is None or value is None:
            em.blank()
            em.rule("Swappable")
            for slot in SWAPPABLE:
                em.field(slot, f"ack swap {slot} <value>")
            return {"swappable": list(SWAPPABLE)}
        cfg = require_project(root)
        result = swap(root, cfg, what, value)
        em.blank()
        em.print(f"  [green]{result['what']}[/green]  {result['from']} → {result['to']}")
        return result

    _emit("swap", json_out, body)


@app.command("eject")
def eject_cmd(
    thing: Annotated[str | None, typer.Argument(help="What to inline, e.g. prompts.")] = None,
    path: PathOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
    json_out: JsonOpt = False,
) -> None:
    """Inline a packaged abstraction into your source. Reversible by design."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        if thing is None:
            em.blank()
            em.rule("Ejectable")
            for key in sorted(EJECTABLES):
                em.field(key, EJECTABLES[key].description)
            return {"available": sorted(EJECTABLES)}
        result = eject(root, thing, force=force)
        em.blank()
        em.rule(f"Ejected {result['ejected']}")
        for rel in result["copied"]:
            em.print(f"    [green]+[/green] {rel}")
        for rel in result["skipped"]:
            em.print(f"    [dim]= {rel}  (exists; --force to overwrite)[/dim]")
        if not result["copied"] and not result["skipped"]:
            em.print("    [dim]nothing packaged to eject for this target[/dim]")
        return result

    _emit("eject", json_out, body)


# ── verification loop ────────────────────────────────────────────────────


@app.command()
def check(path: PathOpt = None, json_out: JsonOpt = False) -> None:
    """Lint plus a fast selftest. Honest pass/fail, under 30 seconds."""

    def body(em: Emitter) -> dict[str, Any]:
        result = run_check(_root(path))
        em.blank()
        em.rule("Check")
        for step in result["steps"]:
            mark = {"pass": "[green]✓[/green]", "fail": "[red]✗[/red]"}.get(
                str(step["status"]), "[yellow]−[/yellow]"
            )
            em.print(f"    {mark} {step['name']:<10}{step['status']}  [dim]{step['duration_ms']:.0f} ms[/dim]")
            if step["status"] != "pass" and step.get("detail"):
                for line in str(step["detail"]).splitlines()[:8]:
                    em.print(f"        [dim]{line}[/dim]")
        em.blank()
        em.print(
            f"  {'[green]ok[/green]' if result['ok'] else '[red]failed[/red]'}  "
            f"[dim]{result['duration_ms'] / 1000:.1f}s of a {result['budget_seconds']}s budget[/dim]"
        )
        return result

    env = _emit("check", json_out, body)
    if not (env["data"] or {}).get("ok", True):
        raise typer.Exit(code=1)


@app.command("eval")
def eval_cmd(
    path: PathOpt = None,
    offline: OfflineOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Score the project against its golden set."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        cfg = require_project(root)
        result = run_eval_command(root, cfg, offline=offline)
        em.blank()
        em.rule("Eval")
        em.field("golden set", result["golden_set"])
        em.field("cases", str(result["cases"]))
        em.field("exact match", f"{result['exact_match_rate'] * 100:.1f}%")
        if result["judge_score_avg"] is not None:
            em.field("judge score", f"{result['judge_score_avg']:.2f}")
        return result

    _emit("eval", json_out, body, elapsed=True)


@app.command()
def demo(
    path: PathOpt = None,
    offline: OfflineOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Run the project's demo. ``--offline`` runs it against mocks."""

    def body(em: Emitter) -> dict[str, Any]:
        root = _root(path)
        cfg = require_project(root)
        result = run_demo(root, cfg, offline=offline)
        em.blank()
        em.rule("Demo")
        em.field("entry", result["entry"])
        em.field("offline", "yes" if result["offline"] else "no")
        em.field("exit code", str(result["exit_code"]))
        em.blank()
        for line in str(result["output"]).splitlines():
            em.print(f"    {line}")
        if not result["succeeded"]:
            em.blank()
            em.print(f"  [red]the demo exited {result['exit_code']}[/red]")
        return result

    env = _emit("demo", json_out, body, elapsed=True)
    # The command did its job; the project's demo did not. Say so with the
    # process status — there is no registered error code for "the generated
    # demo failed", and inventing one would be a test failure (W-J).
    if not (env["data"] or {}).get("succeeded", True):
        raise typer.Exit(code=1)


# ── sidecar ──────────────────────────────────────────────────────────────


@app.command()
def serve(
    path: PathOpt = None,
    host: Annotated[str, typer.Option("--host", help="Bind address (loopback only).")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 4422,
    mcp: Annotated[bool, typer.Option("--mcp", help="Serve MCP over stdio instead.")] = False,
    allow_remote: Annotated[
        bool, typer.Option("--allow-remote", help="Permit a non-loopback bind (deliberate).")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be served, then exit.")
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """Run the local sidecar: the kernel over HTTP, or MCP for agents.

    The policy boundary, redaction and the trace live in this one process, so a
    thin client in any language gets them for free and cannot route around
    them. ``--mcp`` speaks MCP over stdio; stdout is then the transport, so the
    banner goes to stderr. Under ``--json`` the envelope is printed when the
    server stops — the bind details are on stderr at startup. ``--dry-run``
    reports what would be served and exits, binding nothing and writing nothing.
    """

    if dry_run:

        def plan(em: Emitter) -> dict[str, Any]:
            info = _serve_runner().startup_plan(
                _root(path), host=host, port=port, allow_remote=allow_remote, mcp=mcp
            )
            em.blank()
            em.rule("Would serve")
            for key in ("mode", "url", "docs_url", "token_path", "root"):
                if key in info:
                    em.field(key.replace("_", " "), str(info[key]))
            return info

        _emit("serve", json_out, plan)
        return

    if mcp:
        # stdout IS the MCP transport: no header, no envelope, nothing but the
        # protocol. Failures go to stderr in the canonical shape.
        try:
            _serve_runner().run_mcp(_root(path))
        except AckError as err:
            typer.echo(err.render(), err=True)
            raise typer.Exit(code=1) from None
        return

    def body(em: Emitter) -> dict[str, Any]:
        return _serve_runner().run_http(
            _root(path), host=host, port=port, allow_remote=allow_remote
        )

    _emit("serve", json_out, body)


def _serve_runner() -> Any:
    """Import the sidecar lazily; explain the missing extra if it is absent."""
    try:
        from agenticcarekit.serve import runner
    except ImportError as exc:
        raise AckError(
            "the sidecar needs the optional [serve] extra, which is not installed",
            code="E000",
            why=f"importing agenticcarekit.serve failed: {type(exc).__name__}: {exc}",
            fix='uv pip install "agenticcarekit[serve]"',
            details={"missing": ["fastapi", "uvicorn", "sse-starlette", "mcp"]},
        ) from None
    return runner


# ── entry point ──────────────────────────────────────────────────────────


def main() -> None:
    """Console-script entry point for ``ack`` and ``agenticcarekit``."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
