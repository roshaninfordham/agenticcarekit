"""Local microphone adapter — the same `Iterator[bytes]` shape
`twilio_adapter.TwilioMediaStreamAdapter.frames_to_audio` produces, so
`loop.VoiceLoop.run_turn` cannot tell a phone call from a laptop mic apart.

``sounddevice`` is an optional dependency of the voice capability, not of
agenticcarekit core (it is deliberately **not** added to ``pyproject.toml``
by this workstream — see the W-D report). Nothing here imports it at
module load time; `MicAdapter.__init__` imports it lazily and raises a
helpful, actionable `AckError` instead of a bare `ImportError` traceback if
it is missing.
"""

from __future__ import annotations

from collections.abc import Iterator

from agenticcarekit.kernel.contracts import AckError

__all__ = ["MicAdapter"]

#: Proposed code for "optional capability dependency missing" (E0xx =
#: bootstrap/environment, per kernel/contracts/errors.py's ranges). Not yet
#: registered in spec/errors.json — that registry is owned by W-J, and this
#: workstream does not edit spec/. Flagged as a concern in the W-D report;
#: `AckError.explain()` will return `None` for it until it is registered.
E_SOUNDDEVICE_MISSING = "E012"


class MicAdapter:
    """Streams raw PCM16 audio from the default input device in fixed-size
    chunks — the local-mic counterpart to `twilio_adapter.TwilioMediaStreamAdapter`.

    Raises at construction time (never on first read) if ``sounddevice``
    is not installed, naming the exact fix.

    Example:
        >>> try:
        ...     MicAdapter()
        ... except AckError as exc:
        ...     exc.code, "sounddevice" in exc.message
        ('E012', True)
    """

    def __init__(self, *, samplerate: int = 16000, chunk_ms: int = 20) -> None:
        try:
            import sounddevice
        except ImportError as exc:
            raise AckError(
                "the `sounddevice` package is not installed",
                code=E_SOUNDDEVICE_MISSING,
                why=(
                    "MicAdapter streams raw audio from the local input device "
                    "via `sounddevice`, an optional dependency of the voice "
                    "capability that is not installed by default."
                ),
                fix="uv pip install sounddevice   # or: pip install sounddevice",
            ) from exc
        self._sounddevice = sounddevice
        self.samplerate = samplerate
        self.chunk_ms = chunk_ms

    def stream(self) -> Iterator[bytes]:
        """Yield raw PCM16 chunks from the default input device until the
        caller stops iterating (e.g. on voice-activity-detection silence,
        or a barge-in signal firing elsewhere)."""
        frames_per_chunk = int(self.samplerate * self.chunk_ms / 1000)
        with self._sounddevice.RawInputStream(
            samplerate=self.samplerate, channels=1, dtype="int16"
        ) as stream:
            while True:
                data, _overflowed = stream.read(frames_per_chunk)
                yield bytes(data)
