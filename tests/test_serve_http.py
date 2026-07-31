"""W-K · the HTTP sidecar — the boundary must hold over the wire.

The point of Tier 2 (brief §3) is that policy, redaction and trace live in one
process, so a thin client in any language *cannot* bypass PHI enforcement. The
load-bearing tests here are the two generate cases: a public-cloud provider
with a sensitive field and no redactor is refused with **E301** as **403**, and
a device provider succeeds with the decision visible in the trace.

Everything runs through ``fastapi.testclient.TestClient`` — no live socket, no
network, and the machine is a recorded fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import (
    Capabilities,
    EgressClass,
    GenerateResponse,
    Modality,
    Redaction,
)
from agenticcarekit.kernel.providers import MockProvider
from agenticcarekit.serve.app import create_app, status_for
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures_cli"
MACHINES = FIXTURES / "machines"

PUBLIC_CLOUD = Capabilities(
    modalities_in=frozenset({Modality.TEXT}),
    modalities_out=frozenset({Modality.TEXT}),
    tool_calling=True,
    streaming=True,
    context_tokens=262_144,
    thinking=True,
    egress=EgressClass.PUBLIC_CLOUD,
)


@pytest.fixture(autouse=True)
def offline_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACK_MACHINE_FACTS", str(MACHINES / "mac-m3-e4b-pulled.json"))
    monkeypatch.setenv("ACK_OFFLINE", "1")


def cloud_provider(_ref: str) -> MockProvider:
    """A mock that *declares* public-cloud egress. Declaration is the contract."""
    provider = MockProvider(
        [GenerateResponse(text="hosted answer")], PUBLIC_CLOUD, model="gemma-4-31b"
    )
    provider.name = "cerebras"
    return provider


def device_provider(_ref: str) -> MockProvider:
    """A mock that declares device egress: nothing leaves the machine."""
    return MockProvider([GenerateResponse(text="on-device answer")], model="gemma4:e4b")


class Blackout:
    """A redactor stub. Real ones live in packs (``healthcare.phi``)."""

    name = "test.blackout"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        return "[REDACTED]", [Redaction("NAME", 0, len(text), "[REDACTED]")]


@pytest.fixture
def sidecar(tmp_path: Path):
    """A client for a sidecar rooted at ``tmp_path``, plus its auth header."""

    def build(provider_factory=None) -> tuple[TestClient, dict[str, str]]:
        app = create_app(tmp_path, provider_factory=provider_factory)
        token = (tmp_path / ".ack" / "serve.token").read_text(encoding="utf-8").strip()
        return TestClient(app), {"Authorization": f"Bearer {token}"}

    return build


def data_of(response) -> dict:
    payload = response.json()
    assert payload["ok"] is True, payload["error"]
    assert payload["envelope_version"] == 1
    return payload["data"]


# ── auth ─────────────────────────────────────────────────────────────────


def test_health_needs_no_token(sidecar, tmp_path: Path) -> None:
    """A client must be able to find the sidecar before it has a token."""
    client, auth = sidecar()
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = data_of(response)
    assert data["status"] == "ok"
    assert data["telemetry"] is False
    assert data["auth_required"] is True
    # The path is public; the value never is.
    assert data["token_path"] == str(tmp_path / ".ack" / "serve.token")
    secret = auth["Authorization"].removeprefix("Bearer ")
    assert secret not in json.dumps(data)


def test_doctor_without_a_token_is_401(sidecar) -> None:
    """Everything except health fails closed."""
    client, _ = sidecar()
    response = client.get("/v1/doctor")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["fix"].startswith("Authorization: Bearer")


def test_a_wrong_token_is_indistinguishable_from_none(sidecar) -> None:
    client, _ = sidecar()
    assert client.get("/v1/doctor", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/doctor", headers={"Authorization": "Basic x"}).status_code == 401


def test_doctor_with_a_token_returns_a_valid_envelope(sidecar) -> None:
    """The same payload ``ack doctor --json`` prints."""
    client, auth = sidecar()
    response = client.get("/v1/doctor?offline=true", headers=auth)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "envelope_version",
        "ok",
        "command",
        "version",
        "elapsed_ms",
        "data",
        "error",
    }
    assert payload["command"] == "doctor"
    data = payload["data"]
    assert data["facts"]["os"] == "Darwin"
    assert isinstance(data["problems"], list)


# ── the boundary ─────────────────────────────────────────────────────────


def test_sensitive_field_to_public_cloud_without_a_redactor_is_403_e301(sidecar) -> None:
    """The privacy boundary holds over HTTP, with the same error dict.

    This is the whole Tier-2 claim in one assertion: a thin client that
    declared a field sensitive cannot get it to a public-cloud provider, and
    the refusal it receives is the CLI's ``E301`` verbatim.
    """
    client, auth = sidecar(cloud_provider)
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "cerebras:gemma-4-31b",
            "messages": [
                {
                    "role": "user",
                    "text": "Summarise this encounter.",
                    "fields": {"intake_note": "Jane Doe, MRN 99321"},
                }
            ],
            "sensitive_fields": ["intake_note"],
            "policy": {"egress": "public-cloud", "redactor": None},
        },
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["ok"] is False
    error = payload["error"]
    assert error["code"] == "E301"
    assert set(error) == {"code", "message", "why", "fix", "details"}
    # A vague policy error is one nobody fixes (Contract 2).
    assert error["details"]["field"] == "intake_note"
    assert error["details"]["provider"] == "cerebras"
    assert error["details"]["call_site"]
    # And the value itself never appears in the refusal.
    assert "99321" not in json.dumps(payload)


def test_a_provider_broader_than_the_limit_is_refused_outright(sidecar) -> None:
    """E303: not sensitive, still refused — the project declared a limit."""
    client, auth = sidecar(cloud_provider)
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "cerebras:gemma-4-31b",
            "messages": [{"role": "user", "text": "hello"}],
            "policy": {"egress": "device"},
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "E303"


def test_a_declared_redactor_lets_the_call_through_redacted(sidecar, monkeypatch) -> None:
    """The third row of the matrix: public-cloud, redacted only."""
    monkeypatch.setattr(
        "agenticcarekit.serve.ops.discover_redactors", lambda: {"test.blackout": Blackout()}
    )
    client, auth = sidecar(cloud_provider)
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "cerebras:gemma-4-31b",
            "messages": [{"role": "user", "text": "Summarise.",
                          "fields": {"intake_note": "Jane Doe, MRN 99321"}}],
            "sensitive_fields": ["intake_note"],
            "policy": {"egress": "public-cloud", "redactor": "test.blackout"},
        },
    )
    assert response.status_code == 200
    data = data_of(response)
    assert data["policy"]["redacted_fields"] == ["intake_note"]
    assert [e["kind"] for e in data["trace"]].count("redaction") == 1
    # What actually left: the redacted text, never the raw note.
    trace = client.get("/v1/trace", headers=auth).json()["data"]
    assert "99321" not in json.dumps(trace)


def test_an_uninstalled_redactor_is_e302_not_a_silent_pass(sidecar) -> None:
    client, auth = sidecar(cloud_provider)
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "cerebras:gemma-4-31b",
            "messages": [{"role": "user", "text": "hi"}],
            "policy": {"egress": "public-cloud", "redactor": "nobody.provides.this"},
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "E302"


def test_device_egress_generates_and_the_decision_lands_in_the_trace(sidecar) -> None:
    """Happy path: an answer, plus the audit trail that proves what happened."""
    client, auth = sidecar(device_provider)
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "mock:gemma4:e4b",
            "messages": [{"role": "user", "text": "Summarise.",
                          "fields": {"intake_note": "Jane Doe, MRN 99321"}}],
            "sensitive_fields": ["intake_note"],
        },
    )
    assert response.status_code == 200
    data = data_of(response)
    assert data["text"] == "on-device answer"
    assert data["egress"] == "device"
    assert data["policy"]["redacted_fields"] == []
    assert data["bytes_out"] > 0

    trace = client.get("/v1/trace", headers=auth).json()["data"]
    kinds = [e["kind"] for e in trace["events"]]
    assert "model" in kinds and "policy" in kinds
    assert trace["bytes_egressed"] == 0  # the "0 bytes egressed" panel
    model_event = next(e for e in trace["events"] if e["kind"] == "model")
    assert model_event["payload"]["provider"] == "mock"
    assert model_event["egress"] == "device"


def test_a_failed_generation_emits_an_error_event(sidecar) -> None:
    """The trace records the refusal too, with the code and no payload data."""
    client, auth = sidecar(cloud_provider)
    client.post(
        "/v1/generate",
        headers=auth,
        json={
            "model_ref": "cerebras:gemma-4-31b",
            "messages": [{"role": "user", "text": "x", "fields": {"note": "Jane Doe"}}],
            "sensitive_fields": ["note"],
            "policy": {"egress": "public-cloud", "redactor": None},
        },
    )
    trace = client.get("/v1/trace", headers=auth).json()["data"]
    errors = [e for e in trace["events"] if e["kind"] == "error"]
    assert errors and errors[-1]["payload"]["code"] == "E301"
    assert "Jane Doe" not in json.dumps(trace)


def test_an_unknown_provider_is_refused_before_anything_is_built(sidecar) -> None:
    client, auth = sidecar()
    response = client.post(
        "/v1/generate",
        headers=auth,
        json={"model_ref": "nobody:some-model", "messages": [{"role": "user", "text": "hi"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "E401"


# ── SSE ──────────────────────────────────────────────────────────────────


def test_the_trace_stream_delivers_events_over_sse(sidecar) -> None:
    """``GET /v1/trace/stream`` streams TraceEvents as they are emitted.

    Exercised through ``TestClient.stream`` — a real SSE response, parsed off
    the wire. ``limit`` makes the stream terminate, which is what lets a test
    (or a shell pipeline) consume it without killing the connection.
    """
    client, auth = sidecar(device_provider)
    client.post(
        "/v1/generate",
        headers=auth,
        json={"model_ref": "mock:gemma4:e4b", "messages": [{"role": "user", "text": "hi"}]},
    )
    events = []
    with client.stream("GET", "/v1/trace/stream?limit=2", headers=auth) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
            if len(events) >= 2:
                break
    assert len(events) >= 1
    assert events[0]["kind"] in ("policy", "model")
    assert set(events[0]) == {
        "ts",
        "run_id",
        "span_id",
        "parent_span_id",
        "kind",
        "egress",
        "bytes_out",
        "payload",
    }


def test_the_stream_generator_terminates_on_an_idle_timeout() -> None:
    """The generator is also directly testable, without a transport."""
    import asyncio

    from agenticcarekit.serve.trace import TraceHub, stream_events

    hub = TraceHub(run_id="r1")
    hub.tracer.emit("policy", EgressClass.DEVICE, 0, {"decision": "allow"})

    async def drain() -> list[dict[str, str]]:
        return [e async for e in stream_events(hub, idle_timeout=0.05)]

    events = asyncio.run(drain())
    assert len(events) == 1
    assert events[0]["event"] == "trace"
    assert hub.subscriber_count == 0  # the subscription is cleaned up


# ── the rest of the surface ──────────────────────────────────────────────


def test_init_then_manifest_then_add_capability_over_http(sidecar, tmp_path: Path) -> None:
    """The write endpoints, against a real packaged blueprint, in tmp_path."""
    client, auth = sidecar()
    init = data_of(
        client.post(
            "/v1/init",
            headers=auth,
            json={"path": "clinic", "blueprint": "on-device", "offline": True},
        )
    )
    assert (tmp_path / "clinic" / "ack.toml").is_file()
    assert init["generated"]["project_name"] == "clinic"

    manifest = data_of(client.get("/v1/manifest?path=clinic", headers=auth))
    assert manifest["project"]["blueprint"] == "on-device"

    added = data_of(
        client.post(
            "/v1/capabilities/add", headers=auth, json={"capability": "voice", "path": "clinic"}
        )
    )
    assert "voice" in added["capabilities"]

    unknown = client.post(
        "/v1/capabilities/add", headers=auth, json={"capability": "telepathy", "path": "clinic"}
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "E401"


def test_eval_without_a_golden_set_is_an_honest_422(sidecar, tmp_path: Path) -> None:
    client, auth = sidecar()
    client.post(
        "/v1/init", headers=auth, json={"path": "clinic", "blueprint": "on-device"}
    )
    response = client.post("/v1/eval", headers=auth, json={"path": "clinic"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E601"


def test_models_endpoint_mirrors_the_catalog(sidecar) -> None:
    client, auth = sidecar()
    data = data_of(client.get("/v1/models?modality=audio&offline=true", headers=auth))
    assert [m["tag"] for m in data["models"]] == [
        "gemma4:e2b",
        "gemma4:e2b-mlx",
        "gemma4:e4b",
        "gemma4:e4b-mlx",
    ]
    # From the recorded machine, not a probe: this laptop has e4b-mlx already.
    assert "gemma4:e4b-mlx" in data["installed_tags"]
    assert next(m for m in data["models"] if m["tag"] == "gemma4:e4b-mlx")["already_pulled"]


def test_errors_endpoint_is_the_registry(sidecar) -> None:
    client, auth = sidecar()
    data = data_of(client.get("/v1/errors/E203", headers=auth))
    assert data["title"] == "Model does not support a required input modality"
    assert client.get("/v1/errors/E999", headers=auth).status_code == 400


def test_openapi_is_complete_enough_to_generate_a_client_from(sidecar) -> None:
    """The OpenAPI document is the Tier-2 contract, so assert it is real."""
    client, _ = sidecar()
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert {
        "/v1/health",
        "/v1/doctor",
        "/v1/manifest",
        "/v1/models",
        "/v1/init",
        "/v1/capabilities/add",
        "/v1/eval",
        "/v1/check",
        "/v1/generate",
        "/v1/trace",
        "/v1/trace/stream",
    } <= paths
    assert "/v1/errors/{code}" in paths
    generate = spec["components"]["schemas"]["GenerateBody"]
    assert set(generate["required"]) == {"model_ref", "messages"}
    assert "sensitive_fields" in generate["properties"]
    assert spec["info"]["title"] == "agenticcarekit sidecar"


def test_status_mapping_matches_the_code_ranges() -> None:
    """Error-code range → HTTP status is part of the contract."""
    from agenticcarekit.kernel.contracts import AckError, CapabilityMismatch, PolicyViolation

    assert status_for(PolicyViolation("x", code="E301")) == 403
    assert status_for(CapabilityMismatch("x", code="E203")) == 422
    assert status_for(AckError("x", code="E404")) == 404
    assert status_for(AckError("x", code="E401")) == 400
    assert status_for(AckError("x", code="E601")) == 422
