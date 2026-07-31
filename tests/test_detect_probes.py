"""W-G · the probe sweep (brief §7.1).

Every probe here runs offline against an injected :class:`ProbeEnv`: no
subprocess, no sockets, no monkeypatching of the standard library. What is
asserted is the contract of the sweep — concurrency, per-probe timeouts,
graceful degradation to ``unknown``, and the promise that a provider key is
only ever a boolean.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from agenticcarekit.cli.detect import MachineFacts, ProbeEnv, run_probes
from agenticcarekit.cli.detect.facts import PROVIDER_KEY_ENV
from agenticcarekit.cli.detect.probes import (
    PROBES,
    facts_from_file,
    model_dir_for,
    probe_gpu,
    probe_network,
    probe_ollama,
    probe_provider_keys,
    probe_tags,
)

FIXTURES = Path(__file__).parent / "fixtures_cli"

SECRET = "sk-do-not-log-me-0123456789"


def make_env(**kw) -> ProbeEnv:
    """A ProbeEnv where nothing exists unless the test says it does."""
    defaults = dict(
        which=lambda _n: None,
        run=lambda _argv, _t: (127, "", "not found"),
        http_get=lambda _u, _t: (0, ""),
        measure=lambda _u, _t, _c: None,
        mem=lambda: (34_359_738_368 / 1, 16e9),
        disk_free=lambda _p: 512.0,
        environ={},
        cwd=Path("/nonexistent-project"),
        home=Path("/home/tester"),
        uname=("Linux", "6.8.0", "x86_64", "AMD Ryzen"),
        cpu_count=8,
        python_version="3.12.3",
        offline=False,
    )
    defaults.update(kw)
    return ProbeEnv(**defaults)


# ── the sweep ────────────────────────────────────────────────────────────


def test_probe_table_matches_the_brief() -> None:
    names = {p.name for p in PROBES}
    assert names == {
        "network",
        "platform",
        "apple_silicon",
        "cpu",
        "ram",
        "gpu",
        "disk",
        "ollama",
        "tags",
        "docker",
        "runtimes",
        "provider_keys",
        "ack_toml",
    }
    # The network probe is submitted first: it has the largest budget and
    # nothing else should wait behind it.
    assert PROBES[0].name == "network"
    assert PROBES[0].timeout == 3.0


def test_every_probe_records_a_timing() -> None:
    facts = run_probes(make_env(offline=True))
    assert {p.name for p in facts.probes} == {p.name for p in PROBES}
    assert all(p.duration_ms >= 0 for p in facts.probes)


def test_a_crashing_probe_degrades_to_unknown_and_never_raises() -> None:
    def boom(_argv, _t):
        raise RuntimeError("nvidia-smi exploded")

    facts = run_probes(make_env(which=lambda n: "/usr/bin/" + n, run=boom, offline=True))
    assert facts.gpu_vendor is None
    gpu = facts.probe("gpu")
    assert gpu is not None and gpu.status == "error"
    assert "gpu" in facts.unknowns()


def test_a_slow_probe_times_out_without_blocking_the_sweep() -> None:
    def slow_mem():
        time.sleep(2.0)
        return (8e9, 4e9)

    started = time.monotonic()
    facts = run_probes(make_env(mem=slow_mem, offline=True))
    elapsed = time.monotonic() - started
    assert facts.ram_total_gb is None
    ram = facts.probe("ram")
    assert ram is not None and ram.status == "timeout"
    assert "100 ms budget" in (ram.detail or "")
    # The 100 ms RAM budget must not become a 2 s sweep.
    assert elapsed < 1.5


def test_probes_run_concurrently() -> None:
    """Thirteen probes, several sleeping — the sweep is bounded by the
    slowest budget, not by their sum."""

    def slow_run(argv, _t):
        time.sleep(0.25)
        return (127, "", "")

    started = time.monotonic()
    run_probes(make_env(which=lambda n: "/usr/bin/" + n, run=slow_run, offline=True))
    assert time.monotonic() - started < 1.0


def test_offline_skips_every_network_probe() -> None:
    facts = run_probes(make_env(offline=True))
    for name in ("network", "tags"):
        probe = facts.probe(name)
        assert probe is not None and probe.status == "skipped"
        assert probe.detail == "offline"
    assert facts.network_mbps is None
    assert facts.installed_tags == []


# ── individual probes ────────────────────────────────────────────────────


def test_provider_keys_are_booleans_and_never_values() -> None:
    env = make_env(environ={"OPENAI_API_KEY": SECRET, "CEREBRAS_API_KEY": ""})
    result = probe_provider_keys(env)
    assert result["provider_keys"]["OPENAI_API_KEY"] is True
    assert result["provider_keys"]["CEREBRAS_API_KEY"] is False
    assert set(result["provider_keys"]) == set(PROVIDER_KEY_ENV)
    assert SECRET not in json.dumps(result)


def test_a_key_value_never_reaches_serialized_facts() -> None:
    facts = run_probes(make_env(environ={"OPENAI_API_KEY": SECRET}, offline=True))
    assert SECRET not in facts.model_dump_json()
    assert facts.provider_keys["OPENAI_API_KEY"] is True


def test_ollama_daemon_version_is_read_from_the_api() -> None:
    def http(url, _t):
        assert url == "http://127.0.0.1:11434/api/version"
        return 200, json.dumps({"version": "0.12.4"})

    result = probe_ollama(make_env(http_get=http, which=lambda n: "/usr/bin/ollama"))
    assert result == {
        "ollama_installed": True,
        "ollama_daemon": True,
        "ollama_version": "0.12.4",
    }


def test_a_running_daemon_counts_as_installed_even_without_the_binary() -> None:
    result = probe_ollama(
        make_env(which=lambda _n: None, http_get=lambda _u, _t: (200, '{"version":"0.12.4"}'))
    )
    assert result["ollama_installed"] is True
    assert result["ollama_daemon"] is True


def test_a_dead_daemon_is_a_fact_not_an_error() -> None:
    def refused(_u, _t):
        raise ConnectionError("refused")

    result = probe_ollama(make_env(which=lambda n: "/usr/bin/ollama", http_get=refused))
    assert result == {"ollama_installed": True}


def test_installed_tags_are_sorted_and_deduplicated() -> None:
    body = json.dumps(
        {"models": [{"name": "gemma4:12b"}, {"name": "gemma4:e4b"}, {"name": "gemma4:12b"}]}
    )
    result = probe_tags(make_env(http_get=lambda _u, _t: (200, body)))
    assert result == {"installed_tags": ["gemma4:12b", "gemma4:e4b"]}


def test_nvidia_gpu_is_parsed_from_nvidia_smi() -> None:
    env = make_env(
        which=lambda n: "/usr/bin/nvidia-smi" if n == "nvidia-smi" else None,
        run=lambda _argv, _t: (0, "NVIDIA GeForce RTX 4090, 24564\n", ""),
    )
    assert probe_gpu(env) == {
        "gpu_vendor": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4090",
        "vram_gb": 23.99,
    }


def test_apple_silicon_reports_unified_memory_rather_than_inventing_vram() -> None:
    env = make_env(uname=("Darwin", "25.1.0", "arm64", "Apple M4 Max"))
    result = probe_gpu(env)
    assert result["gpu_vendor"] == "apple"
    assert "vram_gb" not in result


def test_network_throughput_is_measured_not_guessed() -> None:
    env = make_env(measure=lambda _u, _t, _c: (8_000_000, 2.0))
    result = probe_network(env)
    assert result["network_mbps"] == 32.0  # 8 MB in 2 s = 32 Mbps


def test_network_failure_is_unknown_not_zero() -> None:
    assert probe_network(make_env(measure=lambda _u, _t, _c: None)) == {}


def test_model_dir_follows_the_platform() -> None:
    linux = make_env(home=Path("/home/tester"), uname=("Linux", "", "x86_64", ""))
    assert model_dir_for(linux) == Path("/home/tester/.ollama/models")
    windows = make_env(
        uname=("Windows", "", "AMD64", ""), environ={"LOCALAPPDATA": "C:\\Users\\t\\AppData\\Local"}
    )
    assert model_dir_for(windows).as_posix().endswith("Ollama/models")
    override = make_env(environ={"OLLAMA_MODELS": "/mnt/big/models"})
    assert model_dir_for(override) == Path("/mnt/big/models")


# ── MachineFacts ─────────────────────────────────────────────────────────


def test_facts_round_trip_through_json() -> None:
    facts = run_probes(make_env(offline=True))
    again = MachineFacts.model_validate_json(facts.model_dump_json())
    assert again.model_dump() == facts.model_dump()


def test_derived_facts() -> None:
    m = MachineFacts(os="Darwin", arch="arm64", ram_total_gb=36.0, gpu_vendor="nvidia")
    assert m.apple_silicon is True
    assert m.cuda is True
    assert m.usable_ram_gb == 21.6
    assert MachineFacts(os="Linux", arch="aarch64").apple_silicon is False
    assert MachineFacts().usable_ram_gb is None


def test_has_tag_tolerates_the_latest_suffix() -> None:
    m = MachineFacts(installed_tags=["gemma4:e4b-mlx:latest", "nomic-embed-text:latest"])
    assert m.has_tag("gemma4:e4b-mlx") is True
    assert m.has_tag("gemma4:12b") is False


@pytest.mark.parametrize("profile", sorted((FIXTURES / "machines").glob("*.json")))
def test_every_fixture_profile_is_a_valid_machinefacts(profile: Path) -> None:
    facts = facts_from_file(profile)
    assert facts.facts_version == 1
    assert set(facts.provider_keys) == set(PROVIDER_KEY_ENV)
    assert all(isinstance(v, bool) for v in facts.provider_keys.values())
