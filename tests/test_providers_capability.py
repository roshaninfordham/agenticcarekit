"""W-A · capability negotiation — the error happens before the socket.

Acceptance (a): a request carrying audio, against a text+image model, raises
a typed error naming the audio-capable tags **before any network call**. The
proof is a client stub that fails the test if it is touched at all.
"""

from __future__ import annotations

import pytest
from agenticcarekit.kernel.contracts import (
    AudioPart,
    CapabilityMismatch,
    EgressClass,
    GenerateRequest,
    ImagePart,
    Message,
    Modality,
    explain,
    tool,
)
from agenticcarekit.kernel.providers import (
    GEMMA4_MODELS,
    MODEL_SIZES_GB,
    MockProvider,
    OllamaProvider,
    audio_capable_tags,
)


class ExplodingClient:
    """Any network use is a test failure, not a mock response."""

    def post(self, *a, **kw):  # pragma: no cover - must never run
        raise AssertionError("network call attempted before the capability check")

    def stream(self, *a, **kw):  # pragma: no cover - must never run
        raise AssertionError("network call attempted before the capability check")

    def request(self, *a, **kw):  # pragma: no cover - must never run
        raise AssertionError("network call attempted before the capability check")

    def send(self, *a, **kw):  # pragma: no cover - must never run
        raise AssertionError("network call attempted before the capability check")


def audio_request() -> GenerateRequest:
    return GenerateRequest(
        messages=(
            Message.text("system", "You are an intake assistant."),
            Message(
                role="user",
                parts=(AudioPart(b"RIFF0000WAVE", format="wav"),),
            ),
        )
    )


# ── acceptance (a) ───────────────────────────────────────────────────────


def test_audio_against_text_only_model_raises_before_any_network_call():
    provider = OllamaProvider("gemma4:31b", client=ExplodingClient())
    with pytest.raises(CapabilityMismatch) as exc:
        provider.generate(audio_request())

    err = exc.value
    assert err.code == "E203"
    assert "gemma4:31b" in err.message
    assert "audio input" in err.message
    assert err.missing == ["audio input"]
    assert err.candidates == ["gemma4:e2b", "gemma4:e2b-mlx", "gemma4:e4b", "gemma4:e4b-mlx"]
    assert err.candidates == audio_capable_tags()
    assert err.fix == "ack init --model gemma4:e4b-mlx"


def test_the_same_check_guards_streaming():
    provider = OllamaProvider("gemma4:31b", client=ExplodingClient())
    with pytest.raises(CapabilityMismatch):
        list(provider.stream(audio_request()))


def test_check_can_be_called_at_startup_without_generating():
    with pytest.raises(CapabilityMismatch):
        OllamaProvider("gemma4:26b", client=ExplodingClient()).check(audio_request())


def test_error_renders_the_cli_shape_with_a_literal_fix():
    provider = OllamaProvider("gemma4:31b", client=ExplodingClient())
    with pytest.raises(CapabilityMismatch) as exc:
        provider.generate(audio_request())
    rendered = exc.value.render()
    assert "E203" in rendered
    assert "gemma4:e4b" in rendered  # the models that would work
    assert "ack init --model gemma4:e4b-mlx" in rendered


def test_e203_is_registered_in_the_shared_error_registry():
    entry = explain("E203")
    assert entry is not None
    assert entry.code == "E203"


def test_audio_capable_model_passes_the_check():
    provider = OllamaProvider("gemma4:e4b-mlx", client=ExplodingClient())
    assert provider.check(audio_request()) is None  # no raise, no network


