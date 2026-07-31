"""W-F acceptance — the `_template` pack.

A pack interface with one implementation is not an interface (brief
§6 W-F). These tests exercise `_template` the same way a real pack
would be exercised: importable, satisfies the `Redactor` protocol,
manifest is well-formed and consistent with what the module exports.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import Redactor
from agenticcarekit.packs._template import Example, TemplateRedactor
from pydantic import ValidationError

TEMPLATE_DIR = Path(__file__).parent.parent / "packages" / "agenticcarekit" / "packs" / "_template"


def test_template_redactor_is_passthrough_and_satisfies_protocol():
    r = TemplateRedactor()
    assert isinstance(r, Redactor)
    assert r.name == "_template.none"
    clean, redactions = r.redact("Jane Doe, MRN 12345, DOB 1990-01-01")
    assert clean == "Jane Doe, MRN 12345, DOB 1990-01-01"
    assert redactions == []


def test_example_model_is_frozen_and_strict():
    ex = Example(id="example-0001", label="hello")
    assert ex.label == "hello"
    with pytest.raises(ValidationError):
        ex.label = "changed"


def test_manifest_is_well_formed_and_matches_exports():
    manifest_path = TEMPLATE_DIR / "manifest.toml"
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)

    assert manifest["pack"]["name"] == "_template"
    assert "description" in manifest["pack"]
    assert "version" in manifest["pack"]

    provides = manifest["provides"]
    assert provides["redactors"] == ["_template.none"]
    assert provides["models"] == ["Example"]
    assert provides["eval_sets"] == []

    # The redactor name declared in the manifest must match what the
    # exported class actually reports.
    assert TemplateRedactor.name == provides["redactors"][0]


def test_readme_documents_pack_authoring():
    readme = (TEMPLATE_DIR / "README.md").read_text()
    assert "manifest.toml" in readme
    assert "entry-point" in readme or "entry point" in readme
    assert "Redactor" in readme
