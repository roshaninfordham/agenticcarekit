"""W-B hardening — the ways a developer accidentally walks around the boundary.

Each test below closes one bypass. The last section is the honest part: the
gaps that are *not* closed, asserted as tests so they cannot rot into a false
claim. The boundary defends against accident, not malice (see
``packages/agenticcarekit/kernel/policy/THREATMODEL.md``).
"""

from __future__ import annotations

import copy
import json
import logging
import pickle
from dataclasses import dataclass

import pytest
from agenticcarekit.kernel.contracts import Capabilities, EgressClass, Redaction, Sensitive
from agenticcarekit.kernel.policy import Policy, PolicyViolation

SECRET = "Jane Doe, MRN 99321"


class StubProvider:
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


# ── bypass 1: printing / interpolating the box ───────────────────────────


def test_repr_str_and_format_are_masked():
    s = Sensitive(SECRET, label="intake_note")
    for rendering in (
        repr(s),
        str(s),
        f"{s}",
        f"{s!s}",
        f"{s!r}",
        f"{s:>60}",
        "{}".format(s),  # noqa: UP032 - the point is exercising .format()
        "%s" % (s,),  # noqa: UP031 - and the %-interpolation path
        "%r" % (s,),  # noqa: UP031
    ):
        assert "Jane Doe" not in rendering
        assert "99321" not in rendering
        assert "intake_note" in rendering  # the label and origin are safe to show


def test_log_formatting_does_not_leak(caplog):
    s = Sensitive(SECRET, label="intake_note")
    with caplog.at_level(logging.INFO):
        logging.getLogger("ack.test").info("about to send %s", s)
    assert "Jane Doe" not in caplog.text
    assert "intake_note" in caplog.text


# ── bypass 2: serializing the box ────────────────────────────────────────


def test_pickling_is_refused_at_every_protocol():
    s = Sensitive(SECRET, label="intake_note")
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError, match="cannot be pickled"):
            pickle.dumps(s, protocol)
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps({"note": s})  # smuggled inside a container


def test_copying_is_refused():
    # copy/deepcopy ride the same __reduce__ hook as pickle, so a Sensitive
    # cannot be cloned into a log record or a task payload.
    s = Sensitive(SECRET)
    with pytest.raises(TypeError, match="cannot be pickled"):
        copy.copy(s)
    with pytest.raises(TypeError, match="cannot be pickled"):
        copy.deepcopy({"payload": s})


def test_json_dumps_raises_type_error():
    s = Sensitive(SECRET, label="intake_note")
    with pytest.raises(TypeError):
        json.dumps(s)
    with pytest.raises(TypeError):
        json.dumps({"note": s})
    with pytest.raises(TypeError):
        json.dumps([{"parts": [s]}])


# ── bypass 3: a Sensitive nested in a structure someone str()s ───────────


@dataclass
class RequestBody:
    prompt: str
    note: object


def test_nesting_in_a_structure_still_masks_on_str():
    s = Sensitive(SECRET, label="intake_note")
    structures = [
        {"note": s},
        [s],
        (s,),
        {"outer": {"inner": [s]}},
        RequestBody(prompt="summarize", note=s),
    ]
    for structure in structures:
        for rendering in (str(structure), repr(structure), f"{structure}"):
            assert "Jane Doe" not in rendering
            assert "99321" not in rendering
    # containers built by str()-ing everything are covered because every
    # container's str() delegates to repr() of its members.


def test_map_does_not_launder_sensitivity():
    s = Sensitive(SECRET, label="intake_note").map(str.upper)
    assert isinstance(s, Sensitive)
    assert "JANE DOE" not in repr(s)
    with pytest.raises(PolicyViolation):
        s.unwrap_for(StubProvider(EgressClass.PUBLIC_CLOUD), Policy(EgressClass.PUBLIC_CLOUD))


def test_attribute_scraping_finds_nothing_by_accident():
    s = Sensitive(SECRET)
    with pytest.raises(TypeError):
        vars(s)  # __slots__, so no __dict__
    assert not hasattr(s, "__dict__")
    assert not hasattr(s, "value")
    assert getattr(s, "_Sensitive__value", None) is not None  # see gaps, below


# ── bypass 4: a provider broader than the project limit ──────────────────


def test_trusted_network_provider_is_refused_by_a_device_only_project():
    # The provider is honest — it declares trusted-network — but the project
    # declared device. Broader than the limit is refused outright (E303),
    # before the value is revealed.
    policy = Policy(EgressClass.DEVICE)
    with pytest.raises(PolicyViolation) as exc_info:
        Sensitive(SECRET, label="intake_note").unwrap_for(
            StubProvider(EgressClass.TRUSTED_NETWORK, "self-hosted-vllm"), policy
        )
    exc = exc_info.value
    assert exc.code == "E303"
    assert exc.provider == "self-hosted-vllm"
    assert exc.field_name == "intake_note"
    assert exc.call_site is not None


