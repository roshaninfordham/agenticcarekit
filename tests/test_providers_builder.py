"""W-A · the canonical message builder — every quirk, asserted.

The golden test in here is the byte-for-byte contract from
``docs/CONTRACTS.md`` → "Canonical message build". If it changes, the
conformance suite (W-J) and the TypeScript port (W-L) change with it.
"""

from __future__ import annotations

import base64
import json

import pytest
from agenticcarekit.kernel.contracts import (
    AudioPart,
    GenerateRequest,
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    tool,
)
from agenticcarekit.kernel.providers import build_ollama_chat
from agenticcarekit.kernel.providers.builder import (
    THINK_TOKEN,
    encode_media,
    split_thinking,
)

PNG = b"\x89PNG\r\n\x1a\n"
PNG_B64 = "iVBORw0KGgo="
WAV = b"RIFF0000WAVEfmt "
WAV_B64 = "UklGRjAwMDBXQVZFZm10IA=="


def _mock_lookup(code: str) -> str:
    return "R51.9 Headache, unspecified"


@tool(permissions={"network"}, mock=_mock_lookup)
def lookup_code(code: str) -> str:
    """Look up an ICD-10 code."""
    return code


# ── rule 1: sampling ─────────────────────────────────────────────────────


def test_sampling_defaults_are_gemma4_known_good():
    payload = build_ollama_chat(GenerateRequest(messages=()), "gemma4:e4b")
    assert payload["options"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


def test_request_overrides_win_over_defaults():
    req = GenerateRequest(messages=(), temperature=0.0, top_p=0.5, top_k=1)
    opts = build_ollama_chat(req, "gemma4:e4b")["options"]
    assert (opts["temperature"], opts["top_p"], opts["top_k"]) == (0.0, 0.5, 1)


def test_max_tokens_and_stop_map_into_options_and_context_is_not_sent():
    req = GenerateRequest(messages=(), max_tokens=256, stop=("</done>",))
    opts = build_ollama_chat(req, "gemma4:e4b")["options"]
    assert opts["num_predict"] == 256
    assert opts["stop"] == ["</done>"]
    # The model declares its context window; the request never sends one.
    assert "num_ctx" not in opts and "context" not in opts


# ── rule 2: think token ──────────────────────────────────────────────────


def test_think_creates_a_system_message_when_none_exists():
    req = GenerateRequest(messages=(Message.text("user", "hi"),), think=True)
    messages = build_ollama_chat(req, "gemma4:e4b")["messages"]
    assert messages[0] == {"role": "system", "content": THINK_TOKEN}
    assert messages[1]["role"] == "user"


def test_think_prepends_once_to_an_existing_system_prompt():
    req = GenerateRequest(
        messages=(Message.text("system", "You are a scribe."), Message.text("user", "hi")),
        think=True,
    )
    first = build_ollama_chat(req, "gemma4:e4b")["messages"][0]
    assert first["content"] == "<|think|>You are a scribe."
    assert first["content"].count(THINK_TOKEN) == 1


def test_think_is_not_applied_twice_if_already_present():
    req = GenerateRequest(
        messages=(Message.text("system", "<|think|>already"),), think=True
    )
    content = build_ollama_chat(req, "gemma4:e4b")["messages"][0]["content"]
    assert content == "<|think|>already"


def test_no_think_token_when_think_is_false():
    req = GenerateRequest(messages=(Message.text("user", "hi"),))
    assert THINK_TOKEN not in json.dumps(build_ollama_chat(req, "gemma4:e4b"))


# ── rule 3: history hygiene (acceptance (b)) ─────────────────────────────

SECRETS = (
    "TURN1: the user may be describing chest pain, consider cardiac causes",
    "TURN2: vitals are normal, downgrade urgency",
    "TURN3: recommend documentation only, this is not a diagnosis",
)


def recorded_transcript() -> GenerateRequest:
    """A three-turn history where every assistant turn carries a thought block."""
    return GenerateRequest(
        messages=(
            Message.text("system", "You are a documentation assistant."),
            Message.text("user", "Patient reports tightness in the chest."),
            Message(
                role="assistant",
                parts=(TextPart("Noted. What are the vitals?"),),
                thinking=SECRETS[0],
            ),
            Message.text("user", "BP 118/76, HR 72, SpO2 99%."),
            Message(
                role="assistant",
                parts=(TextPart("Vitals are within normal range."),),
                thinking=SECRETS[1],
                tool_calls=(ToolCall(id="call_1", name="lookup_code", arguments={"code": "R07.9"}),),
            ),
            Message(
                role="tool",
                parts=(TextPart("R07.9 Chest pain, unspecified"),),
                tool_call_id="lookup_code",
            ),
            Message(
                role="assistant",
                parts=(TextPart("Documented as R07.9."),),
                thinking=SECRETS[2],
            ),
            Message.text("user", "Summarise the encounter."),
        ),
        think=True,
    )


def test_prior_turn_thought_blocks_never_reach_the_wire():
    payload = build_ollama_chat(recorded_transcript(), "gemma4:e4b")
    serialized = json.dumps(payload, sort_keys=True)
    for secret in SECRETS:
        assert secret not in serialized
    # Not a single message carries a `thinking` key either.
    assert all("thinking" not in m for m in payload["messages"])
    # …and the real content survived the stripping.
    assert "Vitals are within normal range." in serialized


def test_history_preserves_tool_calls_and_tool_results():
    payload = build_ollama_chat(recorded_transcript(), "gemma4:e4b")
    assistant = [m for m in payload["messages"] if m.get("tool_calls")][0]
    assert assistant["tool_calls"] == [
        {"function": {"name": "lookup_code", "arguments": {"code": "R07.9"}}}
    ]
    tool_turn = [m for m in payload["messages"] if m["role"] == "tool"][0]
    assert tool_turn["tool_name"] == "lookup_code"
    assert tool_turn["content"] == "R07.9 Chest pain, unspecified"


# ── rule 4: modality order and text joining ──────────────────────────────


def test_media_lands_in_its_own_arrays_and_text_joins_with_blank_line():
    msg = Message(
        role="user",
        parts=(
            TextPart("first"),
            ImagePart(PNG),
            AudioPart(WAV),
            TextPart("second"),
        ),
    )
    out = build_ollama_chat(GenerateRequest(messages=(msg,)), "gemma4:e4b")["messages"][0]
    assert out["content"] == "first\n\nsecond"
    assert out["images"] == [PNG_B64]
    assert out["audio"] == [WAV_B64]
    # images/audio precede content in the serialized turn — Ollama renders
    # them ahead of text (quirk 4).
    assert list(out) == ["role", "content", "images", "audio"]


# ── rule 5: vision token budgets ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("detail", "expected"),
    [("minimal", 70), ("caption", 140), ("default", 280), ("detail", 560), ("ocr", 1120)],
)
def test_each_vision_preset_maps_to_its_budget(detail, expected):
    req = GenerateRequest(messages=(Message("user", (ImagePart(PNG, detail=detail),)),))
    assert build_ollama_chat(req, "gemma4:e4b")["options"]["vision_tokens"] == expected


