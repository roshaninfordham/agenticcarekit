"""Twilio Media Streams <-> the same `Iterator[bytes]` interface a local
mic produces.

No network code lives here. This module only transforms frame *dicts* your
websocket handler has already decoded (e.g. via `websockets`'
`json.loads(message)` or FastAPI's `WebSocket.receive_json()`) into raw
mu-law audio bytes for `loop.VoiceLoop.run_turn`, and raw reply audio back
into the frame dicts Twilio expects to send back over that same socket.
Nothing here opens a connection, and nothing here needs to for
`VoiceLoop` to treat a phone call exactly like a local microphone.

Twilio's Media Streams wire format (for reference — see
`tests/test_voice_twilio.py` for a recorded-style fixture):

    {"event": "connected", "protocol": "Call", "version": "1.0.0"}
    {"event": "start", "streamSid": "MZ...", "start": {...}}
    {"event": "media", "streamSid": "MZ...",
     "media": {"track": "inbound", "chunk": "1", "timestamp": "20",
               "payload": "<base64 mu-law audio>"}}
    {"event": "stop", "streamSid": "MZ..."}
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Iterator
from typing import Any

__all__ = ["TwilioMediaStreamAdapter"]


class TwilioMediaStreamAdapter:
    """Converts Twilio Media Stream frame dicts to/from raw audio bytes.

    `frames_to_audio` is the ASR-facing side: an `Iterator[bytes]` of
    decoded mu-law payloads, same shape `mic.MicAdapter.stream()` and
    `mock.MockASR.transcribe_stream` both consume. `audio_to_frames` is the
    TTS-facing side: reply audio chunked into outbound `"media"` frames.

    Example:
        >>> import base64
        >>> frames = [
        ...     {"event": "start", "streamSid": "MZ123"},
        ...     {"event": "media", "streamSid": "MZ123",
        ...      "media": {"payload": base64.b64encode(b"abc").decode()}},
        ...     {"event": "stop", "streamSid": "MZ123"},
        ... ]
        >>> adapter = TwilioMediaStreamAdapter(stream_sid="MZ123")
        >>> list(adapter.frames_to_audio(frames))
        [b'abc']
    """

    def __init__(self, *, stream_sid: str | None = None) -> None:
        self.stream_sid = stream_sid

    def frames_to_audio(self, frames: Iterable[dict[str, Any]]) -> Iterator[bytes]:
        """Yield decoded mu-law audio bytes for each ``"media"`` frame,
        stopping at the first ``"stop"`` frame. ``"connected"``, ``"start"``,
        and ``"mark"`` frames carry no audio and are ignored.
        """
        for frame in frames:
            event = frame.get("event")
            if event == "media":
                payload = frame["media"]["payload"]
                yield base64.b64decode(payload)
            elif event == "stop":
                return

    def audio_to_frame(self, audio: bytes) -> dict[str, Any]:
        """Wrap one chunk of reply audio as a single outbound Twilio
        ``"media"`` frame."""
        return {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": base64.b64encode(audio).decode("ascii")},
        }

    def audio_to_frames(self, audio: bytes, *, chunk_size: int = 320) -> Iterator[dict[str, Any]]:
        """Chunk reply audio into a sequence of outbound Twilio frames —
        Twilio expects a stream of small media frames, not one giant
        payload. Rebuilding the audio is `b"".join` over each frame's
        decoded ``payload`` in order.
        """
        for i in range(0, len(audio), chunk_size):
            yield self.audio_to_frame(audio[i : i + chunk_size])

    @staticmethod
    def parse_frame(raw: str | bytes) -> dict[str, Any]:
        """Parse one raw websocket text frame (JSON) into a frame dict.

        The only "wire-shaped" thing this module offers — it never touches
        a socket itself, only the string/bytes your handler already read
        off one.

        Example:
            >>> TwilioMediaStreamAdapter.parse_frame('{"event": "stop"}')
            {'event': 'stop'}
        """
        return json.loads(raw)
