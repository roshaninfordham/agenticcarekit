# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Tools available to the care-copilot agent.

Every tool ships a mandatory mock (Contract 3 —
``agenticcarekit.kernel.contracts.tool``): decoration fails at import
time if a mock is missing (E502). The mocks below use realistic synthetic
payers, specialists, and slots — that mock data *is* the offline demo,
not a placeholder for one.
"""

from __future__ import annotations

from app.tools.eligibility import check_eligibility
from app.tools.prior_auth import draft_prior_auth
from app.tools.referrals import find_referral_slots
from app.tools.scheduling import schedule_appointment

ALL_TOOLS = [check_eligibility, draft_prior_auth, find_referral_slots, schedule_appointment]

__all__ = [
    "ALL_TOOLS",
    "check_eligibility",
    "draft_prior_auth",
    "find_referral_slots",
    "schedule_appointment",
]
