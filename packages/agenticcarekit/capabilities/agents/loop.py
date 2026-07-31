"""Tool-calling agent loop with a step budget and cooperative cancellation.

The loop sends the running message history to the provider, dispatches any
requested tool calls, and repeats until the model answers in plain text, the
step budget is exhausted, or the caller cancels. It never raises on budget
exhaustion — that is a normal, reportable stop condition (``stopped_reason``),
not an error.

Offline mode (``offline=True``) dispatches every tool call to ``spec.mock``
instead of ``spec.fn``. Per Contract 3, that swap happens here — in the agent
loop — never inside ``Tool`` itself.

Example:
    >>> from agenticcarekit.kernel.contracts import (
    ...     GenerateRequest, GenerateResponse, ToolCall, tool,
    ... )
    >>> def mock_add(a: int, b: int) -> int:
    ...     return 7
    >>> @tool(mock=mock_add)
    ... def add(a: int, b: int) -> int:
    ...     '''Add two integers.'''
    ...     return a + b
    >>> class ScriptedProvider:
    ...     name = "scripted"
    ...     def __init__(self):
    ...         self.calls = 0
    ...     def capabilities(self):
    ...         raise NotImplementedError
    ...     def generate(self, req):
    ...         self.calls += 1
    ...         if self.calls == 1:
    ...             call = ToolCall(id="1", name="add", arguments={"a": 3, "b": 4})
    ...             return GenerateResponse(text="", tool_calls=(call,))
    ...         return GenerateResponse(text="The answer is 7.")
    ...     def stream(self, req):
    ...         raise NotImplementedError
    >>> loop = AgentLoop(ScriptedProvider(), [add], offline=True)
    >>> result = loop.run("what is 3 + 4?")
    >>> result.stopped_reason
    'done'
    >>> result.final_text
    'The answer is 7.'
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agenticcarekit.kernel.contracts import (
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Message,
    Provider,
    TextPart,
    Tool,
    ToolCall,
    TraceEvent,
)

__all__ = ["AgentLoop", "AgentResult", "StepRecord"]

StoppedReason = Literal["done", "budget", "cancelled"]


@dataclass(frozen=True)
class StepRecord:
    """One step of the loop: the model response and any tool dispatches.

    ``tool_results`` pairs each dispatched ``ToolCall`` with the stringified
    result (or error string) fed back to the model.
    """

    index: int
    response: GenerateResponse
    tool_results: tuple[tuple[ToolCall, str], ...] = ()


@dataclass(frozen=True)
class AgentResult:
    """Outcome of a full ``AgentLoop.run()``."""

    final_text: str
    steps: list[StepRecord]
    stopped_reason: StoppedReason


def _stringify(result: Any) -> str:
    """Best-effort text form of a tool's return value for feeding back into
    the conversation as a ``tool`` message."""
    if isinstance(result, str):
        return result
    return repr(result)


class AgentLoop:
    """Drives a provider through a tool-calling loop.

    Example:
        >>> from agenticcarekit.kernel.contracts import GenerateResponse
        >>> class EchoProvider:
        ...     name = "echo"
        ...     def capabilities(self):
        ...         raise NotImplementedError
        ...     def generate(self, req):
        ...         return GenerateResponse(text="hi back")
        ...     def stream(self, req):
        ...         raise NotImplementedError
        >>> AgentLoop(EchoProvider(), []).run("hi").final_text
        'hi back'
    """

    def __init__(
        self,
        provider: Provider,
        tools: list[Tool],
        *,
        max_steps: int = 8,
        offline: bool = False,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = {t.spec.name: t for t in tools}
        self.max_steps = max_steps
        self.offline = offline
        self.emit = emit
        self._egress = EgressClass.DEVICE

    def run(
        self,
        user_text: str | Sequence[Message],
        *,
        cancel: threading.Event | None = None,
    ) -> AgentResult:
        """Run the loop to completion, a step budget, or cancellation.

        Never raises on a normal stop condition — ``stopped_reason`` tells
        the caller what happened.
        """
        if isinstance(user_text, str):
            history: list[Message] = [Message.text("user", user_text)]
        else:
            history = list(user_text)

        run_id = uuid.uuid4().hex
        steps: list[StepRecord] = []
        last_text = ""

        for step_index in range(self.max_steps):
            if cancel is not None and cancel.is_set():
                return AgentResult(
                    final_text=last_text, steps=steps, stopped_reason="cancelled"
                )

            req = GenerateRequest(
                messages=tuple(history),
                tools=tuple(t.spec for t in self.tools.values()),
            )
            response = self.provider.generate(req)
            last_text = response.text

            if not response.tool_calls:
                steps.append(StepRecord(index=step_index, response=response))
                return AgentResult(
                    final_text=response.text, steps=steps, stopped_reason="done"
                )

            history.append(
                Message(
                    role="assistant",
                    parts=(TextPart(response.text),) if response.text else (),
                    tool_calls=response.tool_calls,
                )
            )

            tool_results: list[tuple[ToolCall, str]] = []
            for call in response.tool_calls:
                result_text = self._dispatch(call, run_id)
                tool_results.append((call, result_text))
                history.append(
                    Message(
                        role="tool",
                        parts=(TextPart(result_text),),
                        tool_call_id=call.id,
                    )
                )

            steps.append(
                StepRecord(index=step_index, response=response, tool_results=tuple(tool_results))
            )

        return AgentResult(final_text=last_text, steps=steps, stopped_reason="budget")

    def _dispatch(self, call: ToolCall, run_id: str) -> str:
        """Dispatch one requested tool call. Unknown tool names never crash
        the loop — they feed an error result back to the model."""
        tool_obj = self.tools.get(call.name)
        if tool_obj is None:
            self._emit_event(
                run_id,
                payload={"tool": call.name, "permissions": [], "mock": self.offline},
            )
            return f"error: unknown tool '{call.name}'"

        fn = tool_obj.spec.mock if self.offline else tool_obj.spec.fn
        self._emit_event(
            run_id,
            payload={
                "tool": tool_obj.spec.name,
                "permissions": sorted(tool_obj.spec.permissions),
                "mock": self.offline,
            },
        )
        try:
            result = fn(**call.arguments)
        except Exception as exc:  # noqa: BLE001 - fed back to the model, not raised
            return f"error: {exc}"
        return _stringify(result)

    def _emit_event(self, run_id: str, *, payload: dict[str, Any]) -> None:
        if self.emit is None:
            return
        self.emit(
            TraceEvent(
                ts=time.time(),
                run_id=run_id,
                span_id=uuid.uuid4().hex,
                parent_span_id=None,
                kind="tool",
                egress=self._egress,
                bytes_out=0,
                payload=payload,
            )
        )
