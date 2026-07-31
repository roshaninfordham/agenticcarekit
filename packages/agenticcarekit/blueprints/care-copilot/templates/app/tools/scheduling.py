# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""``schedule_appointment`` — book a synthetic appointment slot.

Logistics only: this tool records that a chosen slot was booked. It never
chooses the slot itself — that decision is either made by the human user
or already narrowed down via ``find_referral_slots``.
"""

from __future__ import annotations

from dataclasses import dataclass

from agenticcarekit.kernel.contracts import tool

# In-memory synthetic booking ledger for the demo. Real deployments
# replace this with a call to the scheduling system of record.
_MOCK_BOOKINGS: list[dict[str, str]] = []


@dataclass(frozen=True)
class BookingConfirmation:
    """Confirmation of a synthetic booking. Not a real appointment."""

    confirmation_id: str
    specialist: str
    starts_at: str
    status: str = "confirmed-synthetic"


def mock_schedule_appointment(specialist: str, starts_at: str, patient_ref: str) -> BookingConfirmation:
    """Canned booking — appends to an in-memory ledger, calls no system."""
    confirmation_id = f"SYN-BOOK-{len(_MOCK_BOOKINGS) + 1:04d}"
    _MOCK_BOOKINGS.append(
        {
            "confirmation_id": confirmation_id,
            "specialist": specialist,
            "starts_at": starts_at,
            "patient_ref": patient_ref,
        }
    )
    return BookingConfirmation(
        confirmation_id=confirmation_id, specialist=specialist, starts_at=starts_at
    )


@tool(permissions={"network", "writes"}, mock=mock_schedule_appointment)
def schedule_appointment(specialist: str, starts_at: str, patient_ref: str) -> BookingConfirmation:
    """Book ``starts_at`` with ``specialist`` for ``patient_ref``.

    Logistics only — records a booking a human already decided on (a
    slot returned by ``find_referral_slots`` and confirmed by staff or
    the patient). This tool does not select the slot or the specialist.

    Real implementation note: replace the body below with a call to your
    scheduling system of record. Keep ``mock_schedule_appointment``
    realistic (confirmation id, status) so offline demos stay meaningful.
    """
    raise NotImplementedError(
        "schedule_appointment has no live implementation in this template "
        "— wire it to your scheduling system, or keep running with "
        "--offline to use the mock."
    )
