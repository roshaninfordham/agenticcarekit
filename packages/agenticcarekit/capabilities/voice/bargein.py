"""Barge-in: let a caller interrupt the assistant while it is generating or
speaking a reply, and have `loop.VoiceLoop.run_turn` stop cleanly.

Barge-in has no ASR/TTS-specific mechanics of its own here — it is a shared
`threading.Event` that `run_turn` polls at safe checkpoints: between ASR
partials, before calling the LLM, and before/after TTS synthesis. Because
`types.TTSProvider.synthesize` is one blocking call rather than a stream, a
barge-in signal can only take effect *around* it, not mid-byte — that is
the honest limit of the current `TTSProvider` protocol, not a bug: Contract
1's rule ("providers declare capabilities, never silently degrade") applies
here too — we don't pretend to interrupt mid-synthesis when the protocol
doesn't support it.
"""

from __future__ import annotations

import threading

__all__ = ["BargeIn", "is_interrupted"]


def is_interrupted(interrupt: threading.Event | None) -> bool:
    """Checkpoint helper used throughout `loop.VoiceLoop.run_turn`.

    Returns `False` when no interrupt signal is wired up at all, so callers
    that never pass `interrupt=` pay no barge-in cost.

    Example:
        >>> is_interrupted(None)
        False
        >>> e = threading.Event()
        >>> is_interrupted(e)
        False
        >>> e.set()
        >>> is_interrupted(e)
        True
    """
    return interrupt is not None and interrupt.is_set()


class BargeIn:
    """Thin, testable wrapper around a `threading.Event` used as the
    barge-in signal passed to `loop.VoiceLoop.run_turn(..., interrupt=...)`.

    Optional convenience — `run_turn` itself only requires a plain
    `threading.Event`, so a caller (e.g. a voice-activity-detection thread
    watching the mic while the assistant talks) can use either a bare
    `threading.Event()` or this wrapper.

    Example:
        >>> b = BargeIn()
        >>> b.is_set()
        False
        >>> b.trigger()
        >>> b.is_set()
        True
        >>> b.reset()
        >>> b.is_set()
        False
    """

    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event if event is not None else threading.Event()

    @property
    def event(self) -> threading.Event:
        """The underlying `threading.Event` — pass this to `run_turn`."""
        return self._event

    def is_set(self) -> bool:
        return self._event.is_set()

    def trigger(self) -> None:
        """Signal that the user has started speaking again — cut off
        whatever the assistant is currently doing."""
        self._event.set()

    def reset(self) -> None:
        """Clear the signal, ready for the next turn."""
        self._event.clear()
