"""Acceptance tests for the tool-calling agent loop (W-E)."""

from __future__ import annotations

import threading

import pytest
from agenticcarekit.capabilities.agents import AgentLoop
from agenticcarekit.kernel.contracts import (
    GenerateRequest,
    GenerateResponse,
    ToolCall,
    tool,
)

# ── tools with real fn + mock, distinguishable by side effects ────────────

_real_calls: list[tuple] = []
_mock_calls: list[tuple] = []


def _mock_get_weather(city: str) -> str:
    _mock_calls.append((city,))
    return f"mock-sunny-{city}"


@tool(permissions={"network"}, mock=_mock_get_weather)
def get_weather(city: str) -> str:
    """Look up the weather for a city."""
    _real_calls.append((city,))
    return f"real-sunny-{city}"


def _mock_add(a: int, b: int) -> int:
    return a + b


@tool(mock=_mock_add)
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b + 1000  # deliberately different from the mock


class _ScriptedProvider:
    """A provider stub scripted with a fixed sequence of responses."""

    name = "scripted"

    def __init__(self, responses: list[GenerateResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def capabilities(self):
        raise NotImplementedError

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("scripted provider ran out of responses")
        return self._responses.pop(0)

    def stream(self, req):
        raise NotImplementedError


class _AlwaysToolCallsProvider:
    """Never answers in plain text — used to exercise the step budget."""

    name = "always-tools"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        raise NotImplementedError

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.calls += 1
        call = ToolCall(id=str(self.calls), name="add_numbers", arguments={"a": 1, "b": 2})
        return GenerateResponse(text="", tool_calls=(call,))

    def stream(self, req):
        raise NotImplementedError


def setup_function(_fn) -> None:
    _real_calls.clear()
    _mock_calls.clear()


def test_offline_dispatches_mock_not_real_fn():
    call = ToolCall(id="1", name="get_weather", arguments={"city": "NYC"})
    provider = _ScriptedProvider(
        [
            GenerateResponse(text="", tool_calls=(call,)),
            GenerateResponse(text="It is sunny in NYC."),
        ]
    )
    loop = AgentLoop(provider, [get_weather], offline=True)

    result = loop.run("what's the weather in NYC?")

    assert result.stopped_reason == "done"
    assert result.final_text == "It is sunny in NYC."
    assert _mock_calls == [("NYC",)]
    assert _real_calls == []  # real fn must never be called in offline mode
    assert provider.calls == 2


def test_online_dispatches_real_fn_not_mock():
    call = ToolCall(id="1", name="get_weather", arguments={"city": "NYC"})
    provider = _ScriptedProvider(
        [
            GenerateResponse(text="", tool_calls=(call,)),
            GenerateResponse(text="done"),
        ]
    )
    loop = AgentLoop(provider, [get_weather], offline=False)

    result = loop.run("weather?")

    assert result.stopped_reason == "done"
    assert _real_calls == [("NYC",)]
    assert _mock_calls == []


def test_unknown_tool_feeds_error_back_without_crashing():
    bad_call = ToolCall(id="1", name="nonexistent_tool", arguments={})
    provider = _ScriptedProvider(
        [
            GenerateResponse(text="", tool_calls=(bad_call,)),
            GenerateResponse(text="recovered"),
        ]
    )
    loop = AgentLoop(provider, [get_weather], offline=True)

    result = loop.run("do something weird")

    assert result.stopped_reason == "done"
    assert result.final_text == "recovered"
    assert provider.calls == 2


def test_budget_exhaustion_stops_without_raising():
    provider = _AlwaysToolCallsProvider()
    loop = AgentLoop(provider, [add_numbers], max_steps=3, offline=True)

    result = loop.run("keep calling tools forever")

    assert result.stopped_reason == "budget"
    assert provider.calls == 3
    assert len(result.steps) == 3


def test_cancellation_stops_before_any_generate_call():
    provider = _AlwaysToolCallsProvider()
    cancel = threading.Event()
    cancel.set()
    loop = AgentLoop(provider, [add_numbers], offline=True)

    result = loop.run("hello", cancel=cancel)

    assert result.stopped_reason == "cancelled"
    assert provider.calls == 0
    assert result.steps == []


def test_tool_step_emits_trace_event_with_expected_payload():
    events = []
    call = ToolCall(id="1", name="get_weather", arguments={"city": "SF"})
    provider = _ScriptedProvider(
        [
            GenerateResponse(text="", tool_calls=(call,)),
            GenerateResponse(text="ok"),
        ]
    )
    loop = AgentLoop(provider, [get_weather], offline=True, emit=events.append)

    loop.run("weather in SF?")

    assert len(events) == 1
    event = events[0]
    assert event.kind == "tool"
    assert event.bytes_out == 0
    assert event.payload == {
        "tool": "get_weather",
        "permissions": ["network"],
        "mock": True,
    }


def test_run_accepts_message_history():
    from agenticcarekit.kernel.contracts import Message

    provider = _ScriptedProvider([GenerateResponse(text="answer")])
    loop = AgentLoop(provider, [])

    result = loop.run([Message.text("user", "hi"), Message.text("assistant", "hey")])

    assert result.stopped_reason == "done"
    assert result.final_text == "answer"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
