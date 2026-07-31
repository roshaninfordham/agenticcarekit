"""The probe sweep (brief §7.1).

Thirteen probes, all launched concurrently on a thread pool, each with its
**own** timeout. A probe that times out, crashes, or finds nothing yields
``unknown`` — it never blocks the sweep and never propagates an exception.

The network probe is submitted **first** and collected **last**: it has the
largest budget (3 s) and there is no reason to make the other twelve wait
for it.

Everything the probes touch — subprocess, HTTP, psutil, the environment,
the filesystem — arrives through :class:`ProbeEnv`, so the whole sweep is
testable offline with no monkeypatching of the standard library.

Example:
    >>> env = ProbeEnv(which=lambda n: None, environ={}, offline=True)
    >>> facts = run_probes(env)
    >>> facts.ollama_installed
    False
    >>> facts.probe("network").status
    'skipped'
"""

from __future__ import annotations

import os
import platform
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .facts import PROVIDER_KEY_ENV, MachineFacts, ProbeResult

__all__ = ["PROBES", "Probe", "ProbeEnv", "default_env", "run_probes"]

OLLAMA_HOST = "http://127.0.0.1:11434"

#: Public, documented, unmetered throughput endpoint. Overridable with
#: ``ACK_THROUGHPUT_URL``. Nothing is uploaded and nothing is reported —
#: this is a download measurement, not telemetry.
THROUGHPUT_URL = "https://speed.cloudflare.com/__down?bytes=16000000"
THROUGHPUT_CAP_BYTES = 8_000_000
THROUGHPUT_CAP_SECONDS = 3.0


# ── injectable environment ───────────────────────────────────────────────


