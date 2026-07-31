"""The HTTP sidecar — OpenAPI in, thin clients out.

``create_app`` builds the FastAPI application ``ack serve`` runs. Its OpenAPI
document is the contract Tier-2 clients (Go, Rust, Swift, Java/Kotlin, C#) are
*generated* from, which is what stops "available everywhere" from meaning five
hand-maintained ports that diverge in a month (brief §3).

Shape rules, all deliberate:

* every response is the same ``--json`` envelope the CLI emits
  (``{envelope_version, ok, command, version, elapsed_ms, data, error}``), so a
  client that can read ``ack doctor --json`` can read this API and vice versa;
* an :class:`AckError` becomes ``ok: false`` with ``error`` = ``err.to_dict()``
  and an HTTP status that matches the code range — an E301 arrives as **403**
  carrying the same dict the CLI prints, because the boundary must hold
  identically over the wire;
* ``/v1/health`` is the only unauthenticated route. Everything else requires
  ``Authorization: Bearer <token>`` (see :mod:`agenticcarekit.serve.auth`);
* nothing touches the network at import or at startup (invariant 5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agenticcarekit import __version__
from agenticcarekit.cli.output import envelope
from agenticcarekit.kernel.contracts import AckError

from . import ops
from .auth import TokenStore
from .trace import TraceHub, stream_events

__all__ = ["ERROR_STATUS", "create_app", "status_for"]

#: Error-code prefix → HTTP status. The mapping is part of the contract: a
#: client can branch on the status *or* read ``error.code`` and get the same
#: answer. E3xx is 403 because a policy refusal is exactly "forbidden".
ERROR_STATUS: dict[str, int] = {
    "E0": 503,  # environment: the machine cannot do this yet
    "E1": 502,  # model / provider / network: an upstream failed
    "E2": 422,  # capability mismatch: the request asked for what the model lacks
    "E3": 403,  # policy: refused at the privacy boundary
    "E4": 400,  # config: the request or the project is malformed
    "E5": 500,  # generation / templates
    "E6": 422,  # eval
}


def status_for(err: AckError) -> int:
    """HTTP status for an :class:`AckError`.

    Example:
        >>> from agenticcarekit.kernel.contracts import PolicyViolation
        >>> status_for(PolicyViolation("nope", code="E301"))
        403
        >>> status_for(AckError("missing", code="E404"))
        404
        >>> status_for(AckError("?", code="E999"))
        400
    """
    if err.code == "E404":
        return 404
    return ERROR_STATUS.get(err.code[:2], 400)


# ── request bodies (these become the OpenAPI schemas clients generate from) ──


class InitBody(BaseModel):
    """``POST /v1/init`` — non-interactive project generation."""

    path: str = Field(..., description="Where to generate. Relative paths resolve "
                                       "against the sidecar's root, never its cwd.")
    blueprint: str | None = Field(None, description="Blueprint name, e.g. 'on-device'.")
    model: str | None = Field(None, description="Force a model tag, e.g. 'gemma4:e4b'.")
    providers: str | None = Field(None, description="Comma-separated provider chain.")
    pack: str | None = Field(None, description="Domain pack, e.g. 'healthcare'.")
    capabilities: str | None = Field(None, description="Comma-separated capabilities.")
    name: str | None = Field(None, description="Project name (default: the directory name).")
    git: bool = Field(False, description="Run 'git init' in the generated tree.")
    offline: bool = Field(True, description="Probe without touching the network.")


class AddCapabilityBody(BaseModel):
    """``POST /v1/capabilities/add``."""

    capability: str = Field(..., description="One of: agents, extract, rag, voice.")
    path: str | None = Field(None, description="Project directory (default: the root).")


class ProjectBody(BaseModel):
    """``POST /v1/eval`` and ``POST /v1/check``."""

    path: str | None = Field(None, description="Project directory (default: the root).")
    offline: bool = Field(True, description="Run against mocks with networking disabled.")


class PolicyBody(BaseModel):
    """The policy a generation runs under, overriding ``ack.toml``."""

    egress: str | None = Field(None, description="device | trusted-network | public-cloud.")
    redactor: str | None = Field(None, description="Redactor name, e.g. 'healthcare.phi'. "
                                                   "Send null to declare *no* redactor.")


class MessageBody(BaseModel):
    """One conversation turn.

    ``text`` is the frame; ``fields`` are the named values the policy engine
    attaches sensitivity to. A field named in ``sensitive_fields`` is wrapped
    in ``Sensitive`` and can reach the provider only through ``Policy.unwrap``.
    """

    role: str = Field("user", description="system | user | assistant | tool.")
    text: str = Field("", description="Non-sensitive framing text.")
    fields: dict[str, str] = Field(default_factory=dict, description="Named values.")


class GenerateBody(BaseModel):
    """``POST /v1/generate`` — the kernel chokepoint."""

    model_ref: str = Field(..., description="provider:model, e.g. 'ollama:gemma4:e4b'.")
    messages: list[MessageBody] = Field(..., description="The conversation, in order.")
    sensitive_fields: list[str] = Field(
        default_factory=list,
        description="Field names to treat as Sensitive. These cannot reach a "
                    "public-cloud provider without a declared redactor (E301).",
    )
    policy: PolicyBody | None = Field(None, description="Overrides ack.toml's [policy].")
    think: bool = Field(False, description="Enable thinking (the <|think|> quirk).")
    temperature: float | None = Field(None, description="None = the model's known-good default.")
    max_tokens: int | None = Field(None, description="Cap on generated tokens.")


# ── the app ──────────────────────────────────────────────────────────────


_DESCRIPTION = """\
The agenticcarekit sidecar: the kernel over local HTTP.

