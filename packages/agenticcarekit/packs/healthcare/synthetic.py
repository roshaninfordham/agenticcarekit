"""Deterministic synthetic clinical data generator.

``SyntheticGenerator(seed)`` draws every random value from a single
``random.Random(seed)`` instance — never the system clock, never a
randomly generated UUID. The same seed, called in the same sequence,
always produces byte-identical output (proven in
``tests/test_packs_healthcare.py`` by dumping full JSON twice and
comparing). This is what makes the synthetic data usable as a stable
fixture across CI runs and across languages.

None of the values here describe real people. Names, addresses, phone
numbers, and MRNs are drawn from curated word lists / numeric ranges
purely for plausibility.
"""

from __future__ import annotations

import random

from .data.complaints import COMPLAINT_TYPES, ENCOUNTER_REASONS, MEDICATIONS, VITAL_SIGNS
from .data.names import FIRST_NAMES, LAST_NAMES
from .data.places import CITIES, STATE_ABBREVIATIONS, STREET_NAMES, STREET_SUFFIXES
from .models import Coding, Encounter, MedicationStatement, Observation, Patient, Quantity

__all__ = ["SyntheticGenerator"]

_GENDERS = ["male", "female", "other", "unknown"]
_ENCOUNTER_STATUSES = ["planned", "in-progress", "finished", "cancelled"]
_ENCOUNTER_CLASSES = ["AMB", "EMER", "IMP"]
_MED_STATUSES = ["active", "completed", "stopped", "on-hold"]


def _random_date(rng: random.Random, year_lo: int, year_hi: int) -> str:
    year = rng.randint(year_lo, year_hi)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)  # always a valid day regardless of month
    return f"{year:04d}-{month:02d}-{day:02d}"


def _random_datetime(rng: random.Random, year_lo: int, year_hi: int) -> str:
    date = _random_date(rng, year_lo, year_hi)
    hour = rng.randint(7, 18)
    minute = rng.randint(0, 59)
    return f"{date}T{hour:02d}:{minute:02d}:00"


def _random_phone(rng: random.Random) -> str:
    return f"{rng.randint(200, 989)}-{rng.randint(200, 989)}-{rng.randint(1000, 9999)}"


def _random_mrn(rng: random.Random) -> str:
    return f"MRN-{rng.randint(100000, 999999)}"


