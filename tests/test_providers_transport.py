"""W-A · the wire: Ollama, OpenAI-compatible, Cerebras, and the mock.

Every test here runs against ``httpx.MockTransport`` — no socket is ever
opened, which is also what makes the suite honest about being offline-first
(invariant 5).
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from agenticcarekit.kernel.contracts import (
    AckError,
    AudioPart,
    Capabilities,
    GenerateRequest,
    GenerateResponse,
    ImagePart,
    Message,
    Provider,
    TextPart,
    ToolCall,
)
from agenticcarekit.kernel.providers import (
    CerebrasProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from agenticcarekit.kernel.providers.openai_compat import build_openai_chat

SECRET_THOUGHT = "internal deliberation that must never be replayed"


def recording_client(response_factory):
    """An httpx.Client that records every request body it was handed."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return response_factory(request)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


# ── Ollama ───────────────────────────────────────────────────────────────


def ollama_ok(_request):
    return httpx.Response(
        200,
        json={
            "model": "gemma4:e4b",
            "message": {
                "role": "assistant",
                "content": "Documented.",
                "thinking": "fresh thought",
                "tool_calls": [{"function": {"name": "lookup_code", "arguments": {"code": "R07.9"}}}],
            },
            "prompt_eval_count": 41,
            "eval_count": 7,
            "done": True,
        },
    )


def test_generate_posts_to_api_chat_and_parses_the_response():
    client, seen = recording_client(ollama_ok)
    provider = OllamaProvider("gemma4:e4b", client=client)
    resp = provider.generate(GenerateRequest(messages=(Message.text("user", "hi"),)))

    assert seen[0]["model"] == "gemma4:e4b"
    assert seen[0]["stream"] is False
    assert resp.text == "Documented."
    assert resp.thinking == "fresh thought"
    assert resp.tool_calls == (ToolCall(id="lookup_code", name="lookup_code", arguments={"code": "R07.9"}),)
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (41, 7)
    assert resp.raw["done"] is True  # unmodified provider payload survives


def test_the_raw_client_is_reachable():
    client, _ = recording_client(ollama_ok)
    assert OllamaProvider("gemma4:e4b", client=client).client is client


def test_default_host_is_loopback():
    assert OllamaProvider("gemma4:e4b")._url() == "http://127.0.0.1:11434/api/chat"


def test_history_thinking_is_absent_from_the_actual_request_body():
    """Acceptance (b), proven on the wire rather than in a dict."""
    client, seen = recording_client(ollama_ok)
    provider = OllamaProvider("gemma4:e4b", client=client)
    history = GenerateRequest(
        messages=(
            Message.text("user", "first"),
            Message("assistant", (TextPart("answer one"),), thinking=SECRET_THOUGHT),
            Message.text("user", "second"),
            Message("assistant", (TextPart("answer two"),), thinking=SECRET_THOUGHT),
            Message.text("user", "third"),
        )
    )
    provider.generate(history)
    body = json.dumps(seen[0])
    assert SECRET_THOUGHT not in body
    assert "answer one" in body and "answer two" in body


def test_stream_yields_deltas_then_an_assembled_response():
    lines = [
        json.dumps({"message": {"content": "Doc"}}),
        json.dumps({"message": {"content": "umented."}}),
        json.dumps({"message": {"content": ""}, "done": True, "eval_count": 2}),
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="\n".join(lines)))
    )
    provider = OllamaProvider("gemma4:e4b", client=client)
    chunks = list(provider.stream(GenerateRequest(messages=(Message.text("user", "?"),))))
    assert "".join(c.delta for c in chunks) == "Documented."
    assert chunks[-1].done is True
    assert chunks[-1].response.text == "Documented."


def test_stream_sets_stream_true_on_the_wire():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, text=json.dumps({"message": {"content": "x"}, "done": True}))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    list(OllamaProvider("gemma4:e4b", client=client).stream(
        GenerateRequest(messages=(Message.text("user", "?"),))
    ))
    assert seen[0]["stream"] is True


def test_connection_refused_becomes_e011_with_the_daemon_fix():
    def refuse(_request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(AckError) as exc:
        OllamaProvider("gemma4:e4b", client=client).generate(
            GenerateRequest(messages=(Message.text("user", "?"),))
        )
    assert exc.value.code == "E011"
    assert "ollama serve" in exc.value.fix


def test_http_500_becomes_e102():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="nope")))
    with pytest.raises(AckError) as exc:
        OllamaProvider("gemma4:e4b", client=client).generate(
            GenerateRequest(messages=(Message.text("user", "?"),))
        )
    assert exc.value.code == "E102"


# ── OpenAI-compatible ────────────────────────────────────────────────────