The policy boundary, redaction and the trace live in **this** process. A client
of this API cannot bypass PHI enforcement, because it never holds a provider —
`POST /v1/generate` builds one, routes every declared sensitive field through
`Policy.unwrap`, and emits the trace.

Every response is the same envelope `ack <command> --json` prints. Every error
carries a registered code, why it happened, and the literal command that fixes
it. No telemetry, ever.
"""


def create_app(
    root: Path,
    token: str | None = None,
    *,
    hub: TraceHub | None = None,
    provider_factory: Any | None = None,
    require_auth: bool = True,
) -> FastAPI:
    """Build the sidecar application rooted at ``root``.

    Args:
        root: the project directory the sidecar serves. Client-supplied
            relative paths resolve against it.
        token: the bearer token. Omitted, it is read from (or minted into)
            ``<root>/.ack/serve.token`` — see :class:`TokenStore`.
        hub: the process tracer + SSE fan-out. One is created if omitted.
        provider_factory: ``model_ref -> Provider``, for tests and for
            embedding the sidecar in a host that owns its own providers.
        require_auth: leave this True. It exists so a test can exercise a
            route without a token, never so a deployment can.

    Example:
        >>> import tempfile
        >>> app = create_app(Path(tempfile.mkdtemp()))
        >>> app.title
        'agenticcarekit sidecar'
        >>> sorted(r.path for r in app.routes if r.path.startswith("/v1"))[:3]
        ['/v1/capabilities/add', '/v1/check', '/v1/doctor']
    """
    root = Path(root)
    store = TokenStore(root)
    token = store.ensure() if token is None else store.use(token)
    trace_hub = hub or TraceHub()

    app = FastAPI(
        title="agenticcarekit sidecar",
        version=__version__,
        description=_DESCRIPTION,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.root = root
    app.state.hub = trace_hub
    app.state.token_store = store
    app.state.provider_factory = provider_factory

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        """Bearer-token dependency. Fails closed and says nothing extra."""
        if not require_auth:
            return
        if not store.verify(authorization):
            raise _Unauthorized()

    auth = [Depends(authorize)]

    # ── errors ──────────────────────────────────────────────────────────

    @app.exception_handler(AckError)
    async def _ack_error(request: Request, exc: AckError) -> JSONResponse:
        """Render an AckError as the CLI envelope with a matching status."""
        return JSONResponse(
            status_code=status_for(exc),
            content=envelope(_command_of(request), ok=False, error=exc),
        )

    @app.exception_handler(_Unauthorized)
    async def _unauthorized(request: Request, exc: _Unauthorized) -> JSONResponse:
        err = AckError(
            "missing or invalid bearer token",
            code="E130",
            why="every endpoint except /v1/health needs the sidecar's local token.",
            fix=f"Authorization: Bearer $(cat {store.path})",
        )
        return JSONResponse(
            status_code=401,
            content=envelope(_command_of(request), ok=False, error=err),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── read-only ───────────────────────────────────────────────────────

    @app.get("/v1/health", summary="Liveness. The only route needing no token.")
    def health() -> dict[str, Any]:
        return envelope("health", data=ops.health(root, auth_required=require_auth))

    @app.get("/v1/doctor", dependencies=auth, summary="Honest environment report.")
    def doctor(offline: bool = Query(False, description="Skip network probes.")) -> dict[str, Any]:
        return envelope("doctor", data=ops.doctor(offline=offline))

    @app.get("/v1/manifest", dependencies=auth, summary="Describe a generated project.")
    def manifest(path: str | None = Query(None)) -> dict[str, Any]:
        return envelope("manifest", data=ops.get_manifest(root, path))

    @app.get("/v1/models", dependencies=auth, summary="Model catalog + what is pulled.")
    def models(
        modality: str | None = Query(None, description="text | image | audio."),
        min_context_tokens: int | None = Query(None),
        max_size_gb: float | None = Query(None),
        include_hosted: bool = Query(True),
        already_pulled_only: bool = Query(False),
        offline: bool = Query(False),
    ) -> dict[str, Any]:
        return envelope(
            "models",
            data=ops.search_models(
                modality=modality,
                min_context_tokens=min_context_tokens,
                max_size_gb=max_size_gb,
                include_hosted=include_hosted,
                already_pulled_only=already_pulled_only,
                offline=offline,
            ),
        )

    @app.get("/v1/errors/{code}", dependencies=auth, summary="Explain an error code.")
    def explain(code: str) -> dict[str, Any]:
        return envelope("explain", data=ops.explain_error(code))

    # ── writes ──────────────────────────────────────────────────────────

    @app.post("/v1/init", dependencies=auth, summary="Generate a project (non-interactive).")
    def init(body: InitBody) -> dict[str, Any]:
        return envelope(
            "init",
            data=ops.init_project(
                root,
                body.path,
                blueprint=body.blueprint,
                model=body.model,
                providers=body.providers,
                pack=body.pack,
                capabilities=body.capabilities,
                name=body.name,
                git=body.git,
                offline=body.offline,
            ),
        )

    @app.post("/v1/capabilities/add", dependencies=auth, summary="Enable a capability.")
    def add_capability(body: AddCapabilityBody) -> dict[str, Any]:
        return envelope("add", data=ops.add_capability(root, body.capability, body.path))

    @app.post("/v1/eval", dependencies=auth, summary="Score a project's golden set.")
    def run_eval(body: ProjectBody) -> dict[str, Any]:
        return envelope("eval", data=ops.run_eval(root, body.path, offline=body.offline))

    @app.post("/v1/check", dependencies=auth, summary="Lint + fast selftest.")
    def run_check(body: ProjectBody) -> dict[str, Any]:
        return envelope("check", data=ops.check(root, body.path))

    @app.post("/v1/generate", dependencies=auth, summary="Generate through the policy boundary.")
    def generate(body: GenerateBody) -> dict[str, Any]:
        return envelope(
            "generate",
            data=ops.generate(
                root=root,
                hub=trace_hub,
                model_ref=body.model_ref,
                messages=[m.model_dump() for m in body.messages],
                sensitive_fields=body.sensitive_fields,
                policy=body.policy.model_dump() if body.policy is not None else None,
                think=body.think,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                provider_factory=app.state.provider_factory,
            ),
        )

    # ── trace ───────────────────────────────────────────────────────────

    @app.get("/v1/trace", dependencies=auth, summary="Recent trace events.")
    def trace(limit: int | None = Query(None, description="Most recent N events.")) -> dict[str, Any]:
        return envelope("trace", data=trace_hub.snapshot(limit))

    @app.get("/v1/trace/stream", dependencies=auth, summary="Live trace over SSE.")
    async def trace_stream(
        replay: bool = Query(True, description="Send events recorded before connecting."),
        limit: int | None = Query(None, description="Close after N events."),
        idle_timeout: float | None = Query(None, description="Close after N idle seconds."),
    ) -> Any:
        from sse_starlette.sse import EventSourceResponse

        return EventSourceResponse(
            stream_events(
                trace_hub, replay=replay, limit=limit, idle_timeout=idle_timeout
            ),
            ping=15,
        )

    return app


class _Unauthorized(Exception):
    """Internal marker: the bearer token was missing or wrong."""


def _command_of(request: Request) -> str:
    """Envelope ``command`` for a request path: ``/v1/trace/stream`` → ``trace``.

    Example:
        >>> class R:
        ...     class url:
        ...         path = "/v1/capabilities/add"
        >>> _command_of(R())
        'capabilities'
    """
    parts = [p for p in str(request.url.path).split("/") if p]
    return parts[1] if len(parts) > 1 else "serve"
