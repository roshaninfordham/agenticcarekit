"""Barge-in: a `threading.Event` set mid-turn must stop `VoiceLoop.run_turn`
cleanly, mark `TurnResult.interrupted=True`, and keep whatever transcript
was gathered so far in history — fully offline (`MockASR`/`MockTTS`).
"""

from __future__ import annotations

import threading

from agenticcarekit.capabilities.voice import MockASR, MockTTS, Transcript, VoiceLoop
from agenticcarekit.kernel.contracts import Capabilities, EgressClass, GenerateResponse, Modality


def _caps() -> Capabilities:
    return Capabilities(
        modalities_in=frozenset({Modality.TEXT}),
        modalities_out=frozenset({Modality.TEXT}),
        tool_calling=False,
        streaming=False,
        context_tokens=8192,
        thinking=False,
        egress=EgressClass.DEVICE,
    )


class _CountingLLM:
    """Records how many times `generate` was called, so tests can assert
    barge-in stopped the loop *before* the LLM ran."""

    name = "counting-llm"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> Capabilities:
        return _caps()

    def generate(self, req):
        self.calls += 1
        return GenerateResponse(text="reply", model="stub")

    def stream(self, req):
        raise NotImplementedError


def test_interrupt_already_set_stops_before_llm_call():
    interrupt = threading.Event()
    interrupt.set()  # the caller is already talking over the assistant
    asr = MockASR([[Transcript("never mind", True, 0, 100)]])
    llm = _CountingLLM()
    loop = VoiceLoop(asr=asr, llm=llm, tts=MockTTS())

    result = loop.run_turn(iter([b""]), interrupt=interrupt)

    assert result.interrupted is True
    assert result.reply_text == ""
    assert result.reply_audio == b""
    assert llm.calls == 0

    # the transcript gathered before the interrupt is still in history
    assert loop.history[-1].role == "user"
    assert loop.history[-1].parts[0].text == "never mind"


def test_interrupt_during_generate_stops_before_synthesis():
    class TripwireTTS:
        name = "tripwire-tts"

        def __init__(self) -> None:
            self.called = False

        def synthesize(self, text: str) -> bytes:
            self.called = True
            return b"should-not-be-heard"

    class SetInterruptOnGenerate:
        """Simulates the caller starting to talk again the instant the
        reply lands, before it is spoken."""

        name = "set-on-generate-llm"

        def __init__(self, interrupt: threading.Event) -> None:
            self._interrupt = interrupt

        def capabilities(self) -> Capabilities:
            return _caps()

        def generate(self, req):
            self._interrupt.set()
            return GenerateResponse(text="here is your answer", model="stub")

        def stream(self, req):
            raise NotImplementedError

    interrupt = threading.Event()
    asr = MockASR([[Transcript("tell me about", True, 0, 100)]])
    tts = TripwireTTS()
    loop = VoiceLoop(asr=asr, llm=SetInterruptOnGenerate(interrupt), tts=tts)

    result = loop.run_turn(iter([b""]), interrupt=interrupt)

    assert result.interrupted is True
    assert tts.called is False, "synthesis must not run once barge-in fires"
    assert result.reply_audio == b""
    # the generated reply text is retained even though playback was cut
    assert result.reply_text == "here is your answer"
    assert loop.history[-1].role == "assistant"
    assert loop.history[-1].parts[0].text == "here is your answer"


def test_partial_transcript_kept_when_interrupted_mid_listening():
    interrupt = threading.Event()

    def on_partial(t: Transcript) -> None:
        if t.text == "where is":
            interrupt.set()

    asr = MockASR(
        [
            [
                Transcript("where", False, 0, 100),
                Transcript("where is", False, 0, 200),
                Transcript("where is the pharmacy", True, 0, 400),
            ]
        ]
    )
    llm = _CountingLLM()
    loop = VoiceLoop(asr=asr, llm=llm, tts=MockTTS())

    result = loop.run_turn(iter([b""]), on_partial=on_partial, interrupt=interrupt)

    assert result.interrupted is True
    assert result.transcript is not None
    assert result.transcript.text == "where is"
    assert result.transcript.is_final is False
    assert llm.calls == 0
    assert loop.history[-1].role == "user"
    assert loop.history[-1].parts[0].text == "where is"


def test_no_interrupt_passed_runs_normally():
    """Sanity check: omitting `interrupt=` entirely must not change
    behavior — barge-in is opt-in."""
    asr = MockASR([[Transcript("hello", True, 0, 100)]])
    llm = _CountingLLM()
    loop = VoiceLoop(asr=asr, llm=llm, tts=MockTTS())

    result = loop.run_turn(iter([b""]))

    assert result.interrupted is False
    assert llm.calls == 1
