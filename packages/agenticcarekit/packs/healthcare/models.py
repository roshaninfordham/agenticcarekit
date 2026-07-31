"""FHIR-lite Pydantic models for the healthcare pack.

These are deliberately *not* a full FHIR R4 implementation. Real FHIR
resources are deeply nested (``Patient.name`` is ``list[HumanName]``,
``Patient.telecom`` is ``list[ContactPoint]``, ``Patient.address`` is
``list[Address]``, identifiers are typed ``Identifier`` objects with their
own coding systems). That generality buys correctness for a general-purpose
FHIR server and buys nothing for a pack whose job is to hand a blueprint
some plausible, typed clinical data.

Deviations from real FHIR, spelled out so nobody mistakes this for a
compliant resource server:

* **Flattened, singular fields instead of lists.** A ``Patient`` has one
  name, one phone, one address, one MRN — not the FHIR-correct
  ``list[...]`` of each with historical/use qualifiers. Field names carry
  a resource-shaped prefix (``name_given``, ``address_city``) instead of
  nesting, so the model stays flat and pragmatic.
* **``code`` stays FHIR-shaped.** Anywhere real FHIR uses a
  ``CodeableConcept``, this pack uses :class:`Coding` — ``{system, code,
  display}`` — dropping the ``text`` and multi-coding-per-concept
  generality FHIR allows.
* **``valueQuantity`` stays FHIR-shaped.** :class:`Quantity` is
  ``{value, unit}``, dropping FHIR's optional ``system``/``code`` UCUM
  qualifiers on the quantity itself (the unit string is UCUM already).
* **Dates and datetimes are ISO-8601 strings**, not the specific
  ``date``/``dateTime``/``instant`` XML/JSON types FHIR distinguishes.
  ``effectiveDateTime`` in particular is one of several choices FHIR
  allows for ``Observation.effective[x]`` (``effectivePeriod``,
  ``effectiveTiming`` also exist) — this pack only ever emits the
  ``effectiveDateTime`` shape.
* **References are bare strings** (``"Patient/<id>"``), not the FHIR
  ``Reference`` object with ``display``/``type``/versioned URLs.
* **No extensions, no meta, no narrative.** None of FHIR's extensibility
  points are modeled.

All models are frozen (immutable) and strict (no silent type coercion,
no unknown fields) — clinical records should not shape-shift after
construction.

Example:
    >>> p = Patient(
    ...     id="patient-0001",
    ...     mrn="MRN-100234",
    ...     name_family="Chen",
    ...     name_given="Wei",
    ...     gender="female",
    ...     birth_date="1985-03-15",
    ... )
    >>> p.id
    'patient-0001'
    >>> p.model_dump()["name_family"]
    'Chen'
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(frozen=True, strict=True, extra="forbid")


class Coding(BaseModel):
    """FHIR-shaped ``{system, code, display}`` triple, e.g. a LOINC or
    RxNorm code. Stands in for FHIR's ``CodeableConcept`` (simplified to
    exactly one coding, no ``text`` field).

    Example:
        >>> c = Coding(system="http://loinc.org", code="8310-5", display="Body temperature")
        >>> c.code
        '8310-5'
    """

    model_config = _STRICT

    system: str
    code: str
    display: str


class Quantity(BaseModel):
    """FHIR-shaped ``{value, unit}`` pair, e.g. a vital sign reading.

    Example:
        >>> Quantity(value=98.6, unit="degF").unit
        'degF'
    """

    model_config = _STRICT

    value: float
    unit: str


class Patient(BaseModel):
    """FHIR-lite ``Patient``. See module docstring for flattening
    deviations from real FHIR ``Patient``.

    Example:
        >>> Patient(
        ...     id="patient-0001", mrn="MRN-100234",
        ...     name_family="Chen", name_given="Wei",
        ...     gender="female", birth_date="1985-03-15",
        ... ).gender
        'female'
    """

    model_config = _STRICT

    id: str
    mrn: str
    name_family: str
    name_given: str
    gender: Literal["male", "female", "other", "unknown"]
    birth_date: str  # ISO 8601 date, e.g. "1985-03-15"
    phone: str | None = None
    address_line: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_postal_code: str | None = None


class Encounter(BaseModel):
    """FHIR-lite ``Encounter``. ``subject`` is a bare reference string
    (``"Patient/<id>"``), not a FHIR ``Reference`` object.

    Example:
        >>> e = Encounter(
        ...     id="encounter-0001", subject="Patient/patient-0001",
        ...     status="finished", class_code="AMB",
        ...     period_start="2024-05-01T09:00:00",
        ...     reason=Coding(system="http://snomed.info/sct", code="185389009",
        ...                   display="Follow-up encounter"),
        ... )
        >>> e.class_code
        'AMB'
    """

    model_config = _STRICT

    id: str
    subject: str
    status: Literal["planned", "in-progress", "finished", "cancelled"]
    class_code: str  # FHIR Encounter.class code, e.g. "AMB", "EMER", "IMP"
    period_start: str  # ISO 8601 datetime
    period_end: str | None = None
    reason: Coding | None = None


class Observation(BaseModel):
    """FHIR-lite ``Observation`` — used for vitals. ``effective_datetime``
    corresponds to FHIR's ``effectiveDateTime`` choice of
    ``Observation.effective[x]`` (the only choice this pack emits).

    Example:
        >>> o = Observation(
        ...     id="obs-0001", subject="Patient/patient-0001",
        ...     encounter="Encounter/encounter-0001", status="final",
        ...     code=Coding(system="http://loinc.org", code="8867-4", display="Heart rate"),
        ...     effective_datetime="2024-05-01T09:05:00",
        ...     value_quantity=Quantity(value=72, unit="/min"),
        ... )
        >>> o.value_quantity.value
        72.0
    """

    model_config = _STRICT

    id: str
    subject: str
    encounter: str | None = None
    status: Literal["registered", "preliminary", "final", "amended"]
    code: Coding
    effective_datetime: str  # ISO 8601 datetime
    value_quantity: Quantity


class DocumentReference(BaseModel):
    """FHIR-lite ``DocumentReference`` — used to attach free text (e.g. an
    intake transcript or note) to a patient/encounter. ``content_text``
    stands in for FHIR's ``content[].attachment.data`` (base64 payload) —
    here it is stored as plain text since this pack never leaves the
    process boundary raw.

    Example:
        >>> d = DocumentReference(
        ...     id="doc-0001", subject="Patient/patient-0001",
        ...     type=Coding(system="http://loinc.org", code="34117-2",
        ...                 display="History and physical note"),
        ...     date="2024-05-01T09:10:00", status="current",
        ...     content_text="Patient reports mild cough for three days.",
        ... )
        >>> d.status
        'current'
    """

    model_config = _STRICT

    id: str
    subject: str
    type: Coding
    date: str  # ISO 8601 datetime
    status: Literal["current", "superseded", "entered-in-error"]
    content_text: str


class MedicationStatement(BaseModel):
    """FHIR-lite ``MedicationStatement``. ``medication_code`` is an
    RxNorm-shaped :class:`Coding`; ``dosage_text`` stands in for FHIR's
    structured ``Dosage`` (this pack only emits the free-text form FHIR
    itself allows as ``dosage[].text``).

    Example:
        >>> m = MedicationStatement(
        ...     id="medstmt-0001", subject="Patient/patient-0001",
        ...     medication_code=Coding(system="http://www.nlm.nih.gov/research/umls/rxnorm",
        ...                            code="314076", display="Lisinopril 10 MG Oral Tablet"),
        ...     status="active", effective_start="2024-01-15",
        ...     dosage_text="10 mg by mouth once daily",
        ... )
        >>> m.status
        'active'
    """

    model_config = _STRICT

    id: str
    subject: str
    medication_code: Coding
    status: Literal["active", "completed", "stopped", "on-hold"]
    effective_start: str  # ISO 8601 date
    dosage_text: str
