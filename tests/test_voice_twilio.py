"""Twilio Media Streams fixture -> `Iterator[bytes]` -> the same
`VoiceLoop` turn as a plain mic-shaped iterator.

`RECORDED_TWILIO_FRAMES` below is a recorded-style fixture: the JSON dicts
a websocket handler would already have decoded (e.g. `WebSocket.receive_json()`
in FastAPI, or `json.loads(message)` with the `websockets` library) for one
short inbound utterance. No socket, no network — this documents the exact
shape `TwilioMediaStreamAdapter` expects and produces.
"""

from __future__ import annotations

import base64

from agenticcarekit.capabilities.voice import (
    MockASR,
    MockTTS,
    Transcript,
    TwilioMediaStreamAdapter,
    VoiceLoop,
)
from agenticcarekit.kernel.contracts import Capabilities, EgressClass, GenerateResponse, Modality

STREAM_SID = "MZfake00000000000000000000000000"

RECORDED_TWILIO_FRAMES = [
    {"event": "connected", "protocol": "Call", "version": "1.0.0"},
    {
        "event": "start",
        "sequenceNumber": "1",
        "start": {
            "streamSid": STREAM_SID,
            "accountSid": "ACfake00000000000000000000000000",
            "callSid": "CAfake00000000000000000000000000",
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
        "streamSid": STREAM_SID,
    },
    {
        "event": "media",
        "sequenceNumber": "2",
        "media": {
            "track": "inbound",
            "chunk": "1",
            "timestamp": "20",
            "payload": base64.b64encode(b"\xff\xfe\xfd\xfc").decode("ascii"),
        },
        "streamSid": STREAM_SID,
    },
    {
        "event": "media",
        "sequenceNumber": "3",
        "media": {
            "track": "inbound",
            "chunk": "2",
            "timestamp": "40",
            "payload": base64.b64encode(b"\xfb\xfa\xf9\xf8").decode("ascii"),
        },
        "streamSid": STREAM_SID,
    },
    {"event": "stop", "sequenceNumber": "4", "streamSid": STREAM_SID},
]


class _StubLLM:
    name = "stub-llm"

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

    def generate(self, req):
        return GenerateResponse(text="reply", model="stub")

    def stream(self, req):
        raise NotImplementedError


def _script() -> list[list[Transcript]]:
    return [[Transcript("refill my prescription", True, 0, 500)]]


def test_frames_to_audio_yields_decoded_mulaw_payloads_and_stops_at_stop():
    adapter = TwilioMediaStreamAdapter(stream_sid=STREAM_SID)
    chunks = list(adapter.frames_to_audio(RECORDED_TWILIO_FRAMES))
    assert chunks == [b"\xff\xfe\xfd\xfc", b"\xfb\xfa\xf9\xf8"]


def test_twilio_path_runs_identically_to_mic_path():
    adapter = TwilioMediaStreamAdapter(stream_sid=STREAM_SID)
    twilio_audio = adapter.frames_to_audio(RECORDED_TWILIO_FRAMES)
    mic_shaped_audio = iter([b"\xff\xfe\xfd\xfc", b"\xfb\xfa\xf9\xf8"])

    loop_twilio = VoiceLoop(asr=MockASR(_script()), llm=_StubLLM(), tts=MockTTS())
    loop_mic = VoiceLoop(asr=MockASR(_script()), llm=_StubLLM(), tts=MockTTS())

    result_twilio = loop_twilio.run_turn(twilio_audio)
    result_mic = loop_mic.run_turn(mic_shaped_audio)

    assert result_twilio.transcript is not None and result_mic.transcript is not None
    assert result_twilio.transcript.text == result_mic.transcript.text
    assert result_twilio.reply_text == result_mic.reply_text
    assert result_twilio.reply_audio == result_mic.reply_audio
    assert result_twilio.interrupted is False
    assert result_mic.interrupted is False


def test_audio_to_frames_roundtrips_reply_bytes():
    adapter = TwilioMediaStreamAdapter(stream_sid=STREAM_SID)
    reply = b"synthesized-reply-audio-bytes-0123456789"
    frames = list(adapter.audio_to_frames(reply, chunk_size=8))

    assert all(f["event"] == "media" for f in frames)
    assert all(f["streamSid"] == STREAM_SID for f in frames)

    rebuilt = b"".join(base64.b64decode(f["media"]["payload"]) for f in frames)
    assert rebuilt == reply


def test_parse_frame_parses_raw_json_text():
    frame = TwilioMediaStreamAdapter.parse_frame('{"event": "stop", "streamSid": "MZ1"}')
    assert frame == {"event": "stop", "streamSid": "MZ1"}
