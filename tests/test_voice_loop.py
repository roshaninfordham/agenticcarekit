"""End-to-end voice turn loop tests: MockASR -> stub LLM -> MockTTS, fully
offline (no network, no audio hardware).

The LLM stub lives here (not imported from kernel/providers) per the W-D
brief: voice imports only from `agenticcarekit.kernel.contracts` and
accepts any `Provider`-shaped object.
"""

from __future__ import annotations

from agenticcarekit.capabilities.voice import MockASR, MockTTS, Transcript, TurnResult, VoiceLoop
from agenticcarekit.kernel.contracts import (
    Capabilities,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Modality,
)


class StubLLM:
    """Minimal `Provider`-protocol stub: records every request it receives
    and returns a fixed reply."""

    name = "stub-llm"

    def __init__(self, reply_text: str = "Let's find that information for you.") -> None:
        self.reply_text = reply_text
        self.requests: list[GenerateRequest] = []

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities_in=frozenset({Modality.TEXT}),
            modalities_out=frozenset({Modality.TEXT}),
            tool_calling=False,
            streaming=False,
            context_tokens=8192,
            thinking=False,
            egress=EgressClass.DEVICE,
        )

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.requests.append(req)
        return GenerateResponse(text=self.reply_text, model="stub-model")

    def stream(self, req: GenerateRequest):
        raise NotImplementedError


def _pharmacy_script() -> list[list[Transcript]]:
    return [
        [
            Transcript("where", False, 0, 100),
            Transcript("where is", False, 0, 200),
            Transcript("where is the pharmacy", True, 0, 400),
        ]
    ]


def test_full_turn_end_to_end_no_network():
    asr = MockASR(_pharmacy_script())
    tts = MockTTS()
    llm = StubLLM()
    loop = VoiceLoop(asr=asr, llm=llm, tts=tts)

    seen_partials: list[Transcript] = []
    result = loop.run_turn(iter([b"\x00" * 10]), on_partial=seen_partials.append)

    assert isinstance(result, TurnResult)
    assert result.interrupted is False

    # partials surfaced in order via on_partial, and mirrored on the result
    assert [t.text for t in seen_partials] == ["where", "where is"]
    assert result.partials == tuple(seen_partials)

    # final transcript settled correctly
    assert result.transcript is not None
    assert result.transcript.text == "where is the pharmacy"
    assert result.transcript.is_final is True

    # the final transcript (and only the final transcript) reached the LLM
    assert len(llm.requests) == 1
    sent = llm.requests[0]
    assert sent.messages[-1].role == "user"
    assert sent.messages[-1].parts[0].text == "where is the pharmacy"

    # default system prompt is loaded from the shipped .md file and is
    # decision-support framed
    assert sent.messages[0].role == "system"
    system_text = sent.messages[0].parts[0].text
    assert "diagnos" in system_text.lower()

    # TTS bytes returned, deterministic and matching the reply text
    assert result.reply_text == llm.reply_text
    assert result.reply_audio == tts.synthesize(llm.reply_text)
    assert result.reply_audio.startswith(b"AUDIO:")


def test_history_accumulates_across_turns_without_thinking():
    asr = MockASR(
        [
            [Transcript("hi", True, 0, 100)],
            [Transcript("thanks", True, 0, 100)],
        ]
    )
    loop = VoiceLoop(asr=asr, llm=StubLLM(), tts=MockTTS())

    loop.run_turn(iter([b""]))
    loop.run_turn(iter([b""]))

    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "user", "assistant"]
    # never store assistant thinking back into history
    assert all(m.thinking is None for m in loop.history)


def test_run_turn_emits_a_model_trace_event():
    events = []
    loop = VoiceLoop(
        asr=MockASR([[Transcript("hello", True, 0, 100)]]),
        llm=StubLLM(),
        tts=MockTTS(),
        emit=events.append,
    )
    loop.run_turn(iter([b""]))

    assert len(events) == 1
    event = events[0]
    assert event.kind == "model"
    assert event.egress == EgressClass.DEVICE
    assert event.payload["provider"] == "stub-llm"


def test_custom_system_prompt_path(tmp_path):
    custom = tmp_path / "custom.md"
    custom.write_text("You are a custom test assistant.", encoding="utf-8")
    llm = StubLLM()
    loop = VoiceLoop(
        asr=MockASR([[Transcript("hi", True, 0, 100)]]),
        llm=llm,
        tts=MockTTS(),
        system_prompt_path=custom,
    )
    result = loop.run_turn(iter([b""]))
    assert result.interrupted is False
    assert llm.requests[0].messages[0].parts[0].text == "You are a custom test assistant."
