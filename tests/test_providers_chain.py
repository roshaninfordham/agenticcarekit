"""W-A · fallback chains — resilient, but never a silent downgrade.

Acceptance (d): the primary times out, the fallback answers, the caller gets
a response. Everything else in here defends the boundary around that: a
fallback is not a way to route around a capability the user asked for.
"""

from __future__ import annotations

import httpx
import pytest
from agenticcarekit.kernel.contracts import (
    AckError,
    AudioPart,
    Capabilities,
    CapabilityMismatch,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Message,
    Modality,
    Provider,
)
from agenticcarekit.kernel.providers import (
    CerebrasProvider,
    FallbackChain,
    MockProvider,
    OllamaProvider,
)

TEXT_ONLY = Capabilities(
    modalities_in=frozenset({Modality.TEXT}),
    modalities_out=frozenset({Modality.TEXT}),
    tool_calling=True,
    streaming=True,
    context_tokens=8192,
    thinking=False,
    egress=EgressClass.PUBLIC_CLOUD,
)


class Failing(MockProvider):
    """A primary that always fails, with whatever exception you hand it."""

    name = "failing-primary"

    def __init__(self, exc: Exception, **kw):
        super().__init__(**kw)
        self.exc = exc

    def generate(self, req):
        self.check(req)
        raise self.exc

    def stream(self, req):
        self.check(req)
        raise self.exc
        yield  # pragma: no cover


def text_request() -> GenerateRequest:
    return GenerateRequest(messages=(Message.text("user", "summarise the encounter"),))


# ── acceptance (d) ───────────────────────────────────────────────────────


def test_primary_timeout_falls_back_and_returns_the_response():
    primary = Failing(httpx.ReadTimeout("primary timed out"))
    fallback = MockProvider([GenerateResponse(text="answered by the fallback")])
    chain = FallbackChain(primary, fallback)
    assert chain.generate(text_request()).text == "answered by the fallback"
    assert len(fallback.requests) == 1


def test_timeouterror_and_ackerror_also_trigger_the_fallback():
    fallback = MockProvider([GenerateResponse(text="ok")])
    for exc in (TimeoutError("slow"), AckError("boom", code="E102"), httpx.ConnectError("refused")):
        chain = FallbackChain(Failing(exc), fallback)
        assert chain.generate(text_request()).text == "ok"


def test_healthy_primary_is_never_bypassed():
    primary = MockProvider([GenerateResponse(text="from primary")])
    fallback = MockProvider([GenerateResponse(text="from fallback")])
    chain = FallbackChain(primary, fallback)
    assert chain.generate(text_request()).text == "from primary"
    assert fallback.requests == []


def test_fallback_decision_is_recorded_as_error_then_model():
    events = []
    chain = FallbackChain(
        Failing(httpx.ReadTimeout("primary timed out")),
        MockProvider([GenerateResponse(text="ok")]),
        emit=events.append,
    )
    chain.generate(text_request())
    assert [e.kind for e in events] == ["error", "model"]
    assert events[0].payload["provider"] == "failing-primary"
    assert "ReadTimeout" in events[0].payload["error"]
    assert events[1].payload["provider"] == "mock"
    assert "failing-primary" in events[1].payload["reason"]
    assert all(e.bytes_out == 0 for e in events)
    assert events[0].run_id == events[1].run_id


def test_trace_events_serialize_to_the_contract_shape():
    events = []
    chain = FallbackChain(
        Failing(TimeoutError("slow")), MockProvider(), emit=events.append
    )
    chain.generate(text_request())
    import json

    parsed = json.loads(events[0].to_json())
    assert set(parsed) == {
        "ts",
        "run_id",
        "span_id",
        "parent_span_id",
        "kind",
        "egress",
        "bytes_out",
        "payload",
    }


# ── the boundary: no degrading ───────────────────────────────────────────


