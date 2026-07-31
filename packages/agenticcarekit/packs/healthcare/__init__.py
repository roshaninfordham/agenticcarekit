"""Healthcare pack (W-F): FHIR-lite models, PHI redactor, synthetic data
generator, clinical eval sets, and PHI-redaction scoring.

Domain is a pack, not the architecture (brief invariant 8) — this package
is a normal Python package discoverable via the ``agenticcarekit.packs``
entry-point group (see ``pyproject.toml``), not a special-cased import.
"""

from .models import (
    Coding,
    DocumentReference,
    Encounter,
    MedicationStatement,
    Observation,
    Patient,
    Quantity,
)
from .phi import PHIRedactor
from .scoring import score_phi_redactor
from .synthetic import SyntheticGenerator

__all__ = [
    "Coding",
    "DocumentReference",
    "Encounter",
    "MedicationStatement",
    "Observation",
    "PHIRedactor",
    "Patient",
    "Quantity",
    "SyntheticGenerator",
    "score_phi_redactor",
]
