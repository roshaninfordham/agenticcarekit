"""Ollama provider — the only verified path (brief §2, "Testing scope").

Local by construction: ``EgressClass.DEVICE``, loopback host by default.
The raw ``httpx.Client`` is exposed as ``.client`` — nothing here hides the
provider, and dropping this class for a bare ``httpx`` call is always an
option (invariant 3).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from agenticcarekit.kernel.contracts import (
    AckError,
    Capabilities,
    Chunk,
    GenerateRequest,
    GenerateResponse,
    ToolCall,
    Usage,
)

from .builder import build_ollama_chat, split_thinking
from .models import capabilities_for, ensure_supported

__all__ = ["OllamaProvider"]

DEFAULT_HOST = "http://127.0.0.1:11434"


def parse_ollama_message(body: dict[str, Any], model: str) -> GenerateResponse:
    """Turn one ``/api/chat`` body into a ``GenerateResponse``.

    ``raw`` always carries the unmodified payload — the escape hatch that
    keeps this a toolkit rather than a framework.

    Example:
        >>> body = {"model": "gemma4:e4b", "eval_count": 7,
        ...         "message": {"content": "<|think|>hmm<|/think|>hello"}}
        >>> r = parse_ollama_message(body, "gemma4:e4b")
        >>> (r.text, r.thinking, r.usage.output_tokens)
        ('hello', 'hmm', 7)
    """
    msg = body.get("message") or {}
    text, thinking = split_thinking(msg.get("content") or "", msg.get("thinking"))
    calls = tuple(
        ToolCall(
            id=str(tc.get("id") or tc.get("function", {}).get("name") or ""),
            name=tc.get("function", {}).get("name", ""),
            arguments=tc.get("function", {}).get("arguments") or {},
        )
        for tc in (msg.get("tool_calls") or [])
    )
    return GenerateResponse(
        text=text,
        thinking=thinking,
        tool_calls=calls,
        usage=Usage(
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
        ),
        model=body.get("model") or model,
        raw=body,
    )


def _wrap_http_error(exc: Exception, host: str) -> AckError:
    """Translate an httpx failure into a coded, fixable error.

    Example:
        >>> _wrap_http_error(httpx.ConnectError("refused"), DEFAULT_HOST).code
        'E011'
        >>> _wrap_http_error(httpx.ReadTimeout("slow"), DEFAULT_HOST).code
        'E102'
    """
    if isinstance(exc, httpx.ConnectError):
        return AckError(
            f"cannot reach the Ollama daemon at {host}",
            code="E011",
            why=str(exc),
            fix="ollama serve   # then re-run your command",
        )
    return AckError(
        f"Ollama request to {host} failed",
        code="E102",
        why=f"{type(exc).__name__}: {exc}",
        fix='check `ack doctor`, or configure a fallback: [model] fallback = "cerebras:gemma-4-31b"',
    )


class OllamaProvider:
    """Chat against a local Ollama daemon.

    Args:
        model: Ollama tag, e.g. ``gemma4:e4b-mlx``.
        host: daemon base URL; loopback by default (nothing leaves the box).
        client: inject your own ``httpx.Client`` — for tests, proxies, mTLS,
            or anything this class did not anticipate.
        capabilities: override the declared capability record for tags that
            are not in the Gemma 4 table.

    Example:
        >>> import httpx
        >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
        >>> def handler(request):
        ...     return httpx.Response(200, json={"model": "gemma4:e4b",
        ...         "message": {"role": "assistant", "content": "hi there"}})
        >>> p = OllamaProvider("gemma4:e4b",
        ...     client=httpx.Client(transport=httpx.MockTransport(handler)))
        >>> p.generate(GenerateRequest(messages=(Message.text("user", "hi"),))).text
        'hi there'
        >>> p.capabilities().egress
        <EgressClass.DEVICE: 'device'>
    """

    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str = DEFAULT_HOST,
        client: httpx.Client | None = None,
        *,
        capabilities: Capabilities | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self._capabilities = capabilities_for(model, capabilities)
        #: The raw client, on purpose. Reach past this class whenever it
        #: gets in your way.
        self.client: httpx.Client = client or httpx.Client(timeout=timeout)

    def capabilities(self) -> Capabilities:
        """Declared capabilities for this tag.

        Example:
            >>> OllamaProvider("gemma4:e2b").capabilities().context_tokens
            131072
        """
        return self._capabilities

    def check(self, req: GenerateRequest) -> None:
        """Pre-network capability check — raises before any socket is opened.

        Called by ``generate`` and ``stream``; call it yourself at startup to
        fail at boot rather than at the first user turn.

        Example:
            >>> from agenticcarekit.kernel.contracts import AudioPart, Message
            >>> req = GenerateRequest(messages=(Message("user", (AudioPart(b"x"),)),))
            >>> try:
            ...     OllamaProvider("gemma4:31b").check(req)
            ... except Exception as e:
            ...     print(e.code, "|", e.candidates[0])
            E203 | gemma4:e2b
        """
        ensure_supported(self.model, self._capabilities, req)

    def _url(self) -> str:
        """Absolute ``/api/chat`` URL (the injected client may have no base).

        Example:
            >>> OllamaProvider("gemma4:e4b")._url()
            'http://127.0.0.1:11434/api/chat'
        """
        return f"{self.host}/api/chat"

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        """One non-streaming completion.

        Example:
            >>> import httpx
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> handler = lambda r: httpx.Response(200, json={"message": {"content": "ok"}})
            >>> p = OllamaProvider("gemma4:e4b",
            ...     client=httpx.Client(transport=httpx.MockTransport(handler)))
            >>> p.generate(GenerateRequest(messages=(Message.text("user", "?"),))).text
            'ok'
        """
        self.check(req)
        payload = build_ollama_chat(req, self.model)
        try:
            resp = self.client.post(self._url(), json=payload)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            raise _wrap_http_error(exc, self.host) from exc
        return parse_ollama_message(body, self.model)

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]:
        """Stream a completion; the final chunk carries the assembled response.

        Example:
            >>> import httpx, json
            >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
            >>> lines = [json.dumps({"message": {"content": "he"}}),
            ...          json.dumps({"message": {"content": "y"}, "done": True})]
            >>> handler = lambda r: httpx.Response(200, text="\\n".join(lines))
            >>> p = OllamaProvider("gemma4:e4b",
            ...     client=httpx.Client(transport=httpx.MockTransport(handler)))
            >>> chunks = list(p.stream(GenerateRequest(messages=(Message.text("user", "?"),))))
            >>> ("".join(c.delta for c in chunks), chunks[-1].response.text)
            ('hey', 'hey')
        """
        self.check(req)
        payload = build_ollama_chat(req, self.model)
        payload["stream"] = True
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        last: dict[str, Any] = {}
        try:
            with self.client.stream("POST", self._url(), json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    body = json.loads(line)
                    last = body
                    msg = body.get("message") or {}
                    delta = msg.get("content") or ""
                    thinking_delta = msg.get("thinking") or ""
                    text_parts.append(delta)
                    thinking_parts.append(thinking_delta)
                    for tc in msg.get("tool_calls") or []:
                        yield Chunk(
                            tool_call=ToolCall(
                                id=str(tc.get("id") or tc.get("function", {}).get("name") or ""),
                                name=tc.get("function", {}).get("name", ""),
                                arguments=tc.get("function", {}).get("arguments") or {},
                            )
                        )
                    if delta or thinking_delta:
                        yield Chunk(delta=delta, thinking_delta=thinking_delta)
        except httpx.HTTPError as exc:
            raise _wrap_http_error(exc, self.host) from exc

        assembled = dict(last)
        assembled["message"] = {
            **(last.get("message") or {}),
            "content": "".join(text_parts),
            "thinking": "".join(thinking_parts) or None,
        }
        yield Chunk(done=True, response=parse_ollama_message(assembled, self.model))
