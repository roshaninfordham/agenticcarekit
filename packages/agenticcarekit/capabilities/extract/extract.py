"""Structured extraction with schema validation and exactly one repair retry.

Flow: prompt the model (prompt loaded from a ``.md`` file — never a string
literal), parse and validate the JSON response against a pydantic schema.
On validation failure, exactly one repair request is sent — carrying the
validation errors and the malformed output verbatim. If the repair also
fails validation, the function raises ``AckError`` (code ``E504``) summarising
both failures. It never loops more than twice, and it never raises for any
reason other than a doubly-failed validation.

Example:
    >>> import pydantic
    >>> from agenticcarekit.kernel.contracts import GenerateResponse
    >>> class Person(pydantic.BaseModel):
    ...     name: str
    ...     age: int
    >>> class StubProvider:
    ...     name = "stub"
    ...     def capabilities(self):
    ...         raise NotImplementedError
    ...     def generate(self, req):
    ...         return GenerateResponse(text='{"name": "Ada", "age": 36}')
    ...     def stream(self, req):
    ...         raise NotImplementedError
    >>> person = extract(StubProvider(), Person, "Ada is 36 years old.")
    >>> (person.name, person.age)
    ('Ada', 36)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pydantic

from agenticcarekit.kernel.contracts import (
    AckError,
    EgressClass,
    GenerateRequest,
    Message,
    Provider,
    TraceEvent,
)

__all__ = ["extract"]

M = TypeVar("M", bound=pydantic.BaseModel)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_DEFAULT_PROMPT_PATH = _PROMPTS_DIR / "extract.md"
_REPAIR_PROMPT_PATH = _PROMPTS_DIR / "repair.md"


class _MalformedOutput(Exception):
    """Internal: carries the raw (unparseable or schema-invalid) response
    text plus a human-readable description of what went wrong."""

    def __init__(self, raw: str, errors: str) -> None:
        super().__init__(errors)
        self.raw = raw
        self.errors = errors


def _strip_fences(text: str) -> str:
    """Remove a leading/trailing ``` ... ``` markdown fence, if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_span(text: str) -> str:
    """Tolerate leading/trailing prose by slicing from the first ``{`` to
    the last ``}``. Validation downstream stays strict — this only widens
    what we *attempt* to parse."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _parse(text: str, schema: type[M]) -> M:
    candidate = _extract_json_span(_strip_fences(text))
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise _MalformedOutput(text, f"invalid JSON: {exc}") from exc
    try:
        return schema.model_validate(data)
    except pydantic.ValidationError as exc:
        raise _MalformedOutput(text, str(exc)) from exc


def extract(
    provider: Provider,
    schema: type[M],
    text: str,
    *,
    prompt_path: Path | None = None,
    emit: Callable[[TraceEvent], None] | None = None,
) -> M:
    """Extract ``schema`` from ``text`` using ``provider``, with one repair retry.

    Raises:
        AckError E504: the repair attempt also failed validation. Details
            carry both the first and second raw responses and error strings.
    """
    template = (prompt_path or _DEFAULT_PROMPT_PATH).read_text(encoding="utf-8")
    schema_json = json.dumps(schema.model_json_schema(), indent=2, sort_keys=True)
    prompt = template.replace("{schema_json}", schema_json).replace("{text}", text)

    run_id = uuid.uuid4().hex

    first_response = provider.generate(GenerateRequest(messages=(Message.text("user", prompt),)))
    _emit_model_event(emit, run_id, first_response.model if first_response.model else "")

    try:
        return _parse(first_response.text, schema)
    except _MalformedOutput as first_failure:
        repair_template = _REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
        repair_prompt = (
            repair_template.replace("{schema_json}", schema_json)
            .replace("{text}", text)
            .replace("{malformed}", first_failure.raw)
            .replace("{errors}", first_failure.errors)
        )
        second_response = provider.generate(
            GenerateRequest(messages=(Message.text("user", repair_prompt),))
        )
        _emit_model_event(emit, run_id, second_response.model if second_response.model else "")

        try:
            return _parse(second_response.text, schema)
        except _MalformedOutput as second_failure:
            _emit_error_event(emit, run_id)
            raise AckError(
                "structured extraction failed validation after one repair attempt",
                code="E504",
                why=(
                    f"first attempt: {first_failure.errors}\n"
                    f"repair attempt: {second_failure.errors}"
                ),
                fix="inspect the raw model output in `details` and adjust the schema or prompt",
                details={
                    "first_raw": first_failure.raw,
                    "first_errors": first_failure.errors,
                    "second_raw": second_failure.raw,
                    "second_errors": second_failure.errors,
                },
            ) from second_failure


def _emit_model_event(
    emit: Callable[[TraceEvent], None] | None, run_id: str, model: str
) -> None:
    if emit is None:
        return
    emit(
        TraceEvent(
            ts=time.time(),
            run_id=run_id,
            span_id=uuid.uuid4().hex,
            parent_span_id=None,
            kind="model",
            egress=EgressClass.DEVICE,
            bytes_out=0,
            payload={"model": model},
        )
    )


def _emit_error_event(emit: Callable[[TraceEvent], None] | None, run_id: str) -> None:
    if emit is None:
        return
    emit(
        TraceEvent(
            ts=time.time(),
            run_id=run_id,
            span_id=uuid.uuid4().hex,
            parent_span_id=None,
            kind="error",
            egress=EgressClass.DEVICE,
            bytes_out=0,
            payload={"code": "E504", "attempts": 2},
        )
    )

