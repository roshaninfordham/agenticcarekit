"""Fully offline ASR/TTS stand-ins for tests, `ack demo --offline`, and any
caller that wants a deterministic voice turn with no audio hardware, no
model, and no network.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence

from .types import Transcript

__all__ = ["MockASR", "MockTTS"]


class MockASR:
    """Replays a pre-scripted sequence of `Transcript` objects per turn,
    ignoring the actual audio bytes fed in — the audio stream is still
    fully consumed, exactly as a real streaming ASR client would drain it.

    One entry in `script` per call to `run_turn`/`transcribe_stream`; once
    the script is exhausted, further calls yield nothing (an empty turn).

    Example:
        >>> asr = MockASR([[Transcript("hi", False, 0, 100), Transcript("hi there", True, 0, 300)]])
        >>> [t.text for t in asr.transcribe_stream(iter([b"\\x00\\x01"]))]
        ['hi', 'hi there']
        >>> list(asr.transcribe_stream(iter([b""])))
        []
    """

    name = "mock-asr"

    def __init__(self, script: Sequence[Sequence[Transcript]]) -> None:
        self._script = [list(turn) for turn in script]
        self._next_turn = 0

    def transcribe_stream(self, audio_chunks: Iterator[bytes]) -> Iterator[Transcript]:
        for _ in audio_chunks:
            pass  # drain: a real ASR reads until the caller stops feeding audio
        if self._next_turn >= len(self._script):
            return
        turn = self._script[self._next_turn]
        self._next_turn += 1
        yield from turn


class MockTTS:
    """Deterministic, offline text-to-speech stand-in.

    Returns `b"AUDIO:" + <16 hex chars of sha256(text)>` — stable across
    calls and processes, so tests can assert on exact bytes without a real
    audio codec.

    Example:
        >>> tts = MockTTS()
        >>> audio = tts.synthesize("hello")
        >>> audio.startswith(b"AUDIO:")
        True
        >>> tts.synthesize("hello") == audio
        True
        >>> tts.synthesize("goodbye") == audio
        False
    """

    name = "mock-tts"

    def synthesize(self, text: str) -> bytes:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return b"AUDIO:" + digest.encode("ascii")
