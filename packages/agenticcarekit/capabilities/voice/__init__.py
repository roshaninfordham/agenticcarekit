"""Voice capability (W-D): ASR/TTS abstraction, the turn loop, barge-in,
and adapters (local mic, Twilio Media Streams) behind one shared
`Iterator[bytes]` interface.

Gemma 4 has no native speech output on any variant — see `types` for why
`TTSProvider` is always a separate provider from the kernel's LLM
`Provider`. Everything here is offline-first: `mock.MockASR`/`mock.MockTTS`
make the full turn loop runnable with no network and no audio hardware.
"""

from .loop import TurnResult, VoiceLoop
from .mic import MicAdapter
from .mock import MockASR, MockTTS
from .twilio_adapter import TwilioMediaStreamAdapter
from .types import ASRProvider, Transcript, TTSProvider

__all__ = [
    "ASRProvider",
    "MicAdapter",
    "MockASR",
    "MockTTS",
    "Transcript",
    "TTSProvider",
    "TurnResult",
    "TwilioMediaStreamAdapter",
    "VoiceLoop",
]