def test_request_must_pass_the_primary_pre_check_before_any_fallback():
    """A capability gap on the primary is a config error, not a transient one."""
    primary = OllamaProvider("gemma4:31b", client=object())  # text+image only
    fallback = MockProvider([GenerateResponse(text="would have answered")])
    chain = FallbackChain(primary, fallback)
    audio = GenerateRequest(messages=(Message("user", (AudioPart(b"RIFF"),)),))
    with pytest.raises(CapabilityMismatch) as exc:
        chain.generate(audio)
    assert exc.value.code == "E203"
    assert fallback.requests == []  # the fallback was never consulted


def test_incapable_fallback_raises_rather_than_degrading():
    primary = Failing(httpx.ReadTimeout("down"))  # full device capabilities
    fallback = MockProvider(capabilities=TEXT_ONLY)
    chain = FallbackChain(primary, fallback)
    audio = GenerateRequest(messages=(Message("user", (AudioPart(b"RIFF"),)),))
    with pytest.raises(CapabilityMismatch) as exc:
        chain.generate(audio)
    assert exc.value.code == "E203"
    assert exc.value.missing == ["audio input"]
    assert "audio input" in exc.value.message
    assert fallback.requests == []


def test_the_error_event_is_still_emitted_when_the_fallback_is_refused():
    events = []
    chain = FallbackChain(
        Failing(httpx.ReadTimeout("down")),
        MockProvider(capabilities=TEXT_ONLY),
        emit=events.append,
    )
    with pytest.raises(CapabilityMismatch):
        chain.generate(GenerateRequest(messages=(Message("user", (AudioPart(b"x"),)),)))
    assert [e.kind for e in events] == ["error"]  # no "model" event: nothing ran


# ── honest capability arithmetic ─────────────────────────────────────────


def test_capabilities_are_the_intersection_with_the_broadest_egress():
    chain = FallbackChain(MockProvider(), CerebrasProvider("gemma-4-31b"))
    caps = chain.capabilities()
    assert caps.modalities_in == frozenset({Modality.TEXT})  # cloud declares text only
    assert caps.context_tokens == 131_072  # the smaller of the two
    assert caps.egress == EgressClass.PUBLIC_CLOUD  # what may actually happen


def test_a_device_only_chain_stays_device():
    chain = FallbackChain(MockProvider(), MockProvider())
    assert chain.capabilities().egress == EgressClass.DEVICE


def test_chain_satisfies_the_provider_protocol_and_exposes_the_primary_client():
    client = httpx.Client()
    primary = OllamaProvider("gemma4:e4b", client=client)
    chain = FallbackChain(primary, MockProvider())
    assert isinstance(chain, Provider)
    assert chain.client is client
    assert chain.name == "ollama->mock"


# ── streaming ────────────────────────────────────────────────────────────


def test_stream_falls_back_when_the_primary_fails_before_emitting():
    chain = FallbackChain(
        Failing(httpx.ReadTimeout("down")), MockProvider([GenerateResponse(text="fallback text")])
    )
    chunks = list(chain.stream(text_request()))
    assert chunks[-1].response.text == "fallback text"


def test_stream_does_not_splice_two_completions_after_a_mid_stream_failure():
    class HalfStream(MockProvider):
        name = "half"

        def stream(self, req):
            from agenticcarekit.kernel.contracts import Chunk

            yield Chunk(delta="partial ")
            raise httpx.ReadTimeout("died mid-stream")

    chain = FallbackChain(HalfStream(), MockProvider([GenerateResponse(text="whole answer")]))
    with pytest.raises(httpx.ReadTimeout):
        list(chain.stream(text_request()))


def test_healthy_primary_stream_is_passed_through():
    chain = FallbackChain(
        MockProvider([GenerateResponse(text="primary stream")]),
        MockProvider([GenerateResponse(text="fallback stream")]),
    )
    chunks = list(chain.stream(text_request()))
    assert chunks[-1].response.text == "primary stream"
