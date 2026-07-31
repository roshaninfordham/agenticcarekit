"""Template pack models.

A real pack puts its domain's Pydantic models here (see
``agenticcarekit.packs.healthcare.models`` for a fully worked example:
FHIR-lite clinical resources, frozen and strict). This template ships
exactly one placeholder model so the pack is importable and its shape
is obvious at a glance.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Example(BaseModel):
    """Placeholder domain model. Replace with your domain's types.

    Example:
        >>> Example(id="example-0001", label="hello").label
        'hello'
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: str
    label: str