def test_a_declared_redactor_does_not_widen_the_egress_limit():
    # Declaring healthcare.phi means "redact when you must", never
    # "you may now talk to anyone".
    class Mask:
        name = "stub.phi"

        def redact(self, text):
            return "[REDACTED]", [Redaction("NAME", 0, len(text), "[REDACTED]")]

    policy = Policy(EgressClass.DEVICE, {"stub.phi": Mask()})
    with pytest.raises(PolicyViolation) as exc_info:
        Sensitive(SECRET).unwrap_for(StubProvider(EgressClass.PUBLIC_CLOUD), policy)
    assert exc_info.value.code == "E303"


# ── bypass 5: leaking through the audit trail itself ─────────────────────


def test_no_trace_event_ever_carries_the_raw_value():
    class Mask:
        name = "stub.phi"

        def redact(self, text):
            return "[REDACTED]", [Redaction("NAME", 0, len(text), text)]

    events = []
    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": Mask()}, emit=events.append)
    Sensitive(SECRET, label="intake_note").unwrap_for(StubProvider(EgressClass.DEVICE), policy)
    Sensitive(SECRET, label="intake_note").unwrap_for(
        StubProvider(EgressClass.PUBLIC_CLOUD), policy
    )
    try:
        Sensitive(SECRET, label="bytes_note").unwrap_for(
            StubProvider(EgressClass.PUBLIC_CLOUD), Policy(EgressClass.PUBLIC_CLOUD)
        )
    except PolicyViolation:
        pass

    assert events
    for event in events:
        line = event.to_json()
        assert "Jane Doe" not in line
        # the Redaction carried the raw span as its replacement; the trace
        # records categories and counts only, never spans.
        assert "99321" not in line


# ── bypass 6: sneaking a value past the engine's front door ──────────────


def test_unwrap_refuses_a_bare_value_instead_of_silently_allowing_it():
    policy = Policy(EgressClass.PUBLIC_CLOUD)
    with pytest.raises(TypeError, match="takes a Sensitive value"):
        policy.unwrap(SECRET, StubProvider(EgressClass.PUBLIC_CLOUD))


def test_non_text_sensitive_cannot_be_smuggled_past_a_text_redactor():
    class Mask:
        name = "stub.phi"

        def redact(self, text):  # pragma: no cover - must never run
            raise AssertionError("redactor called with a non-str payload")

    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.phi": Mask()})
    payload = Sensitive({"mrn": 99321}, label="patient_record")
    with pytest.raises(PolicyViolation) as exc_info:
        payload.unwrap_for(StubProvider(EgressClass.PUBLIC_CLOUD), policy)
    exc = exc_info.value
    assert exc.code == "E301"
    assert "cannot be redacted" in str(exc)
    assert "text only" in (exc.why or "")
    assert exc.field_name == "patient_record"


def test_a_redactor_returning_non_text_is_not_trusted():
    class Broken:
        name = "stub.broken"

        def redact(self, text):
            return {"clean": "[REDACTED]"}, []

    policy = Policy(EgressClass.PUBLIC_CLOUD, {"stub.broken": Broken()})
    with pytest.raises(TypeError, match="expected str"):
        Sensitive(SECRET).unwrap_for(StubProvider(EgressClass.PUBLIC_CLOUD), policy)


# ── the gaps we do NOT close (asserted, so the claim stays honest) ───────


def test_gap_name_mangled_internals_are_reachable_by_a_determined_developer():
    # Python has no private state. This is documented, not defended: the
    # guarantee is against accident, and the audit trail is that the only
    # sanctioned accessor is greppably named dangerously_reveal.
    s = Sensitive(SECRET)
    assert s._Sensitive__value == SECRET
    assert s.dangerously_reveal() == SECRET


def test_gap_an_unwrapped_value_carries_no_taint():
    # Once policy has authorized and returned text, it is an ordinary str.
    # Nothing downstream can tell where it came from.
    policy = Policy(EgressClass.DEVICE)
    out = Sensitive(SECRET).unwrap_for(StubProvider(EgressClass.DEVICE), policy)
    assert out == SECRET
    assert str(out) == SECRET


def test_gap_a_provider_that_lies_about_its_egress_defeats_the_boundary():
    # Declared-capability spoofing is out of scope: the engine trusts
    # provider.capabilities().egress, because nothing at this layer can
    # observe a socket. Vet providers like the code they are.
    liar = StubProvider(EgressClass.DEVICE, "definitely-local")
    out = Sensitive(SECRET).unwrap_for(liar, Policy(EgressClass.DEVICE))
    assert out == SECRET
