"""The ``init`` flow and the ``doctor`` report (brief §7.3).

``init`` is the command the whole project is judged on, so its shape is
fixed here rather than assembled ad hoc:

    probe → recommend → plan screen → accept/edit → generate → pull

and it **always** prints the non-interactive equivalent of what it just
did. That line is what goes in a README, a CI job, and a message to a
teammate — printing it is what makes teams adopt the tool.

At most two questions are asked (brief §7.3: "every question is a
detection you didn't do"), and ``--yes`` asks none.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import AckConfig, AckError, EgressClass, ModelRef

from .blueprints import BlueprintSpec
from .blueprints import resolve as resolve_blueprint
from .detect import MachineFacts, ProbeEnv, run_probes
from .detect.probes import facts_from_file
from .output import Emitter
from .pull import HttpOllamaAPI, OllamaAPI, PullResult, pull_model
from .recommend import Recommendation, explain_ranking, recommend
from .render import build_vars, render_tree
from .scaffold import agent_instructions, try_git_init, write_ack_toml, write_agent_surface

__all__ = [
    "FACTS_ENV",
    "doctor_report",
    "generate_project",
    "machine_facts",
    "plan",
    "render_plan",
    "rerun_command",
]

#: Point this at a recorded ``MachineFacts`` JSON file to skip probing.
#: The fixture corpus in ``tests/fixtures_cli/machines/`` is exactly this
#: shape, so a test drives the real code path with a synthetic machine.
FACTS_ENV = "ACK_MACHINE_FACTS"


def machine_facts(*, offline: bool = False, env: ProbeEnv | None = None) -> MachineFacts:
    """Probe this machine, or load an injected profile.

    Example:
        >>> import os, tempfile, json
        >>> f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        >>> _ = f.write(json.dumps({"os": "Linux", "ram_total_gb": 8.0})); f.close()
        >>> os.environ["ACK_MACHINE_FACTS"] = f.name
        >>> machine_facts().os
        'Linux'
        >>> del os.environ["ACK_MACHINE_FACTS"]
    """
    override = os.environ.get(FACTS_ENV)
    if override:
        return facts_from_file(override)
    if env is None:
        environ = dict(os.environ)
        env = ProbeEnv(environ=environ, offline=offline or environ.get("ACK_OFFLINE") == "1")
    return run_probes(env)


# ── planning ─────────────────────────────────────────────────────────────


def plan(
    facts: MachineFacts,
    *,
    blueprint: str | None,
    blueprint_path: str | None = None,
    model: str | None = None,
    providers: str | None = None,
    pack: str | None = None,
    capabilities: str | None = None,
) -> tuple[BlueprintSpec, Recommendation]:
    """Resolve the blueprint and rank models against this machine."""
    spec = resolve_blueprint(blueprint, blueprint_path)
    caps = (
        [c.strip() for c in capabilities.split(",") if c.strip()]
        if capabilities
        else list(spec.default_capabilities)
    )
    chosen_pack = pack if pack is not None else spec.default_pack
    provider_list = [p.strip() for p in (providers or "").split(",") if p.strip()]
    allow_hosted = any(p != "ollama" for p in provider_list)
    default_redactor = "healthcare.phi" if chosen_pack == "healthcare" else None
    rec = recommend(
        facts,
        spec.requires,
        pack=chosen_pack,
        capabilities=caps,
        force_model=model,
        allow_hosted_fallback=allow_hosted,
        default_redactor=default_redactor,
    )
    if provider_list:
        rec.providers = provider_list
    return spec, rec


def rerun_command(rec: Recommendation, *, capabilities_overridden: bool = False) -> str:
    """The exact non-interactive command that reproduces this plan.

    Example:
        >>> from .recommend import Recommendation
        >>> rec = Recommendation(blueprint="voice-care", model="gemma4:e4b-mlx",
        ...     model_ref="ollama:gemma4:e4b-mlx", providers=["ollama", "cerebras"],
        ...     pack="healthcare")
        >>> rerun_command(rec)
        'ack init --blueprint voice-care --model gemma4:e4b-mlx --providers ollama,cerebras --pack healthcare --yes'
    """
    parts = [
        "ack init",
        f"--blueprint {rec.blueprint}",
        f"--model {rec.model}",
        f"--providers {','.join(rec.providers)}",
        f"--pack {rec.pack}",
    ]
    if capabilities_overridden:
        parts.append(f"--capabilities {','.join(rec.capabilities)}")
    parts.append("--yes")
    return " ".join(parts)


def render_plan(em: Emitter, rec: Recommendation, rerun: str) -> None:
    """The plan screen (brief §7.3 shape), degrading below 80 columns."""
    em.blank()
    em.rule("Plan")
    em.field("blueprint", rec.blueprint)
    top = rec.top_reasons(2)
    em.field("model", rec.model, top[0] if top else None)
    if len(top) > 1:
        em.note_continuation(top[1])
    em.field("providers", " → ".join(rec.providers), rec.provider_reason)
    em.field("pack", rec.pack or "(none)")
    if rec.capabilities:
        em.field("capabilities", ", ".join(rec.capabilities))
    em.field("egress", rec.egress, f"redactor {rec.redactor}" if rec.redactor else None)
    if rec.background_pull:
        em.field("background pull", rec.background_pull)
    for warning in rec.warnings:
        em.print(f"    [yellow]⚠[/yellow]  {warning}")
    em.blank()
    em.print("  [dim]↵ accept   e edit   ? why these[/dim]")
    em.blank()
    em.print("  Re-run this exactly:")
    if em.narrow:
        import textwrap

        for line in textwrap.wrap(
            rerun, width=max(20, em.width - 6), subsequent_indent="  "
        ):
            em.print(f"    [bold]{line}[/bold]")
    else:
        head, _, tail = rerun.partition(" --providers ")
        # The backslash sits outside the markup: rich reads "\[" as an
        # escaped bracket, which would eat the closing tag.
        em.print(f"    [bold]{head}[/bold] \\")
        em.print(f"      [bold]--providers {tail}[/bold]")


def render_why(em: Emitter, rec: Recommendation) -> None:
    """``? why`` — the full ranked table, every elimination included."""
    data = explain_ranking(rec)
    em.blank()
    em.rule("Why these")
    em.table(
        ["#", "model", "score", "reasons"],
        [
            [
                str(row["rank"]),
                str(row["tag"]),
                f"{row['score']:.1f}",
                "; ".join(row["reasons"]) or "—",
            ]
            for row in data["ranked"]
        ],
    )
    if data["eliminated"]:
        em.blank()
        em.rule("Eliminated")
        em.table(
            ["model", "filter", "why"],
            [
                [str(row["tag"]), str(row["eliminated_by"]), "; ".join(row["reasons"])]
                for row in data["eliminated"]
            ],
        )


# ── generation ───────────────────────────────────────────────────────────


def build_config(rec: Recommendation, existing: AckConfig | None = None) -> AckConfig:
    """Assemble the ``ack.toml`` config, preserving a user's unknown keys."""
    raw = dict(existing.raw) if existing and existing.raw else {}
    return AckConfig(
        blueprint=rec.blueprint,
        pack=rec.pack,
        model_primary=ModelRef.parse(rec.model_ref),
        model_fallback=ModelRef.parse(rec.fallback_ref) if rec.fallback_ref else None,
        egress=EgressClass(rec.egress),
        redactor=rec.redactor,
        capabilities=tuple(rec.capabilities),
        raw=raw,
    )


