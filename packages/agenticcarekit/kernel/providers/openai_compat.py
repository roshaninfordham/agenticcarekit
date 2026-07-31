"""OpenAI-compatible chat provider — the same quirks, a different wire format.

Every sampling default, the ``<|think|>`` prefix, history hygiene and the
media-before-text ordering come from ``builder.py``; this module only maps
them onto the OpenAI ``/chat/completions`` schema. Re-deriving a quirk here
would create exactly the parallel implementation the brief forbids.

The API key is read from the environment **at call time** and never stored on
the instance, never logged, and never placed in an error payload — presence
is a boolean, the value is nobody's business (brief §7.1).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx

from agenticcarekit.kernel.contracts import (
    AckError,
    AudioPart,
    Capabilities,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    ImagePart,
    Message,
    Modality,
    TextPart,
    ToolCall,
    Usage,
)

from .builder import (
    _function_schema,
    apply_think,
    encode_media,
    sampling_options,
    split_thinking,
)
from .models import ensure_supported

__all__ = ["OpenAICompatibleProvider", "build_openai_chat"]

#: What a hosted OpenAI-compatible Gemma 4 endpoint is assumed to do when the
#: caller does not declare otherwise. Text in, text out — the honest floor.
#: Declare richer capabilities explicitly; never infer them from a URL.
DEFAULT_HOSTED = Capabilities(
    modalities_in=frozenset({Modality.TEXT}),
    modalities_out=frozenset({Modality.TEXT}),
    tool_calling=True,
    streaming=True,
    context_tokens=131_072,
    thinking=True,
    egress=EgressClass.PUBLIC_CLOUD,
)

#: ``ImagePart`` carries no MIME type (Contract 1), and a data URI needs one.
#: PNG is the safe default for screenshots and rendered documents; pass an
#: already-formed ``data:`` URI as the part's ``data`` to override.
_IMAGE_MIME = "image/png"


def _content_parts(msg: Message) -> str | list[dict[str, Any]]:
    """OpenAI ``content`` for one turn — media first, then text (quirk 4).

    A text-only turn stays a plain string, which every OpenAI-compatible
    endpoint accepts; mixed turns become the typed part list.

    Example:
        >>> _content_parts(Message.text("user", "hello"))
        'hello'
        >>> [p["type"] for p in _content_parts(
        ...     Message("user", (TextPart("what is this?"), ImagePart(b"x"))))]
        ['image_url', 'text']
    """
    texts = [p.text for p in msg.parts if isinstance(p, TextPart)]
    media: list[dict[str, Any]] = []
    for part in msg.parts:
        if isinstance(part, ImagePart):
            data = part.data if isinstance(part.data, str) and part.data.startswith("data:") else (
                f"data:{_IMAGE_MIME};base64,{encode_media(part.data)}"
            )
            media.append({"type": "image_url", "image_url": {"url": data}})
        elif isinstance(part, AudioPart):
            media.append(
                {
                    "type": "input_audio",
                    "input_audio": {"data": encode_media(part.data), "format": part.format},
                }
            )
    if not media:
        return "\n\n".join(texts)
    parts: list[dict[str, Any]] = list(media)
    if texts:
        parts.append({"type": "text", "text": "\n\n".join(texts)})
    return parts


def _openai_message(msg: Message) -> dict[str, Any]:
    """One turn in OpenAI shape. ``thinking`` is never serialized (rule 3).

    Example:
        >>> _openai_message(Message("assistant", (TextPart("ok"),), thinking="hidden"))
        {'role': 'assistant', 'content': 'ok'}
    """
    out: dict[str, Any] = {"role": msg.role, "content": _content_parts(msg)}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, sort_keys=True),
                },
            }
            for tc in msg.tool_calls
        ]
    if msg.role == "tool" and msg.tool_call_id:
        out["tool_call_id"] = msg.tool_call_id
    # Rule 3: msg.thinking is deliberately absent. Do not add it.
    return out


def build_openai_chat(req: GenerateRequest, model: str) -> dict[str, Any]:
    """Build a ``/chat/completions`` body applying the same six builder rules.

    ``top_k`` has no field in the OpenAI schema, so Gemma 4's ``top_k=64``
    cannot be sent — that is a wire-format limit, stated here rather than
    hidden. Everything else (defaults, think prefix, stripped history, media
    ordering) matches ``build_ollama_chat``.

    Example:
        >>> req = GenerateRequest(
        ...     messages=(Message.text("user", "hi"),), think=True, max_tokens=64)
        >>> body = build_openai_chat(req, "gemma-4-31b")
        >>> body["messages"][0]
        {'role': 'system', 'content': '<|think|>'}
        >>> (body["temperature"], body["top_p"], body["max_tokens"])
        (1.0, 0.95, 64)
    """
    messages = [_openai_message(m) for m in req.messages]
    if req.think:
        messages = apply_think(messages)

    opts = sampling_options(req)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": opts["temperature"],
        "top_p": opts["top_p"],
        "stream": False,
    }
    if "num_predict" in opts:
        body["max_tokens"] = opts["num_predict"]
    if "stop" in opts:
        body["stop"] = opts["stop"]
    if req.tools:
        body["tools"] = [_function_schema(t) for t in req.tools]
    return body


def parse_openai_response(body: dict[str, Any], model: str) -> GenerateResponse:
    """Turn a ``/chat/completions`` body into a ``GenerateResponse``.

    Example:
        >>> body = {"model": "gemma-4-31b", "usage": {"completion_tokens": 3},
        ...         "choices": [{"message": {"content": "hello"}}]}
        >>> parse_openai_response(body, "gemma-4-31b").text
        'hello'
    """
    choices = body.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    text, thinking = split_thinking(
        msg.get("content") or "", msg.get("reasoning_content") or msg.get("thinking")
    )
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {"__raw": raw_args}
        calls.append(ToolCall(id=str(tc.get("id") or fn.get("name") or ""), name=fn.get("name", ""), arguments=args))
    usage = body.get("usage") or {}
    return GenerateResponse(
        text=text,
        thinking=thinking,
        tool_calls=tuple(calls),
        usage=Usage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        ),
        model=body.get("model") or model,
        raw=body,
    )


class OpenAICompatibleProvider:
    """Any endpoint speaking OpenAI ``/chat/completions``.

    Args:
        model: the hosted model id (not an Ollama tag).
        base_url: API root, e.g. ``https://api.cerebras.ai/v1``.
        api_key_env: name of the environment variable holding the key. The
            *name* is stored; the value is read per call and never retained.
        egress: declared egress class — ``public-cloud`` unless you are
            pointing at your own box, in which case say so deliberately.
        capabilities: declare what the endpoint supports; the default is
            text-only, because a URL tells you nothing.

    Example:
        >>> import httpx, os
        >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
        >>> os.environ["DEMO_KEY"] = "sk-not-real"
        >>> handler = lambda r: httpx.Response(200, json={
        ...     "choices": [{"message": {"content": "pong"}}]})
        >>> p = OpenAICompatibleProvider("gemma-4-31b", "https://example.invalid/v1",
        ...     "DEMO_KEY", client=httpx.Client(transport=httpx.MockTransport(handler)))
        >>> p.generate(GenerateRequest(messages=(Message.text("user", "ping"),))).text
        'pong'
        >>> "sk-not-real" in repr(p)
        False
    """

    name = "openai-compatible"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str,
        egress: EgressClass = EgressClass.PUBLIC_CLOUD,
        *,
        capabilities: Capabilities | None = None,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        name: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.egress = egress
        base = capabilities or DEFAULT_HOSTED
        # The declared egress class is the privacy boundary's only input —
        # keep it in sync with the argument the caller actually passed.
        self._capabilities = base if base.egress == egress else replace(base, egress=egress)
        if name:
            self.name = name
        #: The raw client. Nothing hides the provider.
        self.client: httpx.Client = client or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        """Never renders the key — only the variable it lives in.

        Example:
            >>> repr(OpenAICompatibleProvider("m", "https://x/v1", "K"))
            "OpenAICompatibleProvider(model='m', base_url='https://x/v1', api_key_env='K')"
        """
        return (
            f"{type(self).__name__}(model={self.model!r}, "
            f"base_url={self.base_url!r}, api_key_env={self.api_key_env!r})"
        )

    def capabilities(self) -> Capabilities:
        """Declared capabilities (never inferred from the endpoint).

        Example:
            >>> OpenAICompatibleProvider("m", "https://x/v1", "K").capabilities().egress
            <EgressClass.PUBLIC_CLOUD: 'public-cloud'>
        """
        return self._capabilities

    def check(self, req: GenerateRequest) -> None:
        """Pre-network capability check — raises before any socket is opened.

        Example:
            >>> from agenticcarekit.kernel.contracts import AudioPart, Message
            >>> req = GenerateRequest(messages=(Message("user", (AudioPart(b"x"),)),))
            >>> OpenAICompatibleProvider("m", "https://x/v1", "K").check(req)
            Traceback (most recent call last):
            ...
            agenticcarekit.kernel.contracts.errors.CapabilityMismatch: m does not support audio input
        """
        ensure_supported(self.model, self._capabilities, req)

    def _headers(self) -> dict[str, str]:
        """Auth header, built from the environment at call time.

        Raises:
            AckError E120: the variable is unset. The message names the
            variable, never a value.
        """
        key = os.environ.get(self.api_key_env)
        if not key:
            raise AckError(
                f"{self.api_key_env} is not set",
                code="E120",
                why=f"{self.name} needs an API key; only its presence is ever checked.",
                fix=f"export {self.api_key_env}=...   # the value is never logged",
            )
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _wrap(self, exc: Exception) -> AckError:
        """Coded error for a transport failure (key values never included).

        Example:
            >>> import httpx
            >>> OpenAICompatibleProvider("m", "https://x/v1", "K")._wrap(
            ...     httpx.ReadTimeout("slow")).code
            'E102'
        """
        return AckError(
            f"{self.name} request to {self.base_url} failed",
            code="E102",
            why=f"{type(exc).__name__}: {exc}",
            fix="check `ack doctor`, or run against the local provider: ack demo --offline",
        )

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        """One non-streaming completion.

        Example:
            >>> import httpx, os
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> os.environ["DEMO_KEY"] = "sk-not-real"
            >>> handler = lambda r: httpx.Response(200, json={
            ...     "choices": [{"message": {"content": "hi"}}]})
            >>> p = OpenAICompatibleProvider("m", "https://x/v1", "DEMO_KEY",
            ...     client=httpx.Client(transport=httpx.MockTransport(handler)))
            >>> p.generate(GenerateRequest(messages=(Message.text("user", "?"),))).text
            'hi'
        """
        self.check(req)
        body = build_openai_chat(req, self.model)
        try:
            resp = self.client.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers()
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise self._wrap(exc) from exc
        return parse_openai_response(payload, self.model)

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]:
        """Stream a completion over SSE; the last chunk carries the response.

        Example:
            >>> import httpx, os, json
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> os.environ["DEMO_KEY"] = "sk-not-real"
            >>> frames = ['data: ' + json.dumps(
            ...     {"choices": [{"delta": {"content": c}}]}) for c in "hey"]
            >>> handler = lambda r: httpx.Response(
            ...     200, text="\\n".join(frames + ["data: [DONE]"]))
            >>> p = OpenAICompatibleProvider("m", "https://x/v1", "DEMO_KEY",
            ...     client=httpx.Client(transport=httpx.MockTransport(handler)))
            >>> list(p.stream(GenerateRequest(messages=(Message.text("user", "?"),))))[-1].response.text
            'hey'
        """
        self.check(req)
        body = build_openai_chat(req, self.model)
        body["stream"] = True
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        try:
            with self.client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    frame = json.loads(data)
                    delta = (frame.get("choices") or [{}])[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    text_parts.append(piece)
                    thinking_parts.append(thought)
                    if piece or thought:
                        yield Chunk(delta=piece, thinking_delta=thought)
        except httpx.HTTPError as exc:
            raise self._wrap(exc) from exc

        assembled = {
            "model": self.model,
            "choices": [
                {
                    "message": {
                        "content": "".join(text_parts),
                        "reasoning_content": "".join(thinking_parts) or None,
                    }
                }
            ],
        }
        yield Chunk(done=True, response=parse_openai_response(assembled, self.model))