def test_highest_preset_wins_across_images():
    req = GenerateRequest(
        messages=(
            Message("user", (ImagePart(PNG, detail="caption"),)),
            Message("user", (ImagePart(PNG, detail="ocr"), ImagePart(PNG, detail="minimal"))),
        )
    )
    assert build_ollama_chat(req, "gemma4:e4b")["options"]["vision_tokens"] == 1120


def test_no_vision_tokens_without_images():
    req = GenerateRequest(messages=(Message.text("user", "text only"),))
    assert "vision_tokens" not in build_ollama_chat(req, "gemma4:e4b")["options"]


# ── rule 6: base64 handling ──────────────────────────────────────────────


def test_bytes_are_encoded_and_base64_strings_pass_through():
    assert encode_media(PNG) == PNG_B64
    assert encode_media(PNG_B64) == PNG_B64


def test_existing_file_path_is_read_and_encoded(tmp_path):
    p = tmp_path / "scan.png"
    p.write_bytes(PNG)
    assert encode_media(str(p)) == PNG_B64
    req = GenerateRequest(messages=(Message("user", (ImagePart(str(p)),)),))
    assert build_ollama_chat(req, "gemma4:e4b")["messages"][0]["images"] == [PNG_B64]


def test_long_non_path_string_does_not_explode_on_filename_limits():
    blob = base64.b64encode(b"x" * 8000).decode()
    assert encode_media(blob) == blob


# ── response-side thought separation ─────────────────────────────────────


def test_inline_think_block_is_split_out_of_response_text():
    assert split_thinking("<|think|>deliberating<|/think|>Final answer") == (
        "Final answer",
        "deliberating",
    )


def test_unterminated_think_block_still_leaves_clean_text():
    text, thought = split_thinking("prefix <|think|>ran out of tokens")
    assert text == "prefix"
    assert thought == "ran out of tokens"


# ── acceptance (c): the golden payload ───────────────────────────────────


def test_golden_full_featured_payload_is_byte_exact():
    """One request exercising every rule → one exact sorted-key JSON payload."""
    req = GenerateRequest(
        messages=(
            Message.text("system", "You are a clinical documentation assistant."),
            Message(
                role="user",
                parts=(
                    TextPart("Read the chart image."),
                    ImagePart(PNG, detail="ocr"),
                    AudioPart(WAV, format="wav"),
                    TextPart("Then summarise the dictation."),
                ),
            ),
        ),
        tools=(lookup_code,),
        think=True,
        temperature=0.25,
        max_tokens=512,
        stop=("</end>",),
    )
    payload = build_ollama_chat(req, "gemma4:e4b")
    expected = (
        '{"messages":[{"content":"<|think|>You are a clinical documentation assistant.",'
        '"role":"system"},{"audio":["UklGRjAwMDBXQVZFZm10IA=="],"content":"Read the chart '
        'image.\\n\\nThen summarise the dictation.","images":["iVBORw0KGgo="],"role":"user"}],'
        '"model":"gemma4:e4b","options":{"num_predict":512,"stop":["</end>"],"temperature":0.25,'
        '"top_k":64,"top_p":0.95,"vision_tokens":1120},"stream":false,'
        '"tools":[{"function":{"description":"Look up an ICD-10 code.","name":"lookup_code",'
        '"parameters":{"properties":{"code":{"type":"string"}},"required":["code"],'
        '"type":"object"}},"type":"function"}]}'
    )
    assert json.dumps(payload, sort_keys=True, separators=(",", ":")) == expected


def test_golden_payload_is_stable_across_builds():
    req = recorded_transcript()
    a = json.dumps(build_ollama_chat(req, "gemma4:e4b"), sort_keys=True)
    b = json.dumps(build_ollama_chat(req, "gemma4:e4b"), sort_keys=True)
    assert a == b