def generate_project(
    dest: Path,
    spec: BlueprintSpec,
    rec: Recommendation,
    *,
    project_name: str,
    git: bool = True,
) -> dict[str, Any]:
    """Render the blueprint, write ``ack.toml`` and the agent surface.

    Deterministic by construction: sorted iteration, no timestamps, no
    absolute paths (invariant 4). Written *before* any model pull, so a
    Ctrl-C during the download leaves a valid project behind.
    """
    dest.mkdir(parents=True, exist_ok=True)
    variables = build_vars(
        project_name=project_name,
        blueprint=rec.blueprint,
        pack=rec.pack,
        model_primary=rec.model_ref,
        model_fallback=rec.fallback_ref,
        egress=rec.egress,
        redactor=rec.redactor,
        capabilities=rec.capabilities,
    )
    written: list[str] = []
    templates = spec.templates
    if templates is not None:
        written.extend(render_tree(templates, dest, variables))

    # Re-running init in an existing project must not eat a user's or an
    # agent's extra ack.toml tables (Contract 5: unknown keys are preserved).
    existing: AckConfig | None = None
    if (dest / "ack.toml").is_file():
        try:
            existing = AckConfig.load(dest / "ack.toml")
        except AckError:
            existing = None
    cfg = build_config(rec, existing)
    written.append(write_ack_toml(dest, cfg))
    written.extend(
        write_agent_surface(
            dest,
            agent_instructions(
                project_name=project_name,
                blueprint=rec.blueprint,
                pack=rec.pack,
                model_primary=rec.model_ref,
                model_fallback=rec.fallback_ref,
                egress=rec.egress,
                redactor=rec.redactor,
                capabilities=rec.capabilities,
            ),
        )
    )
    git_status = try_git_init(dest) if git else None
    return {
        "path": str(dest),
        "project_name": project_name,
        "files": sorted(set(written)),
        "git": git_status,
        "templates_rendered": templates is not None,
    }