class SyntheticGenerator:
    """Deterministic synthetic FHIR-lite data and intake transcripts.

    Example:
        >>> g1 = SyntheticGenerator(seed=42)
        >>> g2 = SyntheticGenerator(seed=42)
        >>> a = [p.model_dump() for p in g1.patients(3)]
        >>> b = [p.model_dump() for p in g2.patients(3)]
        >>> a == b
        True
        >>> g3 = SyntheticGenerator(seed=43)
        >>> c = [p.model_dump() for p in g3.patients(3)]
        >>> a == c
        False
    """

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._rng = random.Random(seed)
        self._patient_seq = 0
        self._encounter_seq = 0
        self._obs_seq = 0
        self._med_seq = 0
        self._transcript_seq = 0

    # ── patients ─────────────────────────────────────────────────────

    def patients(self, n: int) -> list[Patient]:
        """Generate ``n`` deterministic synthetic patients.

        Example:
            >>> g = SyntheticGenerator(seed=1)
            >>> len(g.patients(2))
            2
        """
        return [self._make_patient() for _ in range(n)]

    def _make_patient(self) -> Patient:
        self._patient_seq += 1
        idx = self._patient_seq
        rng = self._rng
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        gender = rng.choice(_GENDERS)
        birth_date = _random_date(rng, 1930, 2015)
        phone = _random_phone(rng)
        street_num = rng.randint(100, 9999)
        street = rng.choice(STREET_NAMES)
        suffix = rng.choice(STREET_SUFFIXES)
        city = rng.choice(CITIES)
        state = rng.choice(STATE_ABBREVIATIONS)
        postal = f"{rng.randint(10000, 99999)}"
        return Patient(
            id=f"patient-{idx:04d}",
            mrn=_random_mrn(rng),
            name_family=last,
            name_given=first,
            gender=gender,
            birth_date=birth_date,
            phone=phone,
            address_line=f"{street_num} {street} {suffix}",
            address_city=city,
            address_state=state,
            address_postal_code=postal,
        )

    # ── encounters ───────────────────────────────────────────────────

    def encounters(self, patient: Patient, n: int = 2) -> list[Encounter]:
        """Generate ``n`` deterministic encounters for ``patient``.

        Example:
            >>> g = SyntheticGenerator(seed=1)
            >>> p = g.patients(1)[0]
            >>> len(g.encounters(p, n=1))
            1
        """
        return [self._make_encounter(patient) for _ in range(n)]

    def _make_encounter(self, patient: Patient) -> Encounter:
        self._encounter_seq += 1
        idx = self._encounter_seq
        rng = self._rng
        reason_display, reason_code, _ = rng.choice(ENCOUNTER_REASONS)
        start = _random_datetime(rng, 2023, 2025)
        return Encounter(
            id=f"encounter-{idx:04d}",
            subject=f"Patient/{patient.id}",
            status=rng.choice(_ENCOUNTER_STATUSES),
            class_code=rng.choice(_ENCOUNTER_CLASSES),
            period_start=start,
            period_end=None,
            reason=Coding(
                system="http://snomed.info/sct", code=reason_code, display=reason_display
            ),
        )

    # ── vitals ───────────────────────────────────────────────────────

    def vitals(self, encounter: Encounter, n: int | None = None) -> list[Observation]:
        """Generate vitals :class:`Observation` records for ``encounter``.
        Defaults to one Observation per known vital sign.

        Example:
            >>> g = SyntheticGenerator(seed=1)
            >>> p = g.patients(1)[0]
            >>> e = g.encounters(p, n=1)[0]
            >>> len(g.vitals(e)) == len(VITAL_SIGNS)
            True
        """
        rng = self._rng
        signs = VITAL_SIGNS if n is None else rng.sample(VITAL_SIGNS, k=min(n, len(VITAL_SIGNS)))
        out = []
        for display, code, unit, lo, hi in signs:
            self._obs_seq += 1
            idx = self._obs_seq
            value = round(rng.uniform(lo, hi), 1)
            out.append(
                Observation(
                    id=f"obs-{idx:04d}",
                    subject=encounter.subject,
                    encounter=f"Encounter/{encounter.id}",
                    status="final",
                    code=Coding(system="http://loinc.org", code=code, display=display),
                    effective_datetime=encounter.period_start,
                    value_quantity=Quantity(value=value, unit=unit),
                )
            )
        return out

    # ── medications ──────────────────────────────────────────────────

    def medications(self, patient: Patient, n: int = 2) -> list[MedicationStatement]:
        """Generate ``n`` deterministic medication statements for ``patient``.

        Example:
            >>> g = SyntheticGenerator(seed=1)
            >>> p = g.patients(1)[0]
            >>> len(g.medications(p, n=2))
            2
        """
        rng = self._rng
        meds = rng.sample(MEDICATIONS, k=min(n, len(MEDICATIONS)))
        out = []
        for display, code, dosage in meds:
            self._med_seq += 1
            idx = self._med_seq
            start = _random_date(rng, 2022, 2025)
            out.append(
                MedicationStatement(
                    id=f"medstmt-{idx:04d}",
                    subject=f"Patient/{patient.id}",
                    medication_code=Coding(
                        system="http://www.nlm.nih.gov/research/umls/rxnorm",
                        code=code,
                        display=display,
                    ),
                    status=rng.choice(_MED_STATUSES),
                    effective_start=start,
                    dosage_text=dosage,
                )
            )
        return out

    # ── intake transcripts ───────────────────────────────────────────

    def intake_transcripts(self, n: int = 20) -> list[dict]:
        """Generate ``n`` deterministic multi-speaker intake transcripts.

        Each transcript is a plain dict: ``{"id", "complaint_type",
        "turns": [{"speaker", "text"}, ...], "full_text"}``. Turns embed
        realistic synthetic PHI (name, DOB, phone, MRN) so a PHI redactor
        has real values to catch.

        Example:
            >>> g = SyntheticGenerator(seed=1)
            >>> transcripts = g.intake_transcripts(3)
            >>> len(transcripts)
            3
            >>> sorted(transcripts[0].keys())
            ['complaint_type', 'full_text', 'id', 'turns']
        """
        return [self._make_transcript() for _ in range(n)]

    def _make_transcript(self) -> dict:
        self._transcript_seq += 1
        idx = self._transcript_seq
        rng = self._rng
        complaint_type = COMPLAINT_TYPES[(idx - 1) % len(COMPLAINT_TYPES)]
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        full_name = f"{first} {last}"
        dob = _random_date(rng, 1935, 2015)
        phone = _random_phone(rng)
        mrn = _random_mrn(rng)
        turns = _TRANSCRIPT_TEMPLATES[complaint_type](rng, full_name, dob, phone, mrn)
        full_text = "\n".join(f"{speaker}: {text}" for speaker, text in turns)
        return {
            "id": f"transcript-{idx:04d}",
            "complaint_type": complaint_type,
            "turns": [{"speaker": s, "text": t} for s, t in turns],
            "full_text": full_text,
        }


