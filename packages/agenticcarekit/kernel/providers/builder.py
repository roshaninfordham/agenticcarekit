"""The canonical message builder — every Gemma 4 quirk, applied once.

``build_ollama_chat`` is the single quirk-application point in the toolkit
(``docs/CONTRACTS.md`` → "Canonical message build"). Its output is the exact
Ollama ``/api/chat`` payload and the conformance suite asserts it
byte-for-byte as sorted-key JSON. Other providers map *from* this payload
(see ``openai_compat.py``) so the quirks are never re-implemented.

The six rules, in order:

1. Sampling defaults ``1.0 / 0.95 / 64`` land in ``options``; request
   overrides win. ``max_tokens`` → ``options.num_predict``, ``stop`` →
   ``options.stop``. Context is never sent — the model declares it.
2. ``think=True`` prepends ``<|think|>`` to the system message, creating one
   if the conversation has none. Exactly once, at the start.
3. ``Message.thinking`` is never serialized, for any turn. Prior-turn
   thought blocks in history are a silent correctness bug; keeping thinking
   outside ``parts`` makes the stripping structural rather than regex work.
4. Images and audio go to the ``images`` / ``audio`` arrays (Ollama places
   them before text); multiple text parts join with ``"\\n\\n"``.
5. ``ImagePart.detail`` maps to ``options.vision_tokens`` via
   ``VISION_TOKEN_BUDGETS``; across several images the highest preset wins.
6. ``bytes`` data is base64-encoded; a ``str`` that names an existing file is
   read and encoded; any other ``str`` is assumed to be base64 already.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import (
    VISION_TOKEN_BUDGETS,
    AudioPart,
    GenerateRequest,
    ImagePart,
    Message,
    TextPart,
)

__all__ = [
    "GEMMA4_SAMPLING",
    "THINK_TOKEN",
    "apply_think",
    "build_ollama_chat",
    "encode_media",
    "sampling_options",
    "split_thinking",
]

#: Gemma 4's known-good sampling defaults (brief §2, quirk 1). ``None`` on a
#: request means "use these"; a value means the caller overrode deliberately.
GEMMA4_SAMPLING: dict[str, float | int] = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 64,
}

#: Thinking is enabled by this token at the start of the system prompt
#: (brief §2, quirk 2) — not by a sampling flag.
THINK_TOKEN = "<|think|>"

#: Closing counterpart, emitted by the model when it thinks inline.
_THINK_CLOSE = "<|/think|>"


def encode_media(data: bytes | str) -> str:
    """Normalise image/audio payload data to base64 (rule 6).

    ``bytes`` are encoded; a ``str`` naming an existing file is read from
    disk and encoded; any other ``str`` is assumed to be base64 already and
    passed through untouched. Guessing wrong in the last case is the caller's
    to fix — silently re-encoding a base64 string would corrupt it.

    Example:
        >>> encode_media(b"hi")
        'aGk='
        >>> encode_media("aGk=")
        'aGk='
    """
    if isinstance(data, bytes):
        return base64.b64encode(data).decode("ascii")
    try:
        path = Path(data)
        if path.is_file():
            return base64.b64encode(path.read_bytes()).decode("ascii")
    except (OSError, ValueError):
        # Long base64 blobs blow past filename limits on some platforms —
        # that just means it was never a path.
        pass
    return data


def split_thinking(content: str, thinking: str | None = None) -> tuple[str, str | None]:
    """Separate an inline ``<|think|>`` block from response text.

    Ollama returns thinking in its own field for most builds, but Gemma 4
    will emit the block inline when the token is echoed. Either way callers
    get ``(text, thinking)`` with the thought block out of the text — the
    same separation ``Message`` enforces structurally.

    Example:
        >>> split_thinking("<|think|>weigh it<|/think|>Answer: 4")
        ('Answer: 4', 'weigh it')
        >>> split_thinking("plain", "sidecar thought")
        ('plain', 'sidecar thought')
    """
    text = content
    thought = thinking
    start = text.find(THINK_TOKEN)
    if start != -1:
        end = text.find(_THINK_CLOSE, start)
        if end == -1:
            inline = text[start + len(THINK_TOKEN) :]
            text = text[:start]
        else:
            inline = text[start + len(THINK_TOKEN) : end]
            text = text[:start] + text[end + len(_THINK_CLOSE) :]
        inline = inline.strip()
        if inline:
            thought = f"{thought}\n{inline}" if thought else inline
    return text.strip(), thought


def _serialize_message(msg: Message) -> dict[str, Any]:
    """One conversation turn in Ollama's wire shape (rules 3 and 4).

    ``thinking`` is never written. Images and audio become their own arrays;
    text parts join with a blank line.

    Example:
        >>> _serialize_message(Message("assistant", (TextPart("done"),),
        ...                            thinking="secret deliberation"))
        {'role': 'assistant', 'content': 'done'}
    """
    texts: list[str] = []
    images: list[str] = []
    audio: list[str] = []
    for part in msg.parts:
        if isinstance(part, TextPart):
            texts.append(part.text)
        elif isinstance(part, ImagePart):
            images.append(encode_media(part.data))
        elif isinstance(part, AudioPart):
            audio.append(encode_media(part.data))

    out: dict[str, Any] = {"role": msg.role, "content": "\n\n".join(texts)}
    # Rule 4: media arrays are what Ollama renders ahead of the text.
    if images:
        out["images"] = images
    if audio:
        out["audio"] = audio
    if msg.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in msg.tool_calls
        ]
    if msg.role == "tool" and msg.tool_call_id:
        # Ollama identifies tool results by name, not by call id.
        out["tool_name"] = msg.tool_call_id
    # Rule 3: msg.thinking is deliberately absent from `out`. Do not add it.
    return out


def _vision_tokens(req: GenerateRequest) -> int | None:
    """Highest vision-token preset across every image in the request (rule 5).

    Example:
        >>> req = GenerateRequest(messages=(Message("user", (
        ...     ImagePart(b"a", detail="caption"), ImagePart(b"b", detail="ocr"))),))
        >>> _vision_tokens(req)
        1120
    """
    budgets = [
        VISION_TOKEN_BUDGETS.get(part.detail, VISION_TOKEN_BUDGETS["default"])
        for msg in req.messages
        for part in msg.parts
        if isinstance(part, ImagePart)
    ]
    return max(budgets) if budgets else None


def sampling_options(req: GenerateRequest) -> dict[str, Any]:
    """Sampling and decode options (rules 1 and 5).

    Example:
        >>> sampling_options(GenerateRequest(messages=(), temperature=0.2))["temperature"]
        0.2
        >>> sampling_options(GenerateRequest(messages=()))["top_k"]
        64
    """
    opts: dict[str, Any] = {
        "temperature": (
            req.temperature if req.temperature is not None else GEMMA4_SAMPLING["temperature"]
        ),
        "top_p": req.top_p if req.top_p is not None else GEMMA4_SAMPLING["top_p"],
        "top_k": req.top_k if req.top_k is not None else GEMMA4_SAMPLING["top_k"],
    }
    if req.max_tokens is not None:
        opts["num_predict"] = req.max_tokens
    if req.stop:
        opts["stop"] = list(req.stop)
    vision = _vision_tokens(req)
    if vision is not None:
        opts["vision_tokens"] = vision
    # Context length is NOT sent: the model declares its window, the runtime
    # negotiates against that declaration.
    return opts


def apply_think(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend ``<|think|>`` to the system prompt, exactly once (rule 2).

    Example:
        >>> apply_think([{"role": "user", "content": "hi"}])[0]
        {'role': 'system', 'content': '<|think|>'}
        >>> apply_think([{"role": "system", "content": "<|think|>set"}])[0]["content"]
        '<|think|>set'
    """
    for m in messages:
        if m["role"] == "system":
            if not str(m["content"]).startswith(THINK_TOKEN):
                m["content"] = THINK_TOKEN + str(m["content"])
            return messages
    return [{"role": "system", "content": THINK_TOKEN}, *messages]


