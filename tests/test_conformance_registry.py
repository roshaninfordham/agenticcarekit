"""Spec integrity: schemas, the error registry, and the corpus itself (W-J).

``docs/CONTRACTS.md``: *"New error codes are added to spec/errors.json first,
then raised in code. A code raised but not registered is a test failure
(W-J enforces)."* This module is that enforcement, plus the checks that keep
``spec/`` internally consistent — a source of truth that contradicts itself
is worse than none.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "spec"
SCHEMAS = SPEC / "schemas"
CASES = SPEC / "conformance" / "cases"
PACKAGE = REPO_ROOT / "packages" / "agenticcarekit"

#: Codes that legitimately never appear in ``spec/errors.json``.
UNREGISTERED_BY_DESIGN = {
    "E000": "AckError's default sentinel — 'an error with no registered code yet'",
    # Adapter-internal codes; they mark a gap in an implementation's surface,
    # not a product error a user can hit.
    "EADAPTER": "conformance adapter: implementation is missing a required surface",
    "EUNCAUGHT": "conformance adapter: an unexpected exception escaped",
}

_CODE_IN_SOURCE = re.compile(r'code\s*=\s*"(E\d{3})"')


def _registry() -> dict[str, Any]:
    return json.loads((SPEC / "errors.json").read_text(encoding="utf-8"))


def _registered_codes() -> set[str]:
    return {e["code"] for e in _registry()["errors"]}


def _iter_codes(node: Any):
    """Every ``{"error": {"code": ...}}`` anywhere in a case document."""
    if isinstance(node, dict):
        err = node.get("error")
        if isinstance(err, dict) and "code" in err:
            yield err["code"]
        for value in node.values():
            yield from _iter_codes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_codes(value)


# ── the error registry ───────────────────────────────────────────────────


def test_errors_json_validates_against_its_schema() -> None:
    schema = json.loads((SCHEMAS / "errors.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(_registry(), schema)


def test_error_codes_are_unique() -> None:
    codes = [e["code"] for e in _registry()["errors"]]
    duplicates = [code for code, n in Counter(codes).items() if n > 1]
    assert not duplicates, f"duplicate error codes in spec/errors.json: {duplicates}"


def test_every_code_belongs_to_a_declared_range() -> None:
    ranges = set(_registry()["ranges"])
    for code in _registered_codes():
        assert f"E{code[1]}xx" in ranges, f"{code} has no declared range"


def test_fixture_error_codes_are_registered() -> None:
    """A fixture may only expect a code the registry explains.

    Otherwise the suite would demand behaviour that `ack explain` cannot
    describe — an error nobody can act on.
    """
    registered = _registered_codes()
    for path in sorted(CASES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        unknown = {c for c in _iter_codes(doc) if c not in registered}
        assert not unknown, f"{path.name} expects unregistered codes: {sorted(unknown)}"


def test_codes_raised_in_the_implementation_are_registered() -> None:
    """Every ``code="Exxx"`` in the package is explained in the registry.

    Doctest lines (``>>>`` / ``...``) are documentation, not raise sites, so
    they are excluded — an example may cite a deliberately fictional code.
    """
    registered = _registered_codes() | set(UNREGISTERED_BY_DESIGN)
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith((">>>", "...")):
                continue
            for code in _CODE_IN_SOURCE.findall(line):
                if code not in registered:
                    offenders.setdefault(code, []).append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "codes raised but not registered in spec/errors.json "
        "(add the entry first, then raise it): " + json.dumps(offenders, indent=2, sort_keys=True)
    )


# ── the schemas ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*.json")), ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", (
        f"{path.name} must declare draft 2020-12 — the version every implementation targets"
    )
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*.json")), ids=lambda p: p.name)
def test_schema_declares_an_id_and_title(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema.get("$id", "").endswith(path.name), f"{path.name}: $id must match the filename"
    assert schema.get("title"), f"{path.name}: every schema names the contract it describes"


# ── the corpus ───────────────────────────────────────────────────────────


def test_case_ids_are_unique_across_the_corpus() -> None:
    ids: list[str] = []
    for path in sorted(CASES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        ids += [c["id"] for c in doc["cases"]]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    assert not duplicates, f"duplicate case ids: {duplicates}"


@pytest.mark.parametrize("path", sorted(CASES.glob("*.json")), ids=lambda p: p.name)
def test_case_file_is_well_formed(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc.get("suite"), f"{path.name}: missing suite name"
    assert doc.get("spec_version"), f"{path.name}: cases are versioned with the spec"
    assert doc["cases"], f"{path.name}: an empty suite proves nothing"
    for case in doc["cases"]:
        assert set(case) <= {"id", "area", "note", "input", "expected"}, (
            f"{case.get('id')}: unknown keys {sorted(set(case) - {'id', 'area', 'note', 'input', 'expected'})}"
        )
        assert case["area"] == doc["suite"], f"{case['id']}: area must match the suite name"
        assert isinstance(case["input"], dict) and isinstance(case["expected"], dict)
        assert case["id"].startswith(("mb-", "cn-", "pol-", "tr-", "cfg-")), (
            f"{case['id']}: ids carry an area prefix so failures are readable out of context"
        )


def test_trace_fixtures_agree_with_the_trace_schema() -> None:
    """Every trace fixture marked valid really is, and every one marked
    invalid really is not — the corpus is checked against the schema it
    exists to exercise."""
    schema = json.loads((SCHEMAS / "trace-event.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    doc = json.loads((CASES / "trace-shape.json").read_text(encoding="utf-8"))
    for case in doc["cases"]:
        if "event" not in case["input"]:
            continue
        ok = validator.is_valid(case["input"]["event"])
        assert ok is case["expected"]["valid"], f"{case['id']}: schema disagrees with the fixture"


def test_capabilities_fixtures_match_the_capabilities_schema() -> None:
    """Capability inputs are real Capabilities documents, not a parallel
    shape that happens to work."""
    schema = json.loads((SCHEMAS / "capabilities.schema.json").read_text(encoding="utf-8"))
    doc = json.loads((CASES / "capability-negotiation.json").read_text(encoding="utf-8"))
    for case in doc["cases"]:
        jsonschema.validate(case["input"]["capabilities"], schema)
