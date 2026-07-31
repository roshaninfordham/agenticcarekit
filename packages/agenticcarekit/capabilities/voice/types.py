"""ASR/TTS provider protocols for the voice capability.

Gemma 4 outputs **text only** on every variant — e2b, e4b, 12b, 26b, 31b all
lack native speech output (docs/brief.md §2). Audio *input* is native on
e2b/e4b only, and even there the model still only ever emits text back.
Consequently text-to-speech is *always* a separate provider from the LLM
`Provider` (`agenticcarekit.kernel.contracts.Provider`) that produces the
reply text — there is no "does this model also speak" question to ask.

The type system makes that impossible to confuse: `TTSProvider` and the
kernel's `Provider` are unrelated protocols with disjoint method shapes.
Nothing can be typed as "the LLM that also speaks", because no such type
exists here. Wiring an LLM's `generate`/`stream` output into
`VoiceLoop.run_turn`'s TTS step would fail structurally (wrong protocol,
wrong methods), not silently degrade.

ASR (speech-to-text) is likewise its own protocol, independent of whether
the eventual LLM call also receives raw audio directly — `ASRProvider`
turns a live audio stream into text transcripts on its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["ASRProvider", "TTSProvider", "Transcript"]


@dataclass(frozen=True)
class Transcript:
    """One ASR result increment for a spoken utterance.

    ``is_final=False`` marks a partial that may still change as more audio
    arrives; ``is_final=True`` marks the settled transcript. ``start_ms``/
    ``end_ms`` are offsets (milliseconds) into the audio stream this
    transcript covers.

    Example:
        >>> partial = Transcript(text="where is", is_final=False, start_ms=0, end_ms=200)
        >>> final = Transcript(text="where is the pharmacy", is_final=True, start_ms=0, end_ms=400)
        >>> partial.is_final, final.is_final
        (False, True)
    """

    text: str
    is_final: bool
    start_ms: int
    end_ms: int


@runtime_checkable
class ASRProvider(Protocol):
    """Speech-to-text. Streams partial and final transcripts from raw audio.

    ``transcribe_stream`` consumes an `Iterator[bytes]` of raw audio chunks
    — the same shape a local mic (`mic.MicAdapter`) and a Twilio Media
    Stream (`twilio_adapter.TwilioMediaStreamAdapter`) both produce — and
    yields `Transcript` objects as recognition progresses.
    """

    name: str

    def transcribe_stream(self, audio_chunks: Iterator[bytes]) -> Iterator[Transcript]: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech. Always a separate provider from the LLM `Provider` —
    see the module docstring for why that split is structural, not a
    convention."""

    name: str

    def synthesize(self, text: str) -> bytes: ...
