"""Providers (W-A) — the encoded judgment about talking to Gemma 4.

Four providers, one message builder, one fallback chain. Everything a caller
needs is exported here; everything a caller might want to bypass is still
reachable (``provider.client`` is the raw ``httpx.Client`` — invariant 3).

Example:
    >>> from agenticcarekit.kernel.contracts import GenerateRequest, Message
    >>> req = GenerateRequest(messages=(Message.text("user", "hello"),), think=True)
    >>> payload = build_ollama_chat(req, "gemma4:e4b")
    >>> payload["messages"][0]["content"]
    '<|think|>'
    >>> payload["options"]["top_k"]
    64
    >>> audio_capable_tags()
    ['gemma4:e2b', 'gemma4:e2b-mlx', 'gemma4:e4b', 'gemma4:e4b-mlx']
"""

from .builder import build_ollama_chat
from .cerebras import CerebrasProvider
from .chain import FallbackChain
from .mock import MockProvider
from .models import GEMMA4_MODELS, MODEL_SIZES_GB, audio_capable_tags
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

__all__ = [
    "GEMMA4_MODELS",
    "MODEL_SIZES_GB",
    "CerebrasProvider",
    "FallbackChain",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "audio_capable_tags",
    "build_ollama_chat",
]