def _default_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _default_run(argv: list[str], timeout: float) -> tuple[int, str, str]:
    import subprocess

    proc = subprocess.run(  # noqa: S603 - argv is a fixed literal list
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _default_http_get(url: str, timeout: float) -> tuple[int, str]:
    import httpx

    resp = httpx.get(url, timeout=timeout)
    return resp.status_code, resp.text


def _default_measure(url: str, timeout: float, cap_bytes: int) -> tuple[int, float] | None:
    """Ranged GET, measure, abort at the cap. Returns (bytes, seconds)."""
    import httpx

    started = time.monotonic()
    total = 0
    headers = {"Range": f"bytes=0-{cap_bytes - 1}"}
    with httpx.stream("GET", url, timeout=timeout, headers=headers) as resp:
        if resp.status_code >= 400:
            return None
        for chunk in resp.iter_bytes(65536):
            total += len(chunk)
            if total >= cap_bytes or time.monotonic() - started >= THROUGHPUT_CAP_SECONDS:
                break
    return total, max(1e-6, time.monotonic() - started)


def _default_mem() -> tuple[float, float] | None:
    import psutil

    vm = psutil.virtual_memory()
    return vm.total / 1e9, vm.available / 1e9


def _default_disk_free(path: Path) -> float | None:
    import shutil

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1e9


@dataclass
class ProbeEnv:
    """Everything a probe is allowed to touch. Inject to test offline."""

    which: Callable[[str], str | None] = _default_which
    run: Callable[[list[str], float], tuple[int, str, str]] = _default_run
    http_get: Callable[[str, float], tuple[int, str]] = _default_http_get
    measure: Callable[[str, float, int], tuple[int, float] | None] = _default_measure
    mem: Callable[[], tuple[float, float] | None] = _default_mem
    disk_free: Callable[[Path], float | None] = _default_disk_free
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    cwd: Path = field(default_factory=Path.cwd)
    home: Path = field(default_factory=Path.home)
    uname: tuple[str, str, str, str] = field(
        default_factory=lambda: (
            platform.system(),
            platform.release(),
            platform.machine(),
            platform.processor(),
        )
    )
    cpu_count: int | None = field(default_factory=os.cpu_count)
    python_version: str = field(default_factory=platform.python_version)
    offline: bool = False


def default_env() -> ProbeEnv:
    """A :class:`ProbeEnv` bound to the real machine.

    ``ACK_OFFLINE=1`` skips every network probe (the local daemon included),
    which is what ``--offline`` sets.
    """
    environ = dict(os.environ)
    return ProbeEnv(environ=environ, offline=environ.get("ACK_OFFLINE") == "1")


# ── individual probes ────────────────────────────────────────────────────


def probe_platform(env: ProbeEnv) -> dict[str, Any]:
    """os / arch / kernel — 50 ms budget."""
    system, release, machine, _proc = env.uname
    return {"os": system or "unknown", "os_release": release or "unknown", "arch": machine or "unknown"}


def probe_apple_silicon(env: ProbeEnv) -> dict[str, Any]:
    """arm64 + Darwin. Derived, but probed separately so its cost is honest."""
    system, _release, machine, _proc = env.uname
    return {"_apple_silicon": system == "Darwin" and machine in ("arm64", "aarch64")}


def probe_cpu(env: ProbeEnv) -> dict[str, Any]:
    """cpu cores + model — 100 ms budget."""
    system, _release, _machine, proc = env.uname
    model = proc or "unknown"
    if system == "Darwin":
        rc, out, _err = env.run(["sysctl", "-n", "machdep.cpu.brand_string"], 0.09)
        if rc == 0 and out.strip():
            model = out.strip()
    elif system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    return {"cpu_model": model or "unknown", "cpu_cores": env.cpu_count}


def probe_ram(env: ProbeEnv) -> dict[str, Any]:
    """RAM total + available via psutil — 100 ms budget."""
    got = env.mem()
    if not got:
        return {}
    total, available = got
    return {"ram_total_gb": round(total, 2), "ram_available_gb": round(available, 2)}


def probe_gpu(env: ProbeEnv) -> dict[str, Any]:
    """GPU vendor + VRAM — 800 ms budget. nvidia-smi, then Apple, then ROCm."""
    if env.which("nvidia-smi"):
        rc, out, _err = env.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            0.7,
        )
        if rc == 0 and out.strip():
            first = out.strip().splitlines()[0]
            name, _, mem = first.partition(",")
            vram = None
            try:
                vram = round(float(mem.strip()) / 1024.0, 2)  # MiB → GB-ish
            except ValueError:
                pass
            return {"gpu_vendor": "nvidia", "gpu_name": name.strip(), "vram_gb": vram}
    system, _release, machine, _proc = env.uname
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            # Unified memory: VRAM is not a separate pool, so it stays None
            # rather than being invented. The RAM filter governs instead.
            return {"gpu_vendor": "apple", "gpu_name": "Apple Silicon GPU (unified memory)"}
        rc, out, _err = env.run(["system_profiler", "SPDisplaysDataType"], 0.7)
        if rc == 0 and out.strip():
            name = next(
                (ln.split(":", 1)[1].strip() for ln in out.splitlines() if "Chipset Model" in ln),
                "unknown GPU",
            )
            return {"gpu_vendor": "apple", "gpu_name": name}
    if env.which("rocm-smi"):
        rc, out, _err = env.run(["rocm-smi", "--showmeminfo", "vram"], 0.7)
        if rc == 0:
            return {"gpu_vendor": "amd", "gpu_name": "ROCm device"}
    return {}


def model_dir_for(env: ProbeEnv) -> Path:
    """Where Ollama keeps blobs on this platform.

    Example:
        >>> str(model_dir_for(ProbeEnv(home=Path('/h'), uname=('Linux','','x86_64','')))).endswith('.ollama/models')
        True
    """
    override = env.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override)
    system = env.uname[0]
    if system == "Windows":
        base = env.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Ollama" / "models"
    return env.home / ".ollama" / "models"


def probe_disk(env: ProbeEnv) -> dict[str, Any]:
    """Free space at the model directory — 100 ms budget."""
    d = model_dir_for(env)
    free = env.disk_free(d)
    out: dict[str, Any] = {"model_dir": str(d)}
    if free is not None:
        out["disk_free_gb"] = round(free, 2)
    return out