def _function_schema(t: Any) -> dict[str, Any]:
    """Function-calling declaration for a ``ToolSpec`` or a ``@tool``-wrapped
    callable — both are what callers actually have in hand.

    Example:
        >>> from agenticcarekit.kernel.contracts import tool
        >>> @tool(mock=lambda q: "x")
        ... def look_up(q: str) -> str:
        ...     '''Look something up.'''
        ...     return q
        >>> _function_schema(look_up)["function"]["name"]
        'look_up'
        >>> _function_schema(look_up.spec)["function"]["name"]
        'look_up'
    """
    spec = getattr(t, "spec", t)
    return spec.as_function_schema()


def build_ollama_chat(req: GenerateRequest, model: str) -> dict[str, Any]:
    """Build the exact Ollama ``/api/chat`` payload for a request.

    This is the one place Gemma 4's quirks are applied. Callers never pass
    sampling defaults, never inject the think token, and never strip history
    thought blocks — doing any of that at a call site is the bug this
    function exists to prevent.

    Example:
        >>> from agenticcarekit.kernel.contracts import Message
        >>> req = GenerateRequest(
        ...     messages=(Message.text("system", "You are a scribe."),
        ...               Message("assistant", (TextPart("ok"),), thinking="hidden"),
        ...               Message.text("user", "summarise")),
        ...     think=True, temperature=0.3)
        >>> payload = build_ollama_chat(req, "gemma4:e4b")
        >>> payload["messages"][0]["content"]
        '<|think|>You are a scribe.'
        >>> "hidden" in str(payload)
        False
        >>> payload["options"]
        {'temperature': 0.3, 'top_p': 0.95, 'top_k': 64}
        >>> payload["stream"]
        False
    """
    messages = [_serialize_message(m) for m in req.messages]
    if req.think:
        messages = apply_think(messages)

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "options": sampling_options(req),
        "stream": False,
    }
    if req.tools:
        payload["tools"] = [_function_schema(t) for t in req.tools]
    return payload