def test_tool_calling_gap_is_e202_not_e203():
    def _mock() -> str:
        return "x"

    @tool(mock=_mock)
    def ping() -> str:
        """Ping."""
        return "pong"

    from agenticcarekit.kernel.contracts import Capabilities

    no_tools = Capabilities(
        modalities_in=frozenset({Modality.TEXT}),
        modalities_out=frozenset({Modality.TEXT}),
        tool_calling=False,
        streaming=True,
        context_tokens=8192,
        thinking=False,
        egress=EgressClass.DEVICE,
    )
    req = GenerateRequest(messages=(Message.text("user", "hi"),), tools=(ping,))
    with pytest.raises(CapabilityMismatch) as exc:
        MockProvider(capabilities=no_tools).generate(req)
    assert exc.value.code == "E202"
    assert exc.value.missing == ["tool calling"]


def test_image_against_a_text_only_declaration_is_also_e203():
    provider = OllamaProvider("some-unknown-tag:latest", client=ExplodingClient())
    req = GenerateRequest(messages=(Message("user", (ImagePart(b"x"),)),))
    with pytest.raises(CapabilityMismatch) as exc:
        provider.generate(req)
    assert exc.value.code == "E203"
    assert exc.value.missing == ["image input"]


# ── the model table itself (brief §2 ground truth) ───────────────────────


@pytest.mark.parametrize("tag", ["gemma4:e2b", "gemma4:e4b", "gemma4:e2b-mlx", "gemma4:e4b-mlx"])
def test_small_tags_take_text_image_and_audio_at_128k(tag):
    caps = GEMMA4_MODELS[tag]
    assert caps.modalities_in == frozenset({Modality.TEXT, Modality.IMAGE, Modality.AUDIO})
    assert caps.context_tokens == 131_072


@pytest.mark.parametrize("tag", ["gemma4:12b", "gemma4:26b", "gemma4:31b"])
def test_large_tags_take_text_and_image_at_256k(tag):
    caps = GEMMA4_MODELS[tag]
    assert caps.modalities_in == frozenset({Modality.TEXT, Modality.IMAGE})
    assert caps.context_tokens == 262_144


def test_mlx_variants_mirror_their_base_tag():
    assert GEMMA4_MODELS["gemma4:e4b-mlx"] == GEMMA4_MODELS["gemma4:e4b"]
    assert GEMMA4_MODELS["gemma4:e2b-mlx"] == GEMMA4_MODELS["gemma4:e2b"]


def test_every_tag_is_text_only_out_with_tools_streaming_and_thinking():
    for tag, caps in GEMMA4_MODELS.items():
        assert caps.modalities_out == frozenset({Modality.TEXT}), tag
        assert caps.tool_calling and caps.streaming and caps.thinking, tag


def test_local_tags_are_device_and_cloud_tags_are_public_cloud():
    for tag, caps in GEMMA4_MODELS.items():
        hosted = tag.endswith("cloud")  # gemma4:cloud and gemma4:31b-cloud
        expected = EgressClass.PUBLIC_CLOUD if hosted else EgressClass.DEVICE
        assert caps.egress == expected, tag
    assert GEMMA4_MODELS["gemma4:cloud"].egress == EgressClass.PUBLIC_CLOUD


def test_model_sizes_match_the_published_table():
    assert MODEL_SIZES_GB["gemma4:e2b"] == 7.2
    assert MODEL_SIZES_GB["gemma4:e4b"] == 9.6
    assert MODEL_SIZES_GB["gemma4:12b"] == 7.6
    assert MODEL_SIZES_GB["gemma4:26b"] == 18.0
    assert MODEL_SIZES_GB["gemma4:31b"] == 20.0


def test_hosted_tags_have_no_download_size():
    assert "gemma4:cloud" not in MODEL_SIZES_GB
    assert "gemma4:31b-cloud" not in MODEL_SIZES_GB


def test_expected_tag_set_is_exactly_the_documented_one():
    assert set(GEMMA4_MODELS) == {
        "gemma4:e2b",
        "gemma4:e4b",
        "gemma4:e2b-mlx",
        "gemma4:e4b-mlx",
        "gemma4:12b",
        "gemma4:26b",
        "gemma4:31b",
        "gemma4:cloud",
        "gemma4:31b-cloud",
    }