def probe_ollama(env: ProbeEnv) -> dict[str, Any]:
    """Binary present, daemon answering, version — 600 ms budget."""
    installed = env.which("ollama") is not None
    out: dict[str, Any] = {"ollama_installed": installed}
    if env.offline:
        return out
    try:
        status, body = env.http_get(f"{OLLAMA_HOST}/api/version", 0.5)
    except Exception:
        return out
    if status == 200:
        import json

        out["ollama_daemon"] = True
        # The daemon answering is itself proof of an install even when the
        # binary is not on this shell's PATH (containers, launchd).
        out["ollama_installed"] = True
        try:
            out["ollama_version"] = str(json.loads(body).get("version") or "unknown")
        except ValueError:
            out["ollama_version"] = "unknown"
    return out


def probe_tags(env: ProbeEnv) -> dict[str, Any]:
    """Already-pulled tags — 600 ms budget. The single biggest score input."""
    if env.offline:
        raise _Skipped("offline")
    try:
        status, body = env.http_get(f"{OLLAMA_HOST}/api/tags", 0.5)
    except Exception:
        return {}
    if status != 200:
        return {}
    import json

    try:
        models = json.loads(body).get("models") or []
    except ValueError:
        return {}
    tags = sorted({str(m.get("name", "")) for m in models if m.get("name")})
    return {"installed_tags": tags}


def probe_docker(env: ProbeEnv) -> dict[str, Any]:
    """Docker present — 400 ms budget."""
    return {"docker_installed": env.which("docker") is not None}


def probe_runtimes(env: ProbeEnv) -> dict[str, Any]:
    """Python / Node versions — 400 ms budget."""
    out: dict[str, Any] = {"python_version": env.python_version}
    if env.which("node"):
        rc, stdout, _err = env.run(["node", "--version"], 0.35)
        if rc == 0 and stdout.strip():
            out["node_version"] = stdout.strip().lstrip("v")
    return out


def probe_network(env: ProbeEnv) -> dict[str, Any]:
    """Throughput: ranged GET, measure, abort at the 3 s cap.

    Reported in Mbps and fed straight into the download-ETA score. Failure
    means unknown, and an unknown ETA contributes nothing to the ranking —
    never a guess.
    """
    if env.offline:
        raise _Skipped("offline")
    url = env.environ.get("ACK_THROUGHPUT_URL", THROUGHPUT_URL)
    got = env.measure(url, THROUGHPUT_CAP_SECONDS, THROUGHPUT_CAP_BYTES)
    if not got:
        return {}
    total_bytes, seconds = got
    if total_bytes <= 0:
        return {}
    mbps = (total_bytes * 8) / seconds / 1e6
    return {"network_mbps": round(mbps, 2), "network_source": url}


def probe_provider_keys(env: ProbeEnv) -> dict[str, Any]:
    """Provider key **presence** — 10 ms budget.

    Booleans only. The value of a key is never read, logged, or sent
    anywhere; ``ack doctor --json`` shows ``true``/``false`` and nothing else.
    """
    return {"provider_keys": {k: bool(env.environ.get(k)) for k in PROVIDER_KEY_ENV}}


def probe_ack_toml(env: ProbeEnv) -> dict[str, Any]:
    """An existing ``ack.toml`` in cwd — 20 ms budget."""
    p = env.cwd / "ack.toml"
    try:
        present = p.is_file()
    except OSError:
        present = False
    return {"ack_toml_present": present, "ack_toml_path": str(p) if present else None}


class _Skipped(Exception):
    """A probe that deliberately did not run (offline mode)."""


@dataclass(frozen=True)
class Probe:
    """One entry of the brief §7.1 probe table."""

    name: str
    fn: Callable[[ProbeEnv], dict[str, Any]]
    timeout: float


