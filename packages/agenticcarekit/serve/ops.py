"""One implementation of every sidecar operation. Two transports call it.

HTTP (:mod:`agenticcarekit.serve.app`) and MCP
(:mod:`agenticcarekit.serve.mcp_server`) are *transports*. Everything they do
happens here, so a fix lands once and both surfaces get it — the same rule
that makes Tier 2 worth building at all (brief §3: ports are convenience, not
correctness surface).

Every function here:

* returns a plain JSON-able ``dict`` — the ``data`` half of the CLI envelope,
  so ``ack doctor --json | jq .data`` and ``GET /v1/doctor | jq .data`` agree;
* raises :class:`AckError` (with a registered code, a why, and a literal fix)
  and never a bare exception, so both transports render failures identically;
* reuses the landed CLI/kernel code rather than re-deriving it. ``doctor``
  really is ``ack doctor``.

:func:`generate` is the chokepoint. A thin client hands over a model ref, some
messages, and a list of which fields are sensitive; the sidecar builds the
provider, wraps declared fields in ``Sensitive`` and routes them through
``Policy.unwrap``. The client never holds a provider, so it cannot take a
second path to one.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agenticcarekit.cli import checks as _checks
from agenticcarekit.cli import flows as _flows
from agenticcarekit.cli import project_ops as _project_ops
from agenticcarekit.cli.output import Emitter
from agenticcarekit.cli.recommend import CATALOG
from agenticcarekit.kernel.contracts import (
    AckConfig,
    AckError,
    EgressClass,
    GenerateRequest,
    Message,
    Provider,
    Redactor,
    Sensitive,
    error_registry,
)
from agenticcarekit.kernel.contracts import explain as _explain

from .trace import TraceHub

__all__ = [
    "PROVIDERS",
    "add_capability",
    "check",
    "discover_redactors",
    "doctor",
    "explain_error",
    "generate",
    "get_manifest",
    "health",
    "init_project",
    "provider_from_ref",
    "resolve_path",
    "run_eval",
    "search_models",
]


# ── paths ────────────────────────────────────────────────────────────────


def resolve_path(root: Path, path: str | None, *, required: bool = False) -> Path:
    """Resolve a client-supplied path against the sidecar's root.

    Relative paths are relative to ``root``, **never** to the server process's
    working directory — a sidecar that scaffolded into whatever directory it
    happened to be started from would be a footgun with a long fuse.

    Example:
        >>> resolve_path(Path("/srv/work"), "demo").as_posix()
        '/srv/work/demo'
        >>> resolve_path(Path("/srv/work"), "/tmp/elsewhere").as_posix()
        '/tmp/elsewhere'
        >>> resolve_path(Path("/srv/work"), None).as_posix()
        '/srv/work'
    """
    if path is None or not str(path).strip():
        if required:
            raise AckError(
                "no path given",
                code="E401",
                why="this operation writes files, so it never guesses a destination.",
                fix='pass an explicit path, e.g. {"path": "./my-project"}',
            )
        return root
    candidate = Path(str(path)).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def _config(root: Path) -> AckConfig:
    """Load ``ack.toml`` from ``root`` or raise E404 (same as the CLI)."""
    return _flows.require_project(root)


def _quiet() -> Emitter:
    """An emitter in ``--json`` mode: it renders nothing, it only shapes data."""
    return Emitter("serve", True)


# ── read-only operations ─────────────────────────────────────────────────


def health(root: Path, *, auth_required: bool = True) -> dict[str, Any]:
    """Liveness plus what a client needs to talk to this sidecar.

    The one endpoint that needs no token: a client must be able to discover
    that the sidecar is up, and where the token file is, before it has a token.
    The token *value* is never in this payload.

    Example:
        >>> health(Path("/srv/work"))["telemetry"]
        False
    """
    from agenticcarekit import __version__

    return {
        "status": "ok",
        "version": __version__,
        "root": str(root),
        "auth_required": auth_required,
        "token_path": str(root / ".ack" / "serve.token"),
        "telemetry": False,
    }


def doctor(*, offline: bool = False) -> dict[str, Any]:
    """``ack doctor`` — the honest environment report, machine-readable.

    Agents stop hallucinating fixes for problems that do not exist when they
    can read the machine instead (brief §9). ``problems`` is a list of
    registered error codes, each with the literal command that fixes it.

    Set ``ACK_MACHINE_FACTS`` to a recorded profile to skip probing entirely —
    that is how this is tested offline and deterministically.
    """
    facts = _flows.machine_facts(offline=offline)
    return _flows.doctor_report(_quiet(), facts)


def get_manifest(root: Path, path: str | None = None) -> dict[str, Any]:
    """``ack manifest`` — the machine-readable description of a project."""
    target = resolve_path(root, path)
    return _project_ops.build_manifest(target, _config(target))


def explain_error(code: str | None = None) -> dict[str, Any]:
    """``ack explain`` — the long form of a registered error code.

    With no code, lists every registered code so an agent can enumerate the
    failure surface without scraping documentation.

    Example:
        >>> explain_error("E203")["title"]
        'Model does not support a required input modality'
        >>> len(explain_error()["codes"]) > 10
        True
    """
    registry = error_registry()
    if code is None or not code.strip():
        return {
            "codes": [
                {"code": e.code, "title": e.title}
                for e in sorted(registry.values(), key=lambda e: e.code)
            ]
        }
    entry = _explain(code)
    if entry is None:
        raise AckError(
            f"'{code}' is not a registered error code",
            code="E401",
            why="error codes are registered in spec/errors.json before they are raised.",
            fix="call explain_error with no code to list every registered code",
            details={"known": sorted(registry)},
        )
    return {
        "code": entry.code,
        "title": entry.title,
        "what": entry.what,
        "why": entry.why,
        "fix": entry.fix,
    }


def search_models(
    *,
    modality: str | None = None,
    min_context_tokens: int | None = None,
    max_size_gb: float | None = None,
    include_hosted: bool = True,
    already_pulled_only: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    """The model catalog, filtered, with what is already on this machine.

    The catalog is ``docs/brief.md`` §2 verbatim (never invented); the
    ``already_pulled`` flag comes from the same probe ``ack doctor`` uses, so
    an agent can tell "this model would work" from "this model is here now"
    without a download.

    Example:
        >>> tags = [m["tag"] for m in search_models(modality="audio", offline=True)["models"]]
        >>> tags
        ['gemma4:e2b', 'gemma4:e2b-mlx', 'gemma4:e4b', 'gemma4:e4b-mlx']
        >>> search_models(modality="audio", offline=True)["models"][0]["context_tokens"]
        131072
    """
    if modality is not None and modality not in ("text", "image", "audio"):
        raise AckError(
            f"unknown modality '{modality}'",
            code="E401",
            why="modalities are the closed set from Contract 1: text, image, audio.",
            fix='search_models({"modality": "audio"})',
            details={"known": ["text", "image", "audio"]},
        )
    facts = _flows.machine_facts(offline=offline)
    installed = set(facts.installed_tags)

    models: list[dict[str, Any]] = []
    for tag in sorted(CATALOG):
        entry = CATALOG[tag]
        if modality is not None and modality not in entry.modalities_in:
            continue
        if min_context_tokens is not None and entry.context_tokens < min_context_tokens:
            continue
        if max_size_gb is not None and (entry.size_gb is not None and entry.size_gb > max_size_gb):
            continue
        if not include_hosted and entry.hosted:
            continue
        pulled = tag in installed
        if already_pulled_only and not pulled:
            continue
        models.append(
            {
                "tag": tag,
                "provider": entry.provider,
                "ref": entry.ref,
                "size_gb": entry.size_gb,
                "context_tokens": entry.context_tokens,
                "modalities_in": sorted(entry.modalities_in),
                "tool_calling": entry.tool_calling,
                "hosted": entry.hosted,
                "verified": entry.verified,
                "already_pulled": pulled,
                "why": entry.blurb,
            }
        )
    return {
        "models": models,
        "count": len(models),
        "installed_tags": sorted(installed),
        "filters": {
            "modality": modality,
            "min_context_tokens": min_context_tokens,
            "max_size_gb": max_size_gb,
            "include_hosted": include_hosted,
            "already_pulled_only": already_pulled_only,
        },
        "note": (
            "Only Gemma 4 via Ollama is a verified path; entries with "
            "verified=false are declared, untested (brief §2)."
        ),
    }


# ── write operations ─────────────────────────────────────────────────────


def init_project(
    root: Path,
    path: str,
    *,
    blueprint: str | None = None,
    model: str | None = None,
    providers: str | None = None,
    pack: str | None = None,
    capabilities: str | None = None,
    name: str | None = None,
    git: bool = False,
    offline: bool = True,
) -> dict[str, Any]:
    """``ack init --yes`` without the terminal: probe, plan, generate.

    ``path`` is **required** — the sidecar never scaffolds into an implicit
    directory. Generation is deterministic (invariant 4): identical inputs
    produce a byte-identical tree.

    Nothing is downloaded. Pulling a model is a long, interruptible, network
    operation that belongs to a foreground command with a progress region;
    the returned ``pull`` block names the exact command instead of pretending.
    """
    dest = resolve_path(root, path, required=True)
    facts = _flows.machine_facts(offline=offline)
    spec, rec = _flows.plan(
        facts,
        blueprint=blueprint,
        model=model,
        providers=providers,
        pack=pack,
        capabilities=capabilities,
    )
    project_name = name or dest.resolve().name
    generated = _flows.generate_project(dest, spec, rec, project_name=project_name, git=git)
    rerun = _flows.rerun_command(rec, capabilities_overridden=capabilities is not None)
    tag = rec.background_pull or (None if rec.model.endswith("cloud") else rec.model)
    return {
        "blueprint": spec.to_dict(),
        "plan": rec.model_dump(),
        "generated": generated,
        "rerun": rerun,
        "pull": {
            "status": "skipped",
            "tag": tag,
            "message": (
                "the sidecar never downloads models"
                + (f"; run: ollama pull {tag}" if tag else "; this model is hosted")
            ),
        },
    }


def add_capability(root: Path, capability: str, path: str | None = None) -> dict[str, Any]:
    """``ack add <capability>`` — enable a capability in ``ack.toml``.

    Idempotent, and it preserves any keys a human or another agent added to
    the file (Contract 5).
    """
    target = resolve_path(root, path)
    return _project_ops.add_capability(target, _config(target), capability)


def run_eval(root: Path, path: str | None = None, *, offline: bool = True) -> dict[str, Any]:
    """``ack eval`` — score a project against its committed golden set.

    Raises **E601** when a piece is genuinely missing (no golden set, no
    provider chain). An honest failure beats a fabricated score.
    """
    target = resolve_path(root, path)
    return _checks.run_eval_command(target, _config(target), offline=offline)


def check(root: Path, path: str | None = None) -> dict[str, Any]:
    """``ack check`` — lint plus a fast selftest. The loop agents close against."""
    return _checks.run_check(resolve_path(root, path))


# ── the kernel chokepoint ────────────────────────────────────────────────

#: Model-ref prefix → provider constructor. A thin client names a provider it
#: cannot instantiate; the sidecar owns every construction, which is what puts
#: the policy engine unavoidably in the path.
PROVIDERS: dict[str, Callable[[str], Any]] = {}


def _provider_table() -> dict[str, Callable[[str], Any]]:
    """Build the provider table lazily (imports ``httpx``, opens no socket)."""
    if PROVIDERS:
        return PROVIDERS
    from agenticcarekit.kernel.providers import (
        CerebrasProvider,
        MockProvider,
        OllamaProvider,
    )

    PROVIDERS.update(
        {
            "ollama": lambda model: OllamaProvider(model),
            "cerebras": lambda model: CerebrasProvider(model),
            "mock": lambda model: MockProvider(model=model),
        }
    )
    return PROVIDERS


def provider_from_ref(model_ref: str) -> Any:
    """Build the provider named by a ``provider:model`` reference.

    Thin delegation to the kernel's canonical ``provider_for`` factory —
    one implementation resolves references for the CLI, the sidecar, and
    MCP alike (invariant 11: no hand-maintained parallel logic).

    Example:
        >>> p = provider_from_ref("ollama:gemma4:e4b")
        >>> p.name, p.model
        ('ollama', 'gemma4:e4b')
        >>> provider_from_ref("mock:gemma4:e4b").capabilities().egress
        <EgressClass.DEVICE: 'device'>
    """
    from agenticcarekit.kernel.providers import provider_for

    return provider_for(model_ref)


def discover_redactors() -> dict[str, Redactor]:
    """Every redactor an installed pack provides, name → instance.

    Discovery is by entry point (``agenticcarekit.packs``), so installing a
    pack makes its redactor available with no central registration (brief §10).

    Example:
        >>> sorted(discover_redactors())
        ['healthcare.phi']
    """
    from importlib.metadata import entry_points

    found: dict[str, Redactor] = {}
    for ep in entry_points().select(group="agenticcarekit.packs"):
        try:
            module = ep.load()
        except Exception:  # noqa: BLE001 - a broken pack must not break the sidecar
            continue
        for obj in vars(module).values():
            if not isinstance(obj, type):
                continue
            name = getattr(obj, "name", None)
            if not isinstance(name, str) or not callable(getattr(obj, "redact", None)):
                continue
            try:
                found[name] = obj()
            except Exception:  # noqa: BLE001 - a redactor needing args is not ours
                continue
    return found


def build_policy(
    root: Path,
    request_policy: Mapping[str, Any] | None,
    *,
    emit: Callable[[Any], None] | None = None,
) -> Any:
    """Assemble the :class:`Policy` for one generation.

    Resolution order for both fields: what the request declared, else what the
    project's ``ack.toml`` declares, else the strictest thing possible
    (``device``, no redactor). A missing ``ack.toml`` is not an error here —
    the sidecar can serve a client that has no project at all — but it does
    mean the boundary defaults closed.

    A redactor is only ever registered when it was *named*. ``Policy`` would
    otherwise adopt a lone installed redactor as the default, and "the pack you
    happened to install redacted your PHI" is not a boundary anyone can audit.
    """
    from agenticcarekit.kernel.policy import Policy

    declared = dict(request_policy or {})
    cfg: AckConfig | None = None
    if (root / "ack.toml").is_file():
        try:
            cfg = AckConfig.load(root / "ack.toml")
        except AckError:
            cfg = None

    if "egress" in declared and declared["egress"] is not None:
        try:
            egress = EgressClass(str(declared["egress"]))
        except ValueError:
            raise AckError(
                f"invalid egress class '{declared['egress']}'",
                code="E403",
                why="egress must be one of: device, trusted-network, public-cloud",
                fix='send {"policy": {"egress": "device"}}',
            ) from None
    else:
        egress = cfg.egress if cfg else EgressClass.DEVICE

    if "redactor" in declared:
        redactor_name = declared["redactor"]
    else:
        redactor_name = cfg.redactor if cfg else None

    redactors: dict[str, Redactor] = {}
    if redactor_name:
        installed = discover_redactors()
        if redactor_name not in installed:
            raise AckError(
                f'redactor "{redactor_name}" is not installed',
                code="E302",
                why="the policy engine refuses to guess — a silently missing "
                "redactor would be an open boundary.",
                fix="install the pack that provides it, or send "
                '{"policy": {"redactor": null}} and keep egress on-device',
                details={"installed": sorted(installed)},
            )
        redactors = {redactor_name: installed[redactor_name]}

    return Policy(egress, redactors, redactor_name or None, emit)


def _message_text(
    message: Mapping[str, Any],
    *,
    sensitive_fields: set[str],
    policy: Any,
    provider: Provider,
    redacted: list[str],
) -> str:
    """Assemble one message's text, routing declared fields through the policy.

    Fields named in ``sensitive_fields`` are wrapped in ``Sensitive`` and can
    only reach the provider through ``Policy.unwrap`` — the single sanctioned
    path (Contract 2). Everything else is plain text and travels as-is.

    One honest caveat about ``PolicyViolation.call_site``: for a remote client
    it points at *this* function, because this is where the value entered the
    boundary. The client's own line number is not knowable from here; the
    field name it declared is, and that is what the error names.
    """
    parts: list[str] = []
    base = message.get("text") or message.get("content") or ""
    if base:
        parts.append(str(base))
    fields = message.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise AckError(
            "message 'fields' must be an object of name → value",
            code="E401",
            why="named fields are what the policy engine attaches sensitivity to.",
            fix='{"role": "user", "text": "...", "fields": {"intake_note": "..."}}',
        )
    for field_name, value in fields.items():
        text = str(value)
        if field_name in sensitive_fields:
            boxed: Sensitive[str] = Sensitive(text, label=str(field_name))
            authorized = policy.unwrap(boxed, provider)
            if authorized != text:
                redacted.append(str(field_name))
            parts.append(f"{field_name}: {authorized}")
        else:
            parts.append(f"{field_name}: {text}")
    return "\n".join(parts)


def generate(
    *,
    root: Path,
    hub: TraceHub,
    model_ref: str,
    messages: Sequence[Mapping[str, Any]],
    sensitive_fields: Sequence[str] = (),
    policy: Mapping[str, Any] | None = None,
    think: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    provider_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Generate through the policy boundary. **The** reason the sidecar exists.

    A thin client posts ``{model_ref, messages, sensitive_fields, policy}``. It
    never holds a ``Provider``, so it cannot take a second route to one:

    1. the sidecar builds the provider,
    2. ``Policy.check_provider`` refuses any provider broader than the
       project's declared egress limit (**E303**) — sensitive value or not,
    3. every field named in ``sensitive_fields`` is boxed in ``Sensitive`` and
       unwrapped through ``Policy.unwrap``, which redacts or refuses (**E301**)
       exactly as the enforcement matrix says,
    4. the model call and every policy decision emit ``TraceEvent``\\ s into the
       server's tracer, which the client can read back or stream.

    ``bytes_out`` on the model event is the UTF-8 size of the assembled prompt
    handed to the provider; protocol framing is the provider's and is not
    counted. Trace payloads carry decisions, never values.
    """
    if not messages:
        raise AckError(
            "no messages to generate from",
            code="E401",
            why="a generation needs at least one message.",
            fix='{"model_ref": "ollama:gemma4:e4b", "messages": [{"role": "user", "text": "hi"}]}',
        )
    factory = provider_factory or provider_from_ref
    provider = factory(model_ref)
    pol = build_policy(root, policy, emit=hub.policy_emitter())
    declared = {str(f) for f in sensitive_fields}
    redacted: list[str] = []
    first_event = len(hub.tracer.events)

    try:
        # Applies to every call, not just sensitive ones: a project that
        # declared egress = "device" did not agree to send anything anywhere.
        egress = pol.check_provider(provider)

        built: list[Message] = []
        for message in messages:
            role = str(message.get("role", "user"))
            if role not in ("system", "user", "assistant", "tool"):
                raise AckError(
                    f"unknown message role '{role}'",
                    code="E401",
                    why="roles are the closed set from Contract 1.",
                    fix="use one of: system, user, assistant, tool",
                )
            text = _message_text(
                message,
                sensitive_fields=declared,
                policy=pol,
                provider=provider,
                redacted=redacted,
            )
            built.append(Message.text(role, text))  # type: ignore[arg-type]

        req = GenerateRequest(
            messages=tuple(built),
            model=getattr(provider, "model", None),
            think=think,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        bytes_out = sum(
            len(part.text.encode("utf-8")) for m in built for part in m.parts  # type: ignore[union-attr]
        )
        started = time.perf_counter()
        response = provider.generate(req)
        duration_ms = (time.perf_counter() - started) * 1000.0
        hub.tracer.emit(
            "model",
            egress,
            bytes_out,
            {
                "model": req.model or model_ref,
                "provider": provider.name,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "duration_ms": duration_ms,
            },
        )
    except AckError as err:
        hub.tracer.emit(
            "error",
            EgressClass.DEVICE,
            0,
            {"code": err.code, "message": err.message, "provider": getattr(provider, "name", "?")},
        )
        raise

    return {
        "model_ref": model_ref,
        "provider": provider.name,
        "egress": egress.value,
        "text": response.text,
        "thinking": response.thinking,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls
        ],
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "bytes_out": bytes_out,
        "policy": {
            "egress_limit": pol.egress_limit.value,
            "redactor": pol.default_redactor,
            "sensitive_fields": sorted(declared),
            "redacted_fields": sorted(set(redacted)),
        },
        "run_id": hub.tracer.run_id,
        "trace": [e.to_dict() for e in hub.tracer.events[first_event:]],
    }