def _t_medication_refill(rng, name, dob, phone, mrn):
    med = rng.choice(MEDICATIONS)[0]
    return [
        ("RECEPTIONIST", "Thanks for calling Riverside Clinic, how can I help you today?"),
        ("PATIENT", f"Hi, this is {name} calling, I need a refill on my medication."),
        ("RECEPTIONIST", f"Sure, can I get your date of birth to pull up your chart? Name: {name}"),
        ("PATIENT", f"My date of birth is {dob}."),
        ("RECEPTIONIST", f"Got it, I see your MRN {mrn} on file. And a callback number?"),
        ("PATIENT", f"You can reach me at {phone}."),
        ("NURSE", f"This is the nurse line, I'll send the refill for {med} to your pharmacy today."),
    ]


def _t_new_patient(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Good morning, thank you for calling, are you a new patient?"),
        ("PATIENT", f"Yes, my name is {name} and I'd like to establish care."),
        ("RECEPTIONIST", f"Welcome, {name}. What's your date of birth?"),
        ("PATIENT", f"It's {dob}."),
        ("RECEPTIONIST", "And a phone number where we can reach you?"),
        ("PATIENT", f"{phone} is best."),
        ("RECEPTIONIST", f"Perfect, I've created MRN {mrn} for you and booked you in for next week."),
    ]


def _t_follow_up(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Front desk, how can I help?"),
        ("PATIENT", f"This is {name}, I need to schedule a follow-up after my last visit."),
        ("RECEPTIONIST", f"Sure, let me pull up MRN {mrn}. Can you confirm date of birth?"),
        ("PATIENT", f"{dob}."),
        ("NURSE", f"Hi {name}, the doctor wants to see you again in two weeks to check your progress."),
        ("PATIENT", f"That works, call me at {phone} to confirm the time."),
    ]


def _t_billing_question(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Billing department, how can I assist you?"),
        ("PATIENT", f"Hi, this is {name}, I have a question about a recent statement."),
        ("RECEPTIONIST", f"Sure, can you confirm your MRN {mrn} and date of birth?"),
        ("PATIENT", f"Date of birth is {dob}, and you can reach me at {phone} if we get cut off."),
        ("RECEPTIONIST", "Thanks, let me look into that charge for you."),
    ]


def _t_referral_request(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Hello, referrals desk speaking."),
        ("PATIENT", f"This is {name} calling about a referral to a specialist."),
        ("RECEPTIONIST", f"I have MRN {mrn} pulled up, confirming date of birth {dob}?"),
        ("PATIENT", "That's correct."),
        ("NURSE", f"I'll send the referral over today, we'll call {phone} once it's approved."),
    ]


def _t_appointment_reschedule(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Scheduling, how can I help?"),
        ("PATIENT", f"Hi, my name is {name}, I need to reschedule my appointment."),
        ("RECEPTIONIST", f"No problem, MRN {mrn}, date of birth {dob}, is that right?"),
        ("PATIENT", f"Yes. Please call {phone} to confirm the new time."),
        ("RECEPTIONIST", "You're all set for next Thursday at 10am."),
    ]


def _t_test_results(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Nurse line, how can I help?"),
        ("PATIENT", f"This is {name}, I'm calling to check on my recent lab results."),
        ("NURSE", f"Let me look that up, MRN {mrn}, and can you confirm your date of birth?"),
        ("PATIENT", f"{dob}."),
        ("NURSE", f"Everything looks normal, I'll have the doctor call {phone} to go over the details."),
    ]


def _t_symptom_triage(rng, name, dob, phone, mrn):
    return [
        ("RECEPTIONIST", "Triage line, what's going on today?"),
        ("PATIENT", f"Hi, this is {name}, I've had a fever and cough for two days."),
        ("NURSE", f"Okay, let me pull your chart. MRN {mrn}, date of birth?"),
        ("PATIENT", f"{dob}."),
        ("NURSE", f"Please monitor your temperature and we'll call {phone} within the hour."),
    ]


_TRANSCRIPT_TEMPLATES = {
    "medication_refill": _t_medication_refill,
    "new_patient": _t_new_patient,
    "follow_up": _t_follow_up,
    "billing_question": _t_billing_question,
    "referral_request": _t_referral_request,
    "appointment_reschedule": _t_appointment_reschedule,
    "test_results": _t_test_results,
    "symptom_triage": _t_symptom_triage,
}