#: Submission order — the network probe goes first (biggest budget), and is
#: rendered last (see :func:`run_probes`).
PROBES: tuple[Probe, ...] = (
    Probe("network", probe_network, 3.0),
    Probe("platform", probe_platform, 0.05),
    Probe("apple_silicon", probe_apple_silicon, 0.05),
    Probe("cpu", probe_cpu, 0.1),
    Probe("ram", probe_ram, 0.1),
    Probe("gpu", probe_gpu, 0.8),
    Probe("disk", probe_disk, 0.1),
    Probe("ollama", probe_ollama, 0.6),
    Probe("tags", probe_tags, 0.6),
    Probe("docker", probe_docker, 0.4),
    Probe("runtimes", probe_runtimes, 0.4),
    Probe("provider_keys", probe_provider_keys, 0.01),
    Probe("ack_toml", probe_ack_toml, 0.02),
)

#: Collection order — everything except the network, then the network.
_COLLECT_ORDER: tuple[Probe, ...] = tuple(
    [p for p in PROBES if p.name != "network"] + [p for p in PROBES if p.name == "network"]
)


def run_probes(env: ProbeEnv | None = None, *, timeout_scale: float = 1.0) -> MachineFacts:
    """Run every probe concurrently and fold the results into `MachineFacts`.

    ``timeout_scale`` widens every budget uniformly (useful on a loaded CI
    box); it never shrinks the honesty of the reported timings.

    Example:
        >>> facts = run_probes(ProbeEnv(which=lambda n: None, environ={}, offline=True))
        >>> facts.python_version == __import__('platform').python_version()
        True
    """
    env = env or default_env()
    merged: dict[str, Any] = {}
    results: list[ProbeResult] = []

    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=len(PROBES), thread_name_prefix="ack-probe")
    try:
        futures = {p.name: (p, pool.submit(_timed, p, env)) for p in PROBES}
        for probe in _COLLECT_ORDER:
            _p, future = futures[probe.name]
            budget = probe.timeout * timeout_scale
            remaining = budget - (time.monotonic() - started)
            try:
                payload, duration_ms = future.result(timeout=max(0.0, remaining))
            except TimeoutError:
                results.append(
                    ProbeResult(
                        name=probe.name,
                        status="timeout",
                        duration_ms=round(budget * 1000, 2),
                        detail=f"exceeded its {int(budget * 1000)} ms budget",
                    )
                )
                continue
            except _Skipped as skip:
                results.append(
                    ProbeResult(name=probe.name, status="skipped", detail=str(skip))
                )
                continue
            except Exception as exc:  # noqa: BLE001 - a probe never crashes the run
                results.append(
                    ProbeResult(name=probe.name, status="error", detail=type(exc).__name__)
                )
                continue
            if isinstance(payload, BaseException):
                status = "skipped" if isinstance(payload, _Skipped) else "error"
                detail = str(payload) if status == "skipped" else type(payload).__name__
                results.append(
                    ProbeResult(
                        name=probe.name, status=status, duration_ms=duration_ms, detail=detail
                    )
                )
                continue
            merged.update({k: v for k, v in payload.items() if not k.startswith("_")})
            results.append(ProbeResult(name=probe.name, duration_ms=duration_ms))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    merged["probes"] = sorted(results, key=lambda r: r.name)
    return MachineFacts(**merged)


def _timed(probe: Probe, env: ProbeEnv) -> tuple[dict[str, Any] | BaseException, float]:
    """Run one probe, swallowing every exception into the result."""
    t0 = time.monotonic()
    try:
        payload: dict[str, Any] | BaseException = probe.fn(env)
    except BaseException as exc:  # noqa: BLE001 - graceful degradation is the contract
        payload = exc
    return payload, round((time.monotonic() - t0) * 1000, 2)


def facts_from_file(path: str | Path) -> MachineFacts:
    """Load a recorded machine profile (the fixture corpus, brief §7.4)."""
    return MachineFacts.model_validate_json(Path(path).read_text(encoding="utf-8"))
