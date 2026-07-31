"""W-G · resumable model pulls (brief §8).

Acceptance: *killed mid-pull and re-run → resumes*. Ollama resumes a
partial blob server-side, so what is asserted here is the CLI's half of the
bargain — an idempotent call sequence (list, then pull the same tag, never
a delete or a reset), a valid state after Ctrl-C, and progress numbers that
come from the daemon rather than from a timer.
"""

from __future__ import annotations

from typing import Any

import pytest
from agenticcarekit.cli.pull import (
    PullResult,
    StubOllamaAPI,
    format_progress,
    pull_model,
)
from agenticcarekit.kernel.contracts import AckError

FULL_STREAM: list[dict[str, Any]] = [
    {"status": "pulling manifest"},
    {"status": "pulling a1b2c3", "digest": "sha256:a1b2c3", "completed": 0, "total": 9_600_000_000},
    {
        "status": "pulling a1b2c3",
        "digest": "sha256:a1b2c3",
        "completed": 4_800_000_000,
        "total": 9_600_000_000,
    },
    {
        "status": "pulling a1b2c3",
        "digest": "sha256:a1b2c3",
        "completed": 9_600_000_000,
        "total": 9_600_000_000,
    },
    {"status": "verifying sha256 digest"},
    {"status": "success"},
]


def test_an_already_pulled_model_is_skipped_and_said_so() -> None:
    api = StubOllamaAPI(tags=["gemma4:e4b-mlx"], events=FULL_STREAM)
    result = pull_model("gemma4:e4b-mlx", api)
    assert result.status == "already-present"
    assert result.message == "gemma4:e4b-mlx is already pulled — skipping the download"
    assert api.calls == ["list_tags"], "no download may be started for a model we have"


def test_a_complete_pull_reports_the_daemons_own_byte_counts() -> None:
    api = StubOllamaAPI(events=FULL_STREAM)
    result = pull_model("gemma4:e4b", api)
    assert result.status == "completed"
    assert result.completed_bytes == 9_600_000_000
    assert result.total_bytes == 9_600_000_000
    assert result.fraction == 1.0
    assert api.calls == ["list_tags", "pull:gemma4:e4b"]


def test_ctrl_c_leaves_a_valid_result_rather_than_an_exception() -> None:
    api = StubOllamaAPI(events=FULL_STREAM, interrupt_after=3)
    result = pull_model("gemma4:e4b", api)
    assert result.status == "interrupted"
    assert result.completed_bytes == 4_800_000_000
    assert result.fraction == pytest.approx(0.5)
    assert "resumes on the next run" in result.message


def test_re_running_after_an_interrupt_resumes_with_the_same_call_sequence() -> None:
    """The whole acceptance test, in one place.

    First run: interrupted halfway. Second run: the *same* two calls, with
    the *same* tag. Nothing is deleted, nothing is reset — resumption is
    the daemon's job and the CLI must not get in its way.
    """
    first_api = StubOllamaAPI(events=FULL_STREAM, interrupt_after=3)
    first = pull_model("gemma4:e4b", first_api)
    assert first.status == "interrupted"
    assert first_api.calls == ["list_tags", "pull:gemma4:e4b"]

    # The daemon still does not list the tag: the blob is partial.
    second_api = StubOllamaAPI(tags=[], events=FULL_STREAM[2:])
    second = pull_model("gemma4:e4b", second_api)
    assert second.status == "completed"
    assert second_api.calls == first_api.calls
    assert all(not c.startswith(("delete", "rm", "reset")) for c in second_api.calls)


def test_a_second_run_after_success_downloads_nothing() -> None:
    api = StubOllamaAPI(events=FULL_STREAM)
    assert pull_model("gemma4:e4b", api).status == "completed"
    done = StubOllamaAPI(tags=["gemma4:e4b:latest"], events=FULL_STREAM)
    assert pull_model("gemma4:e4b", done).status == "already-present"
    assert done.calls == ["list_tags"]


def test_a_registry_error_mid_stream_is_e101_with_a_resume_fix() -> None:
    api = StubOllamaAPI(events=[{"status": "pulling"}, {"error": "manifest unknown"}])
    with pytest.raises(AckError) as excinfo:
        pull_model("gemma4:e4b", api)
    err = excinfo.value
    assert err.code == "E101"
    assert "resumes where it stopped" in (err.fix or "")
    assert err.details["tag"] == "gemma4:e4b"


def test_progress_is_reported_only_from_daemon_numbers() -> None:
    seen: list[str] = []
    api = StubOllamaAPI(events=FULL_STREAM)
    pull_model("gemma4:e4b", api, on_progress=lambda line, _r: seen.append(line))
    assert seen == [
        "pulling manifest",
        "pulling a1b2c3  0%  0.0/9.6 GB",
        "pulling a1b2c3  50%  4.8/9.6 GB",
        "pulling a1b2c3  100%  9.6/9.6 GB",
        "verifying sha256 digest",
        "success",
    ]


def test_no_percentage_is_invented_when_the_daemon_sends_no_total() -> None:
    assert format_progress({"status": "pulling manifest"}) == "pulling manifest"
    assert format_progress({"status": "x", "completed": 5}) == "x"
    assert format_progress({}) == "working"


def test_pull_result_serializes_for_json() -> None:
    result = PullResult(tag="gemma4:e4b", status="interrupted", completed_bytes=1, total_bytes=4)
    assert result.to_dict() == {
        "tag": "gemma4:e4b",
        "status": "interrupted",
        "completed_bytes": 1,
        "total_bytes": 4,
        "fraction": 0.25,
        "message": "",
    }
    assert PullResult(tag="t", status="skipped").fraction is None
