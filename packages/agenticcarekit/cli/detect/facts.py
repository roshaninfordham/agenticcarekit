"""``MachineFacts`` — the serializable result of the probe sweep.

Everything the recommendation engine (brief §7.2) is allowed to know about
a machine lives in this one model. It is fully serializable in both
directions so that ``--json`` output and the fixture corpus in
``tests/fixtures_cli/machines/`` are literally the same shape: a test
fixture is a recorded ``ack doctor --json``.

Unknown is a first-class value: a probe that timed out leaves ``None``
here and an entry in :attr:`MachineFacts.probes` saying why.

Example:
    >>> f = MachineFacts(os="Darwin", arch="arm64", ram_total_gb=36.0)
    >>> f.apple_silicon, f.usable_ram_gb
    (True, 21.6)
    >>> MachineFacts.model_validate_json(f.model_dump_json()).ram_total_gb
    36.0
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["MachineFacts", "ProbeResult", "ProbeStatus", "RAM_HEADROOM"]

ProbeStatus = Literal["ok", "timeout", "error", "skipped"]

#: A local model may claim at most this share of total RAM (brief §7.2
#: hard filter: ``size > RAM × 0.6`` eliminates).
RAM_HEADROOM = 0.6

#: Provider API keys whose *presence* is probed. The value is never read,
#: logged, or transmitted — only the boolean (brief §7.1).
PROVIDER_KEY_ENV = (
    "ANTHROPIC_API_KEY",
    "CEREBRAS_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "HF_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
)


class ProbeResult(BaseModel):
    """How one probe went. Timings are measured, never estimated."""

    name: str
    status: ProbeStatus = "ok"
    duration_ms: float = 0.0
    detail: str | None = None


class MachineFacts(BaseModel):
    """What we know about this machine. ``None`` always means *unknown*."""

    facts_version: int = 1

    # platform
    os: str = "unknown"
    os_release: str = "unknown"
    arch: str = "unknown"
    cpu_model: str = "unknown"
    cpu_cores: int | None = None

    # memory / storage
    ram_total_gb: float | None = None
    ram_available_gb: float | None = None
    disk_free_gb: float | None = None
    model_dir: str = "unknown"

    # accelerators
    gpu_vendor: str | None = None
    gpu_name: str | None = None
    vram_gb: float | None = None

    # toolchain
    ollama_installed: bool = False
    ollama_daemon: bool = False
    ollama_version: str | None = None
    installed_tags: list[str] = Field(default_factory=list)
    docker_installed: bool = False
    python_version: str = "unknown"
    node_version: str | None = None

    # network
    network_mbps: float | None = None
    network_source: str | None = None

    # environment
    provider_keys: dict[str, bool] = Field(default_factory=dict)
    ack_toml_present: bool = False
    ack_toml_path: str | None = None

    # provenance
    probes: list[ProbeResult] = Field(default_factory=list)

    # ── derived facts (never stored, always computed) ───────────────────

    @property
    def apple_silicon(self) -> bool:
        """arm64 on Darwin — the ``-mlx`` precondition (brief §7.1).

        Example:
            >>> MachineFacts(os="Darwin", arch="arm64").apple_silicon
            True
            >>> MachineFacts(os="Linux", arch="aarch64").apple_silicon
            False
        """
        return self.os == "Darwin" and self.arch in ("arm64", "aarch64")

    @property
    def cuda(self) -> bool:
        """True when an NVIDIA GPU was detected (the CUDA hard filter path)."""
        return self.gpu_vendor == "nvidia"

    @property
    def usable_ram_gb(self) -> float | None:
        """Total RAM × 0.6 — the ceiling a local model must fit under."""
        if self.ram_total_gb is None:
            return None
        return round(self.ram_total_gb * RAM_HEADROOM, 3)

    def has_tag(self, tag: str) -> bool:
        """Whether an Ollama tag is already pulled.

        Matches ``gemma4:e4b`` against a stored ``gemma4:e4b`` or, when the
        daemon reports the default suffix, ``gemma4:e4b:latest``.

        Example:
            >>> MachineFacts(installed_tags=["gemma4:e4b"]).has_tag("gemma4:e4b")
            True
            >>> MachineFacts(installed_tags=["gemma4:e4b"]).has_tag("gemma4:31b")
            False
        """
        if tag in self.installed_tags:
            return True
        return any(t.rsplit(":latest", 1)[0] == tag for t in self.installed_tags)

    def probe(self, name: str) -> ProbeResult | None:
        """Look up one probe's result by name."""
        for p in self.probes:
            if p.name == name:
                return p
        return None

    def unknowns(self) -> list[str]:
        """Names of probes that did not produce a fact. Printed honestly."""
        return sorted(p.name for p in self.probes if p.status != "ok")
