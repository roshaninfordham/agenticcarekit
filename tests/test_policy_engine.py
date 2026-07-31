"""W-B acceptance — the enforcement matrix, the errors, and the trace.

Everything here is offline: providers are local stubs conforming to the
``Provider`` protocol (real ones are W-A) and redactors are local stubs
(real ones live in packs, W-F).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import (
    Capabilities,
    EgressClass,
    PolicyContext,
    Redaction,
    Sensitive,
    TraceEvent,
)
from agenticcarekit.kernel.policy import Policy, PolicyViolation

HERE = Path(__file__).name
SECRET = "Jane Doe, MRN 99321, seen 2026-07-14"


# ── stubs ────────────────────────────────────────────────────────────────


class StubProvider:
    """Minimal Provider: the policy engine reads only ``name`` and egress."""

    def __init__(self, egress: EgressClass, name: str | None = None) -> None:
        self.name = name or f"stub-{egress.value}"
        self._egress = egress

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities_in=frozenset(),
            modalities_out=frozenset(),
            tool_calling=True,
            streaming=True,
            context_tokens=131072,
            thinking=True,
            egress=self._egress,
        )

    def generate(self, req):  # pragma: no cover - never called by policy
        raise AssertionError("policy tests never reach the network")

    def stream(self, req):  # pragma: no cover - never called by policy
        raise AssertionError("policy tests never reach the network")


class StubRedactor:
    """Replaces every digit run and the literal name. Stands in for a pack."""

    def __init__(self, name: str = "stub.phi") -> None:
        self.name = name
        self.calls = 0

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        self.calls += 1
        out = text.replace("Jane Doe", "[NAME]")
        spans = [Redaction("NAME", 0, 8, "[NAME]")]
        digits = "".join(ch for ch in out if ch.isdigit())
        if digits:
            out = "".join("#" if ch.isdigit() else ch for ch in out)
            spans.append(Redaction("MRN", 0, len(out), "#"))
        return out, spans


def device() -> StubProvider:
    return StubProvider(EgressClass.DEVICE, "ollama")


def trusted() -> StubProvider:
    return StubProvider(EgressClass.TRUSTED_NETWORK, "self-hosted-vllm")


def cloud() -> StubProvider:
    return StubProvider(EgressClass.PUBLIC_CLOUD, "cerebras")


def collector() -> tuple[list[TraceEvent], Callable[[TraceEvent], None]]:
    events: list[TraceEvent] = []
    return events, events.append


# ── (a) sensitive → public cloud, no redactor ────────────────────────────


def test_sensitive_to_public_cloud_without_redactor_raises():
    events, emit = collector()
    policy = Policy(EgressClass.PUBLIC_CLOUD, emit=emit)
    note = Sensitive(SECRET, label="intake_note")
    origin_line = inspect.currentframe().f_lineno - 1
    provider = cloud()

    with pytest.raises(PolicyViolation) as exc_info:
        note.unwrap_for(provider, policy)

    exc = exc_info.value
    assert exc.code == "E301"
    assert exc.field_name == "intake_note"
    assert exc.provider == "cerebras"
    assert exc.call_site is not None
    assert exc.call_site.endswith(f"{HERE}:{origin_line}")
    # a deny decision is still a decision: it lands in the trace
    assert [e.kind for e in events] == ["policy"]
    assert events[0].payload["decision"] == "deny"
    assert events[0].payload["label"] == "intake_note"
    assert events[0].payload["provider"] == "cerebras"


def test_violation_message_makes_the_fix_obvious():
    policy = Policy(EgressClass.PUBLIC_CLOUD)
    note = Sensitive(SECRET, label="intake_note")

    with pytest.raises(PolicyViolation) as exc_info:
        note.unwrap_for(cloud(), policy)

    rendered = exc_info.value.render()
    assert "E301" in rendered
    assert "intake_note" in rendered
    assert "cerebras" in rendered
    assert note.origin in rendered  # exact call site, not a vague gesture
    assert "[policy]" in rendered
    assert 'redactor = "healthcare.phi"' in rendered
    assert SECRET not in rendered  # the error never quotes the data


def test_violation_serializes_for_json_and_mcp():
    policy = Policy(EgressClass.PUBLIC_CLOUD)
    with pytest.raises(PolicyViolation) as exc_info:
        Sensitive(SECRET, label="intake_note").unwrap_for(cloud(), policy)
    d = exc_info.value.to_dict()
    assert d["code"] == "E301"
    assert d["details"]["field"] == "intake_note"
    assert d["details"]["provider"] == "cerebras"
    assert SECRET not in json.dumps(d)


# ── (b) sensitive → public cloud, redactor declared ──────────────────────


def test_declared_redactor_lets_the_value_through_redacted():
    events, emit = collector()
    redactor = StubRedactor()
    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": redactor}, "stub.phi", emit)

    out = Sensitive(SECRET, label="intake_note").unwrap_for(cloud(), policy)

    assert redactor.calls == 1
    assert out == "[NAME], MRN #####, seen ####-##-##"
    assert "Jane Doe" not in out
    assert "99321" not in out

    kinds = [e.kind for e in events]
    assert kinds == ["redaction", "policy"]

    red = events[0]
    assert red.payload == {"redactor": "stub.phi", "categories": ["MRN", "NAME"], "count": 2}
    assert red.bytes_out == 0  # redacting egresses nothing
    assert red.egress is EgressClass.DEVICE

    decision = events[1]
    assert decision.payload["decision"] == "allow-redacted"
    assert decision.payload["label"] == "intake_note"
    assert decision.egress is EgressClass.PUBLIC_CLOUD


def test_single_declared_redactor_is_the_default():
    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": StubRedactor()})
    assert policy.default_redactor == "stub.phi"
    assert Sensitive(SECRET).unwrap_for(cloud(), policy) != SECRET


def test_label_specific_redactor_beats_the_default():
    default = StubRedactor("stub.phi")
    special = StubRedactor("stub.transcript")
    policy = Policy(
        EgressClass.PUBLIC_CLOUD,
        {"stub.phi": default, "transcript": special},
        "stub.phi",
    )
    assert policy.redactor_for(Sensitive("x", label="transcript")) is special
    assert policy.redactor_for(Sensitive("x", label="note")) is default


def test_redactor_for_is_none_when_nothing_is_declared():
    assert Policy().redactor_for(Sensitive("x")) is None


def test_unknown_declared_redactor_fails_at_construction_with_e302():
    with pytest.raises(PolicyViolation) as exc_info:
        Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": StubRedactor()}, "healthcare.phi")
    assert exc_info.value.code == "E302"
    assert "healthcare.phi" in str(exc_info.value)


# ── (d) device egress ────────────────────────────────────────────────────


def test_device_provider_gets_the_raw_value_and_an_allow_event():
    events, emit = collector()
    policy = Policy(EgressClass.DEVICE, emit=emit)

    assert Sensitive(SECRET, label="intake_note").unwrap_for(device(), policy) == SECRET

    assert [e.kind for e in events] == ["policy"]
    payload = events[0].payload
    assert payload["decision"] == "allow"
    assert payload["label"] == "intake_note"
    assert payload["provider"] == "ollama"
    assert "never leaves the machine" in payload["reason"]
    assert events[0].egress is EgressClass.DEVICE
    assert events[0].bytes_out == 0


def test_device_run_egresses_zero_bytes():
    events, emit = collector()
    policy = Policy(EgressClass.DEVICE, emit=emit)
    Sensitive(SECRET).unwrap_for(device(), policy)
    policy.check_provider(device())
    assert sum(e.bytes_out for e in events if e.egress != EgressClass.DEVICE) == 0


# ── the full enforcement matrix ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("limit", "provider_egress", "redactor", "expect"),
    [
        # non-sensitive row is check_provider's job; see below.
        (EgressClass.DEVICE, EgressClass.DEVICE, False, "raw"),
        (EgressClass.TRUSTED_NETWORK, EgressClass.DEVICE, False, "raw"),
        (EgressClass.PUBLIC_CLOUD, EgressClass.DEVICE, False, "raw"),
        (EgressClass.DEVICE, EgressClass.TRUSTED_NETWORK, False, "E303"),
        (EgressClass.TRUSTED_NETWORK, EgressClass.TRUSTED_NETWORK, False, "raw"),
        (EgressClass.PUBLIC_CLOUD, EgressClass.TRUSTED_NETWORK, False, "raw"),
        (EgressClass.DEVICE, EgressClass.PUBLIC_CLOUD, False, "E303"),
        (EgressClass.TRUSTED_NETWORK, EgressClass.PUBLIC_CLOUD, False, "E303"),
        (EgressClass.PUBLIC_CLOUD, EgressClass.PUBLIC_CLOUD, False, "E301"),
        (EgressClass.DEVICE, EgressClass.DEVICE, True, "raw"),
        (EgressClass.TRUSTED_NETWORK, EgressClass.TRUSTED_NETWORK, True, "raw"),
        (EgressClass.PUBLIC_CLOUD, EgressClass.TRUSTED_NETWORK, True, "raw"),
        (EgressClass.PUBLIC_CLOUD, EgressClass.PUBLIC_CLOUD, True, "redacted"),
        (EgressClass.DEVICE, EgressClass.PUBLIC_CLOUD, True, "E303"),
    ],
)
def test_enforcement_matrix(limit, provider_egress, redactor, expect):
    redactors = {"stub.phi": StubRedactor()} if redactor else None
    events, emit = collector()
    policy = Policy(limit, redactors, emit=emit)
    value = Sensitive(SECRET, label="intake_note")
    provider = StubProvider(provider_egress)

    if expect in {"E301", "E303"}:
        with pytest.raises(PolicyViolation) as exc_info:
            value.unwrap_for(provider, policy)
        assert exc_info.value.code == expect
        assert events[-1].payload["decision"] == "deny"
        return

    out = value.unwrap_for(provider, policy)
    if expect == "raw":
        assert out == SECRET
        assert events[-1].payload["decision"] == "allow"
    else:
        assert out != SECRET
        assert "Jane Doe" not in out
        assert events[-1].payload["decision"] == "allow-redacted"


def test_redaction_can_be_required_for_trusted_network_too():
    policy = Policy(
        EgressClass.TRUSTED_NETWORK,
        {"stub.phi": StubRedactor()},
        redact_at_or_above=EgressClass.TRUSTED_NETWORK,
    )
    out = Sensitive(SECRET).unwrap_for(trusted(), policy)
    assert "Jane Doe" not in out
    assert Sensitive(SECRET).unwrap_for(device(), policy) == SECRET


# ── the non-sensitive path ───────────────────────────────────────────────


def test_check_provider_allows_within_the_limit_and_traces_it():
    events, emit = collector()
    policy = Policy(EgressClass.TRUSTED_NETWORK, emit=emit)
    assert policy.check_provider(trusted()) is EgressClass.TRUSTED_NETWORK
    assert events[0].payload["decision"] == "allow"
    assert events[0].payload["label"] is None
    assert events[0].payload["call_site"] is None


def test_check_provider_refuses_a_broader_provider_even_without_sensitive_data():
    events, emit = collector()
    policy = Policy(EgressClass.DEVICE, emit=emit)
    with pytest.raises(PolicyViolation) as exc_info:
        policy.check_provider(cloud())
    exc = exc_info.value
    assert exc.code == "E303"
    assert exc.provider == "cerebras"
    assert exc.field_name is None
    assert "public-cloud" in str(exc)
    assert 'egress = "public-cloud"' in (exc.fix or "")
    assert events[0].payload["decision"] == "deny"


def test_non_provider_object_is_a_type_error_not_a_silent_allow():
    with pytest.raises(TypeError, match="Provider protocol"):
        Policy().check_provider(object())


# ── protocol conformance and trace shape ─────────────────────────────────


def test_policy_conforms_to_the_policycontext_protocol():
    policy = Policy(EgressClass.DEVICE)
    assert isinstance(policy.egress_limit, EgressClass)
    for member in ("egress_limit", "unwrap", "redactor_for"):
        assert hasattr(policy, member), member
    assert PolicyContext.__doc__  # the contract this implements


@pytest.mark.parametrize("limit", list(EgressClass))
def test_every_emitted_event_is_a_valid_trace_event(limit):
    events, emit = collector()
    policy = Policy(limit, {"stub.phi": StubRedactor()}, emit=emit)
    try:
        Sensitive(SECRET).unwrap_for(cloud(), policy)
    except PolicyViolation:
        pass
    policy.check_provider(device())

    assert events
    for event in events:
        line = json.loads(event.to_json())
        assert set(line) == {
            "ts",
            "run_id",
            "span_id",
            "parent_span_id",
            "kind",
            "egress",
            "bytes_out",
            "payload",
        }
        assert line["kind"] in {"policy", "redaction"}
        assert line["run_id"] == policy.run_id
        assert line["bytes_out"] == 0
        if line["kind"] == "policy":
            assert set(line["payload"]) == {
                "decision",
                "reason",
                "call_site",
                "label",
                "provider",
            }
            assert line["payload"]["decision"] in {"allow", "allow-redacted", "deny"}
        else:
            assert set(line["payload"]) == {"redactor", "categories", "count"}


def test_policy_without_emit_still_enforces():
    policy = Policy(EgressClass.DEVICE)  # no emitter wired
    with pytest.raises(PolicyViolation):
        Sensitive(SECRET).unwrap_for(cloud(), policy)


def test_repr_of_policy_is_reviewable():
    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": StubRedactor()})
    assert repr(policy) == (
        "Policy(egress='public-cloud', redactors=['stub.phi'], default_redactor='stub.phi')"
    )
