"""``provider_for`` — turn an ``ack.toml`` model reference into a Provider.

The one place the ``provider:model`` string convention is interpreted.
``ack eval``/``ack demo`` and the sidecar resolve providers through this,
so a project's config is the single source of truth for what runs.
"""

from __future__ import annotations

from agenticcarekit.kernel.contracts import AckError, ModelRef, Provider

from .cerebras import CerebrasProvider
from .chain import FallbackChain
from .mock import MockProvider
from .ollama import OllamaProvider

__all__ = ["provider_for"]


def provider_for(
    ref: ModelRef | str,
    *,
    fallback: ModelRef | str | None = None,
    offline: bool = False,
) -> Provider:
    """Build the Provider a ``provider:model`` reference names.

    ``offline=True`` returns a fully-capable ``MockProvider`` regardless of
    the reference — the honest backbone of ``ack demo --offline``.

    Example:
        >>> provider_for("ollama:gemma4:e4b").name
        'ollama'
        >>> provider_for("mock:anything").__class__.__name__
        'MockProvider'
        >>> provider_for("ollama:gemma4:e4b", fallback="cerebras:gemma-4-31b").__class__.__name__
        'FallbackChain'
    """
    if offline:
        return MockProvider()
    parsed = ModelRef.parse(ref) if isinstance(ref, str) else ref
    primary = _single(parsed)
    if fallback is None:
        return primary
    parsed_fb = ModelRef.parse(fallback) if isinstance(fallback, str) else fallback
    return FallbackChain(primary, _single(parsed_fb))


def _single(ref: ModelRef) -> Provider:
    if ref.provider == "ollama":
        return OllamaProvider(ref.model)
    if ref.provider == "cerebras":
        return CerebrasProvider(ref.model)
    if ref.provider in ("openai", "openai-compatible"):
        raise AckError(
            f"'{ref.provider}:' references need an explicit base URL",
            code="E401",
            why="OpenAICompatibleProvider cannot guess which endpoint you mean.",
            fix="construct OpenAICompatibleProvider(model=..., base_url=..., api_key_env=...) directly",
        )
    if ref.provider == "mock":
        return MockProvider()
    raise AckError(
        f"unknown provider '{ref.provider}' in model reference '{ref}'",
        code="E401",
        why="built-in providers are: ollama, cerebras, mock. Plugins register via entry points.",
        fix='set [model] primary = "ollama:gemma4:e4b" in ack.toml',
    )