def do_pull(
    em: Emitter,
    tag: str,
    api: OllamaAPI | None = None,
) -> PullResult:
    """Pull the chosen model, reporting only what the daemon reports."""
    client: OllamaAPI = api or HttpOllamaAPI()
    with em.live_status(f"pulling {tag}") as status:

        def on_progress(line: str, _result: PullResult) -> None:
            status.update(f"pulling {tag} — {line}")

        return pull_model(tag, client, on_progress=on_progress)


# ── doctor ───────────────────────────────────────────────────────────────


def detected_problems(facts: MachineFacts) -> list[dict[str, str]]:
    """Environment problems, as registered error codes with their fixes.

    Only codes that exist in ``spec/errors.json`` are ever emitted — a code
    raised but not registered is a test failure (docs/CONTRACTS.md).

    Example:
        >>> [p["code"] for p in detected_problems(MachineFacts())]
        ['E010', 'E011']
    """
    from agenticcarekit.kernel.contracts import explain

    problems: list[str] = []
    if not facts.ollama_installed:
        problems.append("E010")
    if facts.ollama_installed and not facts.ollama_daemon:
        problems.append("E011")
    if not facts.ollama_installed and not facts.ollama_daemon:
        problems.append("E011")
    if facts.disk_free_gb is not None and facts.disk_free_gb < 12.0:
        problems.append("E020")
    if facts.python_version != "unknown":
        try:
            major, minor = (int(p) for p in facts.python_version.split(".")[:2])
            if (major, minor) < (3, 11):
                problems.append("E001")
        except ValueError:
            pass
    if facts.network_mbps is None and facts.probe("network") and (
        facts.probe("network").status == "error"  # type: ignore[union-attr]
    ):
        problems.append("E110")

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in problems:
        if code in seen:
            continue
        seen.add(code)
        entry = explain(code)
        if entry is None:  # pragma: no cover - registry drift is a W-J failure
            continue
        out.append({"code": entry.code, "title": entry.title, "what": entry.what, "fix": entry.fix})
    return out


def doctor_report(em: Emitter, facts: MachineFacts) -> dict[str, Any]:
    """Print the honest environment table and return the ``--json`` payload."""
    problems = detected_problems(facts)
    rows = [
        ("os", f"{facts.os} {facts.os_release} ({facts.arch})"),
        ("cpu", f"{facts.cpu_model}" + (f" × {facts.cpu_cores}" if facts.cpu_cores else "")),
        ("ram", _gb(facts.ram_total_gb, f"{_g(facts.ram_available_gb)} GB available")),
        ("gpu", facts.gpu_name or "none detected"),
        ("vram", _gb(facts.vram_gb)),
        ("disk free", _gb(facts.disk_free_gb, facts.model_dir)),
        (
            "ollama",
            "not installed"
            if not facts.ollama_installed
            else f"{facts.ollama_version or 'installed'}"
            + (" (daemon up)" if facts.ollama_daemon else " (daemon down)"),
        ),
        ("models pulled", ", ".join(facts.installed_tags) or "none"),
        ("docker", "yes" if facts.docker_installed else "no"),
        ("python", facts.python_version),
        ("node", facts.node_version or "not installed"),
        (
            "network",
            f"{facts.network_mbps} Mbps" if facts.network_mbps is not None else "unknown",
        ),
        (
            "provider keys",
            ", ".join(sorted(k for k, v in facts.provider_keys.items() if v)) or "none set",
        ),
        ("ack.toml", facts.ack_toml_path or "not in this directory"),
    ]
    em.blank()
    em.rule("Environment")
    for label, value in rows:
        em.field(label, value)

    unknown = facts.unknowns()
    if unknown:
        em.blank()
        em.print(f"  [dim]unknown: {', '.join(unknown)} (probe did not complete)[/dim]")

    em.blank()
    if problems:
        em.rule("Problems")
        for p in problems:
            em.print(f"    [yellow]{p['code']}[/yellow]  {p['title']}")
            em.print(f"            [dim]{p['what']}[/dim]")
            em.print(f"            [bold]{p['fix']}[/bold]")
    else:
        em.print("  [green]no problems detected[/green]")

    em.blank()
    em.print(
        "  [dim]probe timings: "
        + ", ".join(f"{p.name} {p.duration_ms:.0f}ms" for p in facts.probes)
        + "[/dim]"
    )
    return {"facts": facts.model_dump(), "problems": problems}


def _g(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _gb(value: float | None, note: str | None = None) -> str:
    if value is None:
        return "unknown"
    text = f"{_g(value)} GB"
    return f"{text}  ({note})" if note else text


def require_project(path: Path) -> AckConfig:
    """Load ``ack.toml`` from ``path`` or raise E404."""
    cfg_path = path / "ack.toml"
    if not cfg_path.is_file():
        raise AckError(
            f"no ack.toml in {path}",
            code="E404",
            why="this command needs a generated project and none was found here.",
            fix="ack init",
        )
    return AckConfig.load(cfg_path)
