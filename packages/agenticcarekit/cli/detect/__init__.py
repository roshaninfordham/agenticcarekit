"""Machine detection (brief §7.1).

Every probe runs concurrently with its **own** timeout and degrades to
``unknown`` rather than raising — a slow ``nvidia-smi`` must never block
``ack init``, and a missing binary is a fact, not an error.

Example:
    >>> from agenticcarekit.cli.detect import MachineFacts
    >>> MachineFacts().os
    'unknown'
"""

from __future__ import annotations

from .facts import MachineFacts, ProbeResult
from .probes import PROBES, ProbeEnv, default_env, run_probes

__all__ = [
    "MachineFacts",
    "PROBES",
    "ProbeEnv",
    "ProbeResult",
    "default_env",
    "run_probes",
]
