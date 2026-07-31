"""Resumable model pulls (brief §8).

Three promises, and nothing else:

1. **If the model is already pulled, say so and skip.** Zero download cost
   is the single largest signal in the recommendation engine; acting on it
   is the least this can do.
2. **Ctrl-C leaves a valid state.** The project tree and ``ack.toml`` are
   written *before* the pull starts, so an interrupted pull leaves a
   working project, not a half-generated one.
3. **Re-running continues rather than restarts.** Ollama resumes a partial
   blob server-side; this module's job is to make the re-invocation
   idempotent (list, then pull the same tag — never delete, never reset)
   and to report the daemon's real byte counts.

**Never fake progress** (invariant 7): every number printed comes from the
pull stream. When the daemon reports no totals, nothing is rendered.

Example:
    >>> api = StubOllamaAPI(tags=["gemma4:e4b"])
    >>> pull_model("gemma4:e4b", api).status
    'already-present'
    >>> api.calls
    ['list_tags']
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agenticcarekit.kernel.contracts import AckError

__all__ = [
    "HttpOllamaAPI",
    "OllamaAPI",
    "PullResult",
    "StubOllamaAPI",
    "format_progress",
    "pull_model",
]

OLLAMA_HOST = "http://127.0.0.1:11434"

PullStatus = Literal["already-present", "completed", "interrupted", "failed", "skipped"]


class OllamaAPI(Protocol):
    """The two calls a pull needs. Injected in tests; never mocked at the
    HTTP layer, so the call *sequence* is what the tests assert."""

    def list_tags(self) -> list[str]:
        """Tags the daemon already has."""
        ...

    def pull(self, tag: str) -> Iterator[dict[str, Any]]:
        """Stream ``/api/pull`` progress objects for ``tag``."""
        ...


@dataclass
class PullResult:
    """Outcome of one pull attempt."""

    tag: str
    status: PullStatus
    completed_bytes: int = 0
    total_bytes: int = 0
    message: str = ""

    @property
    def fraction(self) -> float | None:
        """Completed fraction, or None when the daemon reported no total."""
        if self.total_bytes <= 0:
            return None
        return min(1.0, self.completed_bytes / self.total_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "status": self.status,
            "completed_bytes": self.completed_bytes,
            "total_bytes": self.total_bytes,
            "fraction": self.fraction,
            "message": self.message,
        }


class HttpOllamaAPI:
    """The real client. Lazily imports httpx so ``--offline`` never needs it."""

    def __init__(self, host: str = OLLAMA_HOST, timeout: float = 30.0) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout

    def list_tags(self) -> list[str]:
        import httpx

        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=min(5.0, self.timeout))
        except Exception as exc:  # noqa: BLE001
            raise AckError(
                "the Ollama daemon did not answer on 127.0.0.1:11434",
                code="E011",
                why=f"{type(exc).__name__} while listing installed models.",
                fix="ollama serve   # then re-run your command",
            ) from None
        if resp.status_code != 200:
            return []
        models = resp.json().get("models") or []
        return sorted({str(m.get("name", "")) for m in models if m.get("name")})

    def pull(self, tag: str) -> Iterator[dict[str, Any]]:
        import httpx

        payload = {"model": tag, "stream": True}
        with httpx.stream(
            "POST", f"{self.host}/api/pull", json=payload, timeout=None
        ) as resp:
            if resp.status_code != 200:
                raise AckError(
                    f"the Ollama daemon refused to pull {tag} (HTTP {resp.status_code})",
                    code="E101",
                    why="the registry or the daemon rejected the request.",
                    fix="re-run the same command — the pull resumes where it stopped",
                )
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


@dataclass
class StubOllamaAPI:
    """An injectable fake used by the resumability tests and ``--offline``.

    Records its call sequence so a test can assert that a re-run *resumes*
    (``list_tags`` then ``pull`` with the same tag) rather than restarting
    from a clean slate.
    """

    tags: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    interrupt_after: int | None = None

    def list_tags(self) -> list[str]:
        self.calls.append("list_tags")
        return list(self.tags)

    def pull(self, tag: str) -> Iterator[dict[str, Any]]:
        self.calls.append(f"pull:{tag}")
        for i, event in enumerate(self.events):
            if self.interrupt_after is not None and i >= self.interrupt_after:
                raise KeyboardInterrupt
            yield event


def format_progress(event: dict[str, Any]) -> str:
    """Render one pull-stream event honestly.

    Only what the daemon reported: its status line, and a percentage when
    (and only when) it sent both ``completed`` and ``total``.

    Example:
        >>> format_progress({"status": "pulling manifest"})
        'pulling manifest'
        >>> format_progress({"status": "pulling ab12", "completed": 5, "total": 10})
        'pulling ab12  50%  0.0/0.0 GB'
    """
    status = str(event.get("status", "")).strip() or "working"
    completed = event.get("completed")
    total = event.get("total")
    if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total > 0:
        pct = int(min(100.0, completed / total * 100))
        return f"{status}  {pct}%  {completed / 1e9:.1f}/{total / 1e9:.1f} GB"
    return status


def pull_model(
    tag: str,
    api: OllamaAPI,
    *,
    on_progress: Callable[[str, PullResult], None] | None = None,
) -> PullResult:
    """Pull ``tag``, resuming if a previous attempt was interrupted.

    Returns a :class:`PullResult` rather than raising on Ctrl-C: an
    interrupted pull is a valid state, and the caller prints the exact
    command that continues it.

    Example:
        >>> api = StubOllamaAPI(events=[{"status": "pulling", "completed": 1, "total": 2},
        ...                             {"status": "success"}])
        >>> r = pull_model("gemma4:e4b", api)
        >>> r.status, api.calls
        ('completed', ['list_tags', 'pull:gemma4:e4b'])
    """
    result = PullResult(tag=tag, status="failed")
    try:
        present = api.list_tags()
    except AckError:
        raise
    if _has_tag(tag, present):
        result.status = "already-present"
        result.message = f"{tag} is already pulled — skipping the download"
        return result

    try:
        for event in api.pull(tag):
            if event.get("error"):
                raise AckError(
                    f"pulling {tag} failed: {event['error']}",
                    code="E101",
                    why="the registry or the daemon reported an error mid-stream.",
                    fix="re-run the same command — the pull resumes where it stopped",
                    details={"tag": tag},
                )
            completed = event.get("completed")
            total = event.get("total")
            if isinstance(completed, (int, float)):
                result.completed_bytes = int(completed)
            if isinstance(total, (int, float)):
                result.total_bytes = int(total)
            if on_progress is not None:
                on_progress(format_progress(event), result)
        result.status = "completed"
        result.message = f"{tag} pulled"
    except KeyboardInterrupt:
        result.status = "interrupted"
        result.message = (
            f"pull interrupted — {tag} is partially downloaded and resumes on the next run"
        )
    return result


def _has_tag(tag: str, tags: list[str]) -> bool:
    """Tag membership, tolerating the daemon's ``:latest`` suffix.

    Example:
        >>> _has_tag("gemma4:e4b", ["gemma4:e4b:latest"])
        True
    """
    if tag in tags:
        return True
    return any(t.rsplit(":latest", 1)[0] == tag for t in tags)
