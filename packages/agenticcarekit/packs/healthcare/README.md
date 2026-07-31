# healthcare pack

FHIR-lite clinical models, a PHI redactor covering the 18 HIPAA Safe
Harbor identifier categories, a deterministic synthetic data generator,
and clinical eval sets. See `agenticcarekit.packs._template` for what a
minimal pack looks like, and this pack for what a full one looks like.

## Contents

- `models.py` — FHIR-lite Pydantic models: `Patient`, `Encounter`,
  `Observation`, `DocumentReference`, `MedicationStatement`, plus the
  shared `Coding` (`{system, code, display}`) and `Quantity`
  (`{value, unit}`) submodels. Flattened and pragmatic, not a full FHIR
  R4 implementation — deviations are documented in `models.py`'s module
  docstring.
- `phi.py` — `PHIRedactor` (`name = "healthcare.phi"`), a regex- and
  wordlist-based de-identifier. Implements the
  `agenticcarekit.kernel.contracts.Redactor` protocol.
- `synthetic.py` — `SyntheticGenerator(seed)`, fully deterministic
  (`random.Random(seed)` only — no `datetime.now()`, no `uuid4()`).
  Generates patients, encounters, vitals, medications, and multi-speaker
  intake call transcripts.
- `scoring.py` — `score_phi_redactor(redactor, labelled_path)`, entity-
  level precision/recall scoring against a labelled JSONL set.
- `evalsets/phi_labelled.jsonl` — 33 hand-written labelled sentences
  covering all 18 categories plus tricky negatives (a bare year is not
  PHI, a state name alone is not PHI, "Dr." immediately before a disease
  eponym is not a person's name) **and** honest known-limitation cases
  (see below).
- `evalsets/intake_extraction.jsonl` — 10 transcript → structured-summary
  golden cases for extraction capabilities built on top of this pack.
- `data/` — curated name/place/complaint word lists backing both the
  generator and the redactor.

## PHI redaction: honesty first

**This is regex/wordlist de-identification, not a certified HIPAA Safe
Harbor implementation.** It has no clinical NLP model, no manual review,
and no legal sign-off — see the caveats in `phi.py`'s module docstring
for exactly what it does and does not catch.

Measured on the bundled labelled set (`evalsets/phi_labelled.jsonl`,
entity-level match: category + overlapping span):
**precision 0.9688, recall 0.9394** — do not treat as Safe Harbor
certification.

The labelled set deliberately includes cases the redactor gets wrong,
so this number isn't a rigged 1.0:

- **False positive** — a real name-word coincidence unrelated to any
  patient (e.g. "James Brown" mentioned as a song on the radio) still
  matches the curated first/last name wordlist. Any wordlist-based name
  matcher trades this kind of over-redaction for coverage of names that
  lack an honorific or other context cue.
- **False negatives** — a name outside the curated wordlist and lacking
  an honorific/context cue (e.g. an uncommon or non-Western name with no
  "Dr."/"my name is" nearby), and a date in a format the regex set
  doesn't recognize (e.g. `05-01-2024`, day-month-dash order with a
  2-digit-first token) are both missed.

Re-running `score_phi_redactor` against `evalsets/phi_labelled.jsonl`
must reproduce these exact numbers — `tests/test_packs_healthcare.py`
asserts it, so this README cannot silently drift from the code.

### Coverage

`NAME`, `ADDRESS` (geographic subdivision smaller than state), `DATE`
(all elements except year) and `AGE` (ages over 89), `PHONE`, `FAX`,
`EMAIL`, `SSN`, `MRN`, `HEALTH_PLAN`, `ACCOUNT`, `CERTIFICATE`,
`VEHICLE`, `DEVICE`, `URL`, `IP`, `BIOMETRIC` (textual mentions only),
`PHOTO` (textual mentions only), `OTHER_ID`.

Replacement tokens are category-stable within one `redact()` call:
the same original substring always maps to the same token
(`[NAME-1]`, `[NAME-1]` again, `[NAME-2]` for a different name, ...).

## Synthetic data generator

```python
from agenticcarekit.packs.healthcare import SyntheticGenerator

gen = SyntheticGenerator(seed=42)
patients = gen.patients(5)
encounters = gen.encounters(patients[0])
vitals = gen.vitals(encounters[0])
meds = gen.medications(patients[0])
transcripts = gen.intake_transcripts(20)
```

Same seed, same call sequence → byte-identical output. A different seed
→ different output. No real patients; all names/addresses/phone numbers
are drawn from curated word lists purely for plausibility.
