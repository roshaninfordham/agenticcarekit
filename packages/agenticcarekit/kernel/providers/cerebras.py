"""Cerebras — an OpenAI-compatible preset, nothing more.

A hosted provider is one URL, one environment variable and one egress class
away from the generic implementation. Keeping it a subclass rather than a
copy is the difference between a preset and a parallel implementation.

Status: **declared, untested**. Only Gemma 4 via Ollama is a verified path
(brief §2, "Testing scope") — the README support matrix says so, and so
does this docstring.
"""

from __future__ import annotations

import httpx

from agenticcarekit.kernel.contracts import Capabilities, EgressClass

from .openai_compat import OpenAICompatibleProvider

__all__ = ["CEREBRAS_API_KEY_ENV", "CEREBRAS_BASE_URL", "CerebrasProvider"]

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_API_KEY_ENV = "CEREBRAS_API_KEY"


class CerebrasProvider(OpenAICompatibleProvider):
    """Hosted Cerebras inference. Public cloud — the policy boundary knows.

    Example:
        >>> p = CerebrasProvider("gemma-4-31b")
        >>> (p.base_url, p.api_key_env, p.name)
        ('https://api.cerebras.ai/v1', 'CEREBRAS_API_KEY', 'cerebras')
        >>> p.capabilities().egress
        <EgressClass.PUBLIC_CLOUD: 'public-cloud'>
    """

    name = "cerebras"

    def __init__(
        self,
        model: str,
        base_url: str = CEREBRAS_BASE_URL,
        api_key_env: str = CEREBRAS_API_KEY_ENV,
        egress: EgressClass = EgressClass.PUBLIC_CLOUD,
        *,
        capabilities: Capabilities | None = None,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            model,
            base_url,
            api_key_env,
            egress,
            capabilities=capabilities,
            client=client,
            timeout=timeout,
            name="cerebras",
        )