def openai_ok(_request):
    return httpx.Response(
        200,
        json={
            "model": "gemma-4-31b",
            "choices": [
                {
                    "message": {
                        "content": "Summary.",
                        "tool_calls": [
                            {
                                "id": "call_7",
                                "type": "function",
                                "function": {"name": "lookup_code", "arguments": '{"code": "R07.9"}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
    )


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "sk-test-not-real")


def test_openai_mapping_keeps_the_quirks_media_first_and_thinking_stripped():
    req = GenerateRequest(
        messages=(
            Message.text("system", "You are a scribe."),
            Message("assistant", (TextPart("earlier"),), thinking=SECRET_THOUGHT),
            Message("user", (TextPart("describe"), ImagePart(b"png"), AudioPart(b"wav"))),
        ),
        think=True,
        temperature=0.2,
        max_tokens=64,
    )
    body = build_openai_chat(req, "gemma-4-31b")
    assert body["messages"][0]["content"] == "<|think|>You are a scribe."
    assert SECRET_THOUGHT not in json.dumps(body)
    types = [p["type"] for p in body["messages"][2]["content"]]
    assert types == ["image_url", "input_audio", "text"]  # media before text
    assert body["temperature"] == 0.2 and body["top_p"] == 0.95
    assert body["max_tokens"] == 64
    # top_k has no OpenAI field — stated, not smuggled.
    assert "top_k" not in body


def test_openai_tool_calls_round_trip_with_ids():
    req = GenerateRequest(
        messages=(
            Message(
                "assistant",
                (TextPart(""),),
                tool_calls=(ToolCall(id="call_7", name="lookup_code", arguments={"code": "R07.9"}),),
            ),
            Message("tool", (TextPart("R07.9"),), tool_call_id="call_7"),
        )
    )
    body = build_openai_chat(req, "gemma-4-31b")
    assert body["messages"][0]["tool_calls"][0]["id"] == "call_7"
    assert body["messages"][1]["tool_call_id"] == "call_7"


def test_openai_generate_parses_tool_calls_and_usage():
    client, seen = recording_client(openai_ok)
    provider = OpenAICompatibleProvider(
        "gemma-4-31b", "https://api.example.invalid/v1", "CEREBRAS_API_KEY", client=client
    )
    resp = provider.generate(GenerateRequest(messages=(Message.text("user", "hi"),)))
    assert resp.text == "Summary."
    assert resp.tool_calls[0].arguments == {"code": "R07.9"}
    assert resp.usage.input_tokens == 12
    assert seen[0]["model"] == "gemma-4-31b"


def test_api_key_is_read_at_call_time_and_never_stored():
    client, _ = recording_client(openai_ok)
    provider = OpenAICompatibleProvider(
        "gemma-4-31b", "https://api.example.invalid/v1", "CEREBRAS_API_KEY", client=client
    )
    assert "sk-test-not-real" not in repr(provider)
    assert "sk-test-not-real" not in json.dumps(vars(provider), default=str)


def test_missing_key_is_e120_naming_the_variable_not_a_value(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    client, _ = recording_client(openai_ok)
    provider = CerebrasProvider("gemma-4-31b", client=client)
    with pytest.raises(AckError) as exc:
        provider.generate(GenerateRequest(messages=(Message.text("user", "hi"),)))
    assert exc.value.code == "E120"
    assert "CEREBRAS_API_KEY" in exc.value.message
    assert "export CEREBRAS_API_KEY=" in exc.value.fix


def test_bearer_header_is_sent():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers["authorization"])
        return openai_ok(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    CerebrasProvider("gemma-4-31b", client=client).generate(
        GenerateRequest(messages=(Message.text("user", "hi"),))
    )
    assert captured == ["Bearer sk-test-not-real"]


def test_cerebras_is_a_preset_of_the_generic_provider():
    p = CerebrasProvider("gemma-4-31b")
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == "https://api.cerebras.ai/v1"
    assert p.api_key_env == "CEREBRAS_API_KEY"
    assert p.capabilities().egress.value == "public-cloud"


def test_openai_sse_stream_assembles_the_response():
    frames = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Sum"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "mary."}}]}),
        "data: [DONE]",
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="\n".join(frames)))
    )
    chunks = list(
        CerebrasProvider("gemma-4-31b", client=client).stream(
            GenerateRequest(messages=(Message.text("user", "?"),))
        )
    )
    assert "".join(c.delta for c in chunks) == "Summary."
    assert chunks[-1].response.text == "Summary."


# ── Mock provider ────────────────────────────────────────────────────────


def test_mock_cycles_responses_deterministically_and_records_requests():
    p = MockProvider([GenerateResponse(text="a"), GenerateResponse(text="b")])
    req = GenerateRequest(messages=(Message.text("user", "hi"),))
    assert [p.generate(req).text for _ in range(4)] == ["a", "b", "a", "b"]
    assert p.requests == [req, req, req, req]


def test_mock_defaults_to_full_device_capabilities():
    caps = MockProvider().capabilities()
    assert caps.egress.value == "device"
    assert {m.value for m in caps.modalities_in} == {"text", "image", "audio"}
    assert caps.tool_calling and caps.streaming and caps.thinking


def test_mock_accepts_audio_and_images_without_a_network():
    p = MockProvider()
    req = GenerateRequest(messages=(Message("user", (AudioPart(b"x"), ImagePart(b"y"))),))
    assert p.generate(req).text.startswith("This is a mock response")
    assert p.client is None


def test_mock_stream_reassembles_its_own_text():
    p = MockProvider([GenerateResponse(text="three word answer")])
    chunks = list(p.stream(GenerateRequest(messages=(Message.text("user", "?"),))))
    assert "".join(c.delta for c in chunks) == "three word answer"
    assert chunks[-1].response.text == "three word answer"


def test_no_environment_key_is_needed_for_the_offline_path():
    assert "OLLAMA_API_KEY" not in os.environ
    p = MockProvider()
    assert p.generate(GenerateRequest(messages=(Message.text("user", "hi"),))).text


# ── the protocol itself ──────────────────────────────────────────────────


def test_every_provider_satisfies_the_contract_and_exposes_a_client_attribute():
    providers = [
        OllamaProvider("gemma4:e4b"),
        CerebrasProvider("gemma-4-31b"),
        OpenAICompatibleProvider("m", "https://x/v1", "K"),
        MockProvider(),
    ]
    for p in providers:
        assert isinstance(p, Provider), p
        assert hasattr(p, "client")  # nothing hides the provider
        assert isinstance(p.capabilities(), Capabilities)
