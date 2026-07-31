"""Curated intake-call scenario data: complaint types, opening lines, and
clinical vitals/medication vocabulary used by ``synthetic.py``.

All synthetic — no real clinical content, no real patients.
"""

COMPLAINT_TYPES: list[str] = [
    "medication_refill",
    "new_patient",
    "follow_up",
    "billing_question",
    "referral_request",
    "appointment_reschedule",
    "test_results",
    "symptom_triage",
]

# Vitals: (display, LOINC code, unit, low, high) — low/high bound the
# synthetic random value range (kept in a plausible clinical band).
VITAL_SIGNS: list[tuple[str, str, str, float, float]] = [
    ("Body temperature", "8310-5", "Cel", 36.1, 38.5),
    ("Heart rate", "8867-4", "/min", 55, 110),
    ("Systolic blood pressure", "8480-6", "mm[Hg]", 100, 145),
    ("Diastolic blood pressure", "8462-4", "mm[Hg]", 60, 95),
    ("Respiratory rate", "9279-1", "/min", 12, 22),
    ("Oxygen saturation", "59408-5", "%", 92, 100),
]

# (display, RxNorm-style code, dosage text)
MEDICATIONS: list[tuple[str, str, str]] = [
    ("Lisinopril 10 MG Oral Tablet", "314076", "10 mg by mouth once daily"),
    ("Metformin 500 MG Oral Tablet", "860975", "500 mg by mouth twice daily"),
    ("Atorvastatin 20 MG Oral Tablet", "617312", "20 mg by mouth at bedtime"),
    ("Albuterol 90 MCG Inhaler", "745679", "2 puffs every 4-6 hours as needed"),
    ("Levothyroxine 50 MCG Oral Tablet", "966125", "50 mcg by mouth once daily"),
    ("Amoxicillin 500 MG Oral Capsule", "308191", "500 mg by mouth three times daily"),
    ("Sertraline 50 MG Oral Tablet", "312940", "50 mg by mouth once daily"),
]

ENCOUNTER_REASONS: list[tuple[str, str, str]] = [
    ("Encounter for general adult medical examination", "162673000", "annual physical"),
    ("Follow-up encounter", "185389009", "follow-up visit"),
    ("Prescription renewal", "182931001", "medication refill"),
    ("Referral to specialist", "306206005", "specialist referral"),
]
