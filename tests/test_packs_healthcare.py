"""W-F acceptance — the healthcare pack.

Covers: deterministic synthetic data generation (same seed → byte-
identical output; different seed → different output), the PHI redactor
scored against the bundled labelled set with the measured precision/
recall required to match what's published in ``healthcare/README.md``
(so the numbers can't silently drift), and a spot-check that every
generated intake transcript's embedded PHI (name, phone, MRN, DOB) is
actually redacted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import Redaction, Redactor
from agenticcarekit.packs.healthcare import (
    Coding,
    DocumentReference,
    Encounter,
    MedicationStatement,
    Observation,
    Patient,
    PHIRedactor,
    Quantity,
    SyntheticGenerator,
    score_phi_redactor,
)
from pydantic import ValidationError

HEALTHCARE_DIR = Path(__file__).parent.parent / "packages" / "agenticcarekit" / "packs" / "healthcare"
LABELLED_PATH = HEALTHCARE_DIR / "evalsets" / "phi_labelled.jsonl"
README_PATH = HEALTHCARE_DIR / "README.md"


# ── models ────────────────────────────────────────────────────────────────


def test_models_are_frozen_and_strict():
    p = Patient(
        id="patient-0001", mrn="MRN-100234", name_family="Chen", name_given="Wei",
        gender="female", birth_date="1985-03-15",
    )
    with pytest.raises(ValidationError):
        p.name_family = "Other"  # frozen
    with pytest.raises(ValidationError):
        Patient(
            id="x", mrn="y", name_family="a", name_given="b", gender="female",
            birth_date="2000-01-01", unknown_field="nope",
        )  # extra="forbid"
    with pytest.raises(ValidationError):
        Patient(
            id="x", mrn="y", name_family="a", name_given="b", gender="not-a-gender",
            birth_date="2000-01-01",
        )  # invalid Literal


def test_model_shapes_are_fhir_lite():
    obs = Observation(
        id="obs-0001", subject="Patient/patient-0001", encounter="Encounter/e-1",
        status="final", code=Coding(system="http://loinc.org", code="8867-4", display="Heart rate"),
        effective_datetime="2024-05-01T09:05:00", value_quantity=Quantity(value=72, unit="/min"),
    )
    assert obs.code.system == "http://loinc.org"
    assert obs.value_quantity.unit == "/min"

    doc = DocumentReference(
        id="doc-1", subject="Patient/patient-0001",
        type=Coding(system="http://loinc.org", code="34117-2", display="History and physical note"),
        date="2024-05-01T09:10:00", status="current", content_text="note text",
    )
    assert doc.status == "current"

    med = MedicationStatement(
        id="medstmt-1", subject="Patient/patient-0001",
        medication_code=Coding(
            system="http://www.nlm.nih.gov/research/umls/rxnorm", code="314076",
            display="Lisinopril 10 MG Oral Tablet",
        ),
        status="active", effective_start="2024-01-15", dosage_text="10 mg by mouth once daily",
    )
    assert med.status == "active"

    enc = Encounter(
        id="encounter-1", subject="Patient/patient-0001", status="finished",
        class_code="AMB", period_start="2024-05-01T09:00:00",
    )
    assert enc.class_code == "AMB"


# ── deterministic synthetic generator ───────────────────────────────────


def _full_dump(seed: int) -> dict:
    gen = SyntheticGenerator(seed=seed)
    patients = gen.patients(10)
    encounters = [gen.encounters(p, n=2) for p in patients]
    vitals = [gen.vitals(e) for enc_list in encounters for e in enc_list]
    meds = [gen.medications(p, n=2) for p in patients]
    transcripts = gen.intake_transcripts(20)
    return {
        "patients": [p.model_dump() for p in patients],
        "encounters": [[e.model_dump() for e in lst] for lst in encounters],
        "vitals": [[o.model_dump() for o in lst] for lst in vitals],
        "medications": [[m.model_dump() for m in lst] for lst in meds],
        "transcripts": transcripts,
    }


def test_same_seed_byte_identical():
    dump_a = json.dumps(_full_dump(42), sort_keys=True)
    dump_b = json.dumps(_full_dump(42), sort_keys=True)
    assert dump_a == dump_b


def test_different_seed_differs():
    dump_42 = json.dumps(_full_dump(42), sort_keys=True)
    dump_43 = json.dumps(_full_dump(43), sort_keys=True)
    assert dump_42 != dump_43


def test_intake_transcripts_count_and_shape():
    gen = SyntheticGenerator(seed=7)
    transcripts = gen.intake_transcripts(20)
    assert len(transcripts) == 20
    for t in transcripts:
        assert set(t.keys()) == {"id", "complaint_type", "turns", "full_text"}
        assert t["turns"]
        for turn in t["turns"]:
            assert turn["speaker"] in {"RECEPTIONIST", "PATIENT", "NURSE"}


def test_synthetic_generator_conforms_to_no_forbidden_randomness_sources():
    """Static check: synthetic.py never calls datetime.now()/uuid4()."""
    src = (HEALTHCARE_DIR / "synthetic.py").read_text()
    assert "datetime.now(" not in src
    assert "uuid4(" not in src
    assert "import uuid" not in src


# ── PHI redactor: protocol conformance ──────────────────────────────────


def test_phi_redactor_satisfies_redactor_protocol():
    r = PHIRedactor()
    assert isinstance(r, Redactor)
    assert r.name == "healthcare.phi"


def test_redaction_spans_are_against_original_text():
    r = PHIRedactor()
    text = "Call Jane Smith at 555-123-4567 about MRN 48213."
    clean, reds = r.redact(text)
    assert isinstance(reds, list)
    for red in reds:
        assert isinstance(red, Redaction)
        # span must slice out of the ORIGINAL text meaningfully (non-empty)
        assert text[red.start : red.end] == red.replacement or len(text[red.start : red.end]) > 0
    assert clean != text


def test_token_stability_within_one_call():
    r = PHIRedactor()
    clean, reds = r.redact("Call Jane Smith and then call Jane Smith again.")
    tokens = {red.replacement for red in reds if red.category == "NAME"}
    assert tokens == {"[NAME-1]"}


def test_year_alone_is_not_redacted():
    r = PHIRedactor()
    clean, reds = r.redact("The clinic opened in 2010.")
    assert reds == []
    assert clean == "The clinic opened in 2010."


def test_state_name_alone_is_not_redacted():
    r = PHIRedactor()
    clean, reds = r.redact("She recently moved to California for work.")
    assert reds == []


# ── PHI redactor: scored against the labelled golden set ───────────────


def test_phi_redactor_precision_recall_meets_bar_and_matches_readme():
    result = score_phi_redactor(PHIRedactor(), LABELLED_PATH)
    precision = result["precision"]
    recall = result["recall"]

    # Acceptance bar from the brief: >= 0.8 on both.
    assert precision >= 0.8, f"precision {precision} below 0.8 floor"
    assert recall >= 0.8, f"recall {recall} below 0.8 floor"

    # The measured numbers must be exactly what's published in the
    # README (formatted to 4 decimal places) — this is what stops the
    # published numbers from silently drifting away from reality.
    readme_text = README_PATH.read_text()
    precision_str = f"{precision:.4f}"
    recall_str = f"{recall:.4f}"
    assert precision_str in readme_text, (
        f"README does not contain measured precision {precision_str}"
    )
    assert recall_str in readme_text, (
        f"README does not contain measured recall {recall_str}"
    )

    # Not a rigged perfect score — the labelled set intentionally
    # includes known-limitation cases.
    assert precision < 1.0
    assert recall < 1.0


def test_labelled_set_has_required_size_and_negatives():
    lines = [json.loads(line) for line in LABELLED_PATH.read_text().splitlines() if line.strip()]
    assert len(lines) >= 25
    negative_rows = [row for row in lines if "negative" in row.get("tags", [])]
    assert len(negative_rows) >= 3
    for row in negative_rows:
        tags = row.get("tags", [])
        if "name-outside-wordlist" in tags or "unsupported-date-format" in tags:
            # Known-limitation false-negative cases: genuinely PHI, but
            # outside what the regex/wordlist redactor catches — these
            # legitimately carry expected entities.
            assert row["expected"]["entities"]
        else:
            # Pure negatives (year alone, state name, eponym, wordlist
            # false-positive) must have zero *expected* entities.
            assert row["expected"]["entities"] == []


# ── transcripts run through the redactor: spot-check embedded PHI ──────


def test_every_generated_transcript_has_embedded_phi_redacted():
    gen = SyntheticGenerator(seed=99)
    redactor = PHIRedactor()
    transcripts = gen.intake_transcripts(20)
    assert len(transcripts) == 20

    date_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")
    phone_pattern = re.compile(r"\d{3}-\d{3}-\d{4}")
    mrn_pattern = re.compile(r"MRN-\d{6}")

    for t in transcripts:
        full_text = t["full_text"]
        clean, reds = redactor.redact(full_text)
        categories = {r.category for r in reds}

        # Every transcript embeds a DOB, phone, and MRN (see synthetic.py
        # templates) — confirm the raw values are gone from clean text
        # and the categories were actually flagged.
        assert not date_pattern.search(clean), f"DOB leaked in transcript {t['id']}: {clean}"
        assert not phone_pattern.search(clean), f"phone leaked in transcript {t['id']}: {clean}"
        assert not mrn_pattern.search(clean), f"MRN leaked in transcript {t['id']}: {clean}"
        assert "DATE" in categories
        assert "PHONE" in categories
        assert "MRN" in categories
        assert "NAME" in categories
