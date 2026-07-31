"""The voice turn loop: ASR -> LLM -> TTS, with partial transcripts and
barge-in.

Imports only from `agenticcarekit.kernel.contracts` — `llm` need only
satisfy the kernel `Provider` protocol, `emit` is an optional
`Callable[[TraceEvent], None]` hook. No provider, policy, or trace package
is imported directly, so this module works identically against a real
provider or the offline stubs in `mock.py`/tests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from agenticcarekit.kernel.contracts import (
    GenerateRequest,
    GenerateResponse,
    Message,
    Provider,
    TextPart,
    TraceEvent,
)

from .bargein import is_interrupted
from .types import ASRProvider, Transcript, TTSProvider

__all__ = ["DEFAULT_SYSTEM_PROMPT_PATH", "TurnResult", "VoiceLoop"]

#: Ships with the package. Decision-support-only framing — see the file
#: itself. Prompts are `.md` files, never string literals (project
#: convention), so this default is loaded from disk like any custom one.
DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "assistant.md"


@dataclass(frozen=True)
class TurnResult:
    """The outcome of one `VoiceLoop.run_turn` call.

    ``transcript`` is the best transcript obtained for this turn: the final
    one when ASR settled, otherwise the last partial seen before the turn
    ended early (barge-in, or the audio stream simply ran out). It is
    `None` only if no audio was recognized at all.

    ``reply_audio`` is `b""` whenever synthesis did not happen or was cut
    off by barge-in — never partially-decodable audio.
    """

    transcript: Transcript | None
    partials: tuple[Transcript, ...]
    reply_text: str
    reply_audio: bytes
    interrupted: bool


class VoiceLoop:
    """Runs one spoken conversation turn end to end: ASR -> LLM -> TTS.

    Keeps multi-turn history as `agenticcarekit.kernel.contracts.Message`
    objects. Assistant `thinking` is never written back into `parts` —
    turns are built with `Message.text(...)`, which never populates
    `thinking`, so there is nothing to strip later (quirk 3 stays
    structural rather than relying on someone remembering to strip it).

    Example:
        >>> from agenticcarekit.capabilities.voice import MockASR, MockTTS, Transcript
        >>> from agenticcarekit.kernel.contracts import (
        ...     Capabilities, EgressClass, GenerateResponse, Modality)
        >>> class EchoLLM:
        ...     name = "echo"
        ...     def capabilities(self):
        ...         return Capabilities(
        ...             modalities_in=frozenset({Modality.TEXT}),
        ...             modalities_out=frozenset({Modality.TEXT}),
        ...             tool_calling=False, streaming=False,
        ...             context_tokens=8192, thinking=False,
        ...             egress=EgressClass.DEVICE)
        ...     def generate(self, req):
        ...         return GenerateResponse(text="ok", model="echo")
        ...     def stream(self, req):
        ...         raise NotImplementedError
        >>> asr = MockASR([[Transcript("hello", True, 0, 100)]])
        >>> loop = VoiceLoop(asr=asr, llm=EchoLLM(), tts=MockTTS())
        >>> result = loop.run_turn(iter([b""]))
        >>> result.reply_text
        'ok'
        >>> result.interrupted
        False
        >>> loop.history[-1].role
        'assistant'
    """

    def __init__(
        self,
        asr: ASRProvider,
        llm: Provider,
        tts: TTSProvider,
        *,
        system_prompt_path: Path | None = None,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> None:
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.system_prompt_path = system_prompt_path or DEFAULT_SYSTEM_PROMPT_PATH
        self.emit = emit
        self._system_prompt = self.system_prompt_path.read_text(encoding="utf-8")
        self._history: list[Message] = []
        self._run_id = uuid.uuid4().hex

    @property
    def history(self) -> tuple[Message, ...]:
        """Read-only view of the accumulated conversation history."""
        return tuple(self._history)

    def run_turn(
        self,
        audio_chunks: Iterator[bytes],
        *,
        on_partial: Callable[[Transcript], None] | None = None,
        interrupt: Event | None = None,
    ) -> TurnResult:
        """Consume one utterance's audio, reply, and speak the reply.

        Checkpoints `interrupt` (if given) between ASR partials, before
        calling the LLM, and before/after TTS synthesis. As soon as it is
        set, the turn stops cleanly: `TurnResult.interrupted=True`, and
        whatever transcript was gathered so far is still appended to
        `history` — barge-in must never lose what the caller already said.
        """
        partials: list[Transcript] = []
        final: Transcript | None = None

        for t in self.asr.transcribe_stream(audio_chunks):
            if t.is_final:
                final = t
                break
            partials.append(t)
            if on_partial is not None:
                on_partial(t)
            if is_interrupted(interrupt):
                break

        transcript = final if final is not None else (partials[-1] if partials else None)
        if transcript is not None and transcript.text:
            self._history.append(Message.text("user", transcript.text))

        if final is None:
            # Either barge-in cut listening short, or the audio simply
            # ended without a settled transcript. Either way there is no
            # final transcript to hand the LLM.
            return TurnResult(
                transcript=transcript,
                partials=tuple(partials),
                reply_text="",
                reply_audio=b"",
                interrupted=is_interrupted(interrupt),
            )

        if is_interrupted(interrupt):
            return TurnResult(
                transcript=transcript,
                partials=tuple(partials),
                reply_text="",
                reply_audio=b"",
                interrupted=True,
            )

        request = self._build_request()
        started = time.monotonic()
        response = self.llm.generate(request)
        self._emit_model_event(response, duration_ms=(time.monotonic() - started) * 1000)

        if is_interrupted(interrupt):
            # The reply text exists and is worth keeping in history for
            # context on the next turn, but nothing gets spoken.
            self._history.append(Message.text("assistant", response.text))
            return TurnResult(
                transcript=transcript,
                partials=tuple(partials),
                reply_text=response.text,
                reply_audio=b"",
                interrupted=True,
            )

        reply_audio = self.tts.synthesize(response.text)
        interrupted = is_interrupted(interrupt)
        self._history.append(Message.text("assistant", response.text))

        return TurnResult(
            transcript=transcript,
            partials=tuple(partials),
            reply_text=response.text,
            reply_audio=b"" if interrupted else reply_audio,
            interrupted=interrupted,
        )

    def _build_request(self) -> GenerateRequest:
        system = Message(role="system", parts=(TextPart(self._system_prompt),))
        return GenerateRequest(messages=(system, *self._history))

    def _emit_model_event(self, response: GenerateResponse, *, duration_ms: float) -> None:
        if self.emit is None:
            return
        caps = self.llm.capabilities()
        event = TraceEvent(
            ts=time.time(),
            run_id=self._run_id,
            span_id=uuid.uuid4().hex,
            parent_span_id=None,
            kind="model",
            egress=caps.egress,
            bytes_out=0,  # network egress accounting is the provider's job (W-A)
            payload={
                "model": response.model,
                "provider": getattr(self.llm, "name", ""),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "duration_ms": duration_ms,
            },
        )
        self.emit(event)
