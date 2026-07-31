# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""``find_referral_slots`` — search a synthetic specialist panel for open slots.

Administrative search only: this tool filters by specialty, network, and
availability — it never recommends a specific clinician based on
clinical reasoning about the patient's condition.
"""

from __future__ import annotations

from dataclasses import dataclass

from agenticcarekit.kernel.contracts import tool

# Synthetic specialist panel. Real deployments replace this with a query
# against the referral network's directory/scheduling system.
_MOCK_PANEL = [
    {"specialist": "Dr. Synthetic Ortiz", "specialty": "cardiology", "network": "SYN-PAYER-AETNA", "slots": ["2026-08-04T09:00", "2026-08-06T14:30"]},
    {"specialist": "Dr. Synthetic Lin", "specialty": "cardiology", "network": "SYN-PAYER-UHC", "slots": ["2026-08-05T11:00"]},
    {"specialist": "Dr. Synthetic Osei", "specialty": "dermatology", "network": "SYN-PAYER-BCBS", "slots": ["2026-08-03T10:00", "2026-08-03T15:00"]},
    {"specialist": "Dr. Synthetic Kapoor", "specialty": "orthopedics", "network": "SYN-PAYER-AETNA", "slots": ["2026-08-07T08:30"]},
]


@dataclass(frozen=True)
class ReferralSlot:
    """One open slot with a matching specialist. All synthetic here."""

    specialist: str
    specialty: str
    network: str
    starts_at: str


def mock_find_referral_slots(specialty: str, network: str | None = None) -> list[ReferralSlot]:
    """Canned slot search over the synthetic specialist panel."""
    results: list[ReferralSlot] = []
    for entry in _MOCK_PANEL:
        if entry["specialty"] != specialty:
            continue
        if network is not None and entry["network"] != network:
            continue
        for slot in entry["slots"]:
            results.append(
                ReferralSlot(
                    specialist=entry["specialist"],
                    specialty=entry["specialty"],
                    network=entry["network"],
                    starts_at=slot,
                )
            )
    return results


@tool(permissions={"network"}, mock=mock_find_referral_slots)
def find_referral_slots(specialty: str, network: str | None = None) -> list[ReferralSlot]:
    """Find open referral slots for ``specialty``, optionally filtered to
    a payer ``network``.

    Administrative search only — ranks by availability and network match,
    never by a clinical judgment about which specialist is "better" for
    this patient. That judgment belongs to the referring clinician.

    Real implementation note: replace the body below with a call to your
    referral network's directory/scheduling API. Keep
    ``mock_find_referral_slots`` realistic so offline demos stay
    meaningful.
    """
    raise NotImplementedError(
        "find_referral_slots has no live implementation in this template — "
        "wire it to your referral network's directory API, or keep "
        "running with --offline to use the mock."
    )
