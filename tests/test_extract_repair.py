"""Acceptance tests for structured extraction with exactly-one repair retry (W-E)."""

from __future__ import annotations

import pydantic
import pytest
from agenticcarekit.capabilities.extract import extract
from agenticcarekit.kernel.contracts import AckError, GenerateRequest, GenerateResponse


class Patient(pydantic.BaseModel):
    name: str
    age: int


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    def capabilities(self):
        raise NotImplementedError

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.calls += 1
        if not self._texts:
            raise AssertionError("scripted provider ran out of responses")
        return GenerateResponse(text=self._texts.pop(0))

    def stream(self, req):
        raise NotImplementedError


def test_repairs_once_after_malformed_first_response():
    provider = _ScriptedProvider(
        [
            "not json at all, sorry",
            '{"name": "Ada Lovelace", "age": 36}',
        ]
    )

    result = extract(provider, Patient, "Ada Lovelace is 36 years old.")

    assert result == Patient(name="Ada Lovelace", age=36)
    assert provider.calls == 2


def test_two_malformed_responses_raise_cleanly_not_a_loop():
    provider = _ScriptedProvider(
        [
            "still not json",
            "also not json, even after the repair prompt",
        ]
    )

    with pytest.raises(AckError) as excinfo:
        extract(provider, Patient, "Ada Lovelace is 36 years old.")

    assert excinfo.value.code == "E504"
    assert provider.calls == 2  # exactly two calls — never more, never a loop


def test_second_malformed_response_details_carry_both_failures():
    provider = _ScriptedProvider(["bad #1", "bad #2"])

    with pytest.raises(AckError) as excinfo:
        extract(provider, Patient, "text")

    details = excinfo.value.details
    assert "bad #1" in details["first_raw"]
    assert "bad #2" in details["second_raw"]


def test_markdown_fences_and_surrounding_prose_are_tolerated_on_first_try():
    provider = _ScriptedProvider(
        [
            'Here you go:\n```json\n{"name": "Grace Hopper", "age": 85}\n```\nHope that helps!',
        ]
    )

    result = extract(provider, Patient, "Grace Hopper is 85.")

    assert result == Patient(name="Grace Hopper", age=85)
    assert provider.calls == 1  # no repair needed


def test_emit_receives_model_events_and_error_event_on_failure():
    events = []
    provider = _ScriptedProvider(["bad", "still bad"])

    with pytest.raises(AckError):
        extract(provider, Patient, "text", emit=events.append)

    kinds = [e.kind for e in events]
    assert kinds.count("model") == 2
    assert kinds.count("error") == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
