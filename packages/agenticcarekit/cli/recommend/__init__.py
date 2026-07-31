"""Model recommendation (brief §7.2) — a declarative, auditable rule table.

Import surface::

    from agenticcarekit.cli.recommend import (
        CATALOG, Candidate, Recommendation, Requirements,
        explain_ranking, rank, recommend, validate_choice,
    )
"""

from __future__ import annotations

from .catalog import (
    CATALOG,
    HOSTED_PRIMARY,
    ModelEntry,
    audio_capable_tags,
    hosted_tags,
    local_tags,
)
from .engine import (
    Candidate,
    Recommendation,
    Requirements,
    explain_ranking,
    rank,
    recommend,
    validate_choice,
)
from .rules import ETA_THRESHOLD_SECONDS, HARD_FILTERS, SOFT_SCORES, HardFilter, SoftScore

__all__ = [
    "CATALOG",
    "Candidate",
    "ETA_THRESHOLD_SECONDS",
    "HARD_FILTERS",
    "HOSTED_PRIMARY",
    "HardFilter",
    "ModelEntry",
    "Recommendation",
    "Requirements",
    "SOFT_SCORES",
    "SoftScore",
    "audio_capable_tags",
    "explain_ranking",
    "hosted_tags",
    "local_tags",
    "rank",
    "recommend",
    "validate_choice",
]
