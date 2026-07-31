"""W-K · the token file and the bind rules.

Two things the sidecar must get right before anything else: the local token is
a real secret on disk (``0600``, minted once, reused), and the process refuses
to expose the privacy boundary to the network unless someone asked for that
out loud.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from agenticcarekit.kernel.contracts import AckError
from agenticcarekit.serve.app import create_app
from agenticcarekit.serve.auth import TokenStore, bearer_token
from agenticcarekit.serve.runner import banner_lines, check_bind, is_loopback, startup_info
from fastapi.testclient import TestClient


def mode_of(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


# ── the token file ───────────────────────────────────────────────────────


def test_token_file_is_created_0600_and_reused_across_app_instances(tmp_path: Path) -> None:
    """One secret per project, minted once, reused by every later sidecar.

    A long-lived agent keeps working across sidecar restarts precisely because
    the token does not rotate underneath it.
    """
    first = create_app(tmp_path)
    token_path = tmp_path / ".ack" / "serve.token"
    assert token_path.is_file()
    assert mode_of(token_path) == "0o600"
    assert mode_of(token_path.parent) == "0o700"

    token = token_path.read_text(encoding="utf-8").strip()
    assert len(token) >= 32

    second = create_app(tmp_path)
    assert token_path.read_text(encoding="utf-8").strip() == token

    auth = {"Authorization": f"Bearer {token}"}
    for app in (first, second):
        client = TestClient(app)
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/models?offline=true", headers=auth).status_code == 200


def test_a_loosened_token_file_is_tightened_on_read(tmp_path: Path) -> None:
    """A tree copied with careless permissions is fixed, not silently trusted."""
    store = TokenStore(tmp_path)
    token = store.ensure()
    os.chmod(store.path, 0o644)
    assert TokenStore(tmp_path).read() == token
    assert mode_of(store.path) == "0o600"


def test_verify_is_constant_time_and_fails_closed(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    token = store.ensure()
    assert store.verify(f"Bearer {token}") is True
    assert store.verify(f"bearer {token}") is True
    assert store.verify("Bearer ") is False
    assert store.verify("Bearer wrong") is False
    assert store.verify(token) is False  # no scheme is no token
    assert store.verify(None) is False
    assert TokenStore(tmp_path / "nowhere").verify("Bearer anything") is False


def test_bearer_token_parsing() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("Bearer   abc  ") == "abc"
    assert bearer_token("Token abc") is None
    assert bearer_token("") is None


def test_the_token_value_is_never_in_the_banner(tmp_path: Path) -> None:
    """The banner prints the path so a client can read it. Never the value."""
    store = TokenStore(tmp_path)
    token = store.ensure()
    info = startup_info(tmp_path, "127.0.0.1", 4422, token_path=store.path)
    rendered = "\n".join(banner_lines(info)) + repr(info)
    assert str(store.path) in rendered
    assert token not in rendered
    assert info["telemetry"] is False


# ── the bind rules ───────────────────────────────────────────────────────


def test_loopback_detection() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("localhost")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("10.0.0.5")
    assert not is_loopback("example.internal")


def test_a_non_loopback_bind_is_refused_by_default() -> None:
    """Binding the policy boundary to the network is never the default."""
    assert check_bind("127.0.0.1", allow_remote=False) == "127.0.0.1"
    with pytest.raises(AckError) as caught:
        check_bind("0.0.0.0", allow_remote=False)
    assert caught.value.code == "E303"
    assert "--allow-remote" in caught.value.fix


def test_allow_remote_requires_token_auth() -> None:
    """Deliberate exposure is allowed. Unauthenticated exposure is not."""
    assert check_bind("0.0.0.0", allow_remote=True) == "0.0.0.0"
    with pytest.raises(AckError) as caught:
        check_bind("0.0.0.0", allow_remote=True, require_auth=False)
    assert caught.value.code == "E303"


def test_startup_info_reports_where_a_client_should_look() -> None:
    info = startup_info(Path("/srv/proj"), "127.0.0.1", 4422, token_path=Path("/srv/t"))
    assert info["url"] == "http://127.0.0.1:4422"
    assert info["docs_url"].endswith("/docs")
    assert info["openapi_url"].endswith("/openapi.json")
    assert info["mode"] == "http"
    assert startup_info(Path("/x"), "h", 1, token_path=Path("/t"), mcp=True)["mode"] == "mcp"


# ── the CLI seam ─────────────────────────────────────────────────────────


def test_ack_serve_is_registered_and_documented() -> None:
    """``ack serve`` exists, lazily imports the sidecar, and says what it does."""
    from agenticcarekit.cli.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["serve", "--help"], env={"NO_COLOR": "1", "COLUMNS": "100"})
    assert result.exit_code == 0
    flat = " ".join(result.stdout.split())
    assert "--mcp" in flat
    assert "--allow-remote" in flat
    assert "--host" in flat and "--port" in flat


def test_dry_run_binds_nothing_and_writes_nothing(tmp_path: Path) -> None:
    """``--dry-run`` is the non-blocking form: it validates and reports.

    Load-bearing: it must not mint a token, because that would litter a
    directory (a repo root, a CI checkout) that never asked for a sidecar.
    """
    from agenticcarekit.serve.runner import startup_plan

    plan = startup_plan(tmp_path, port=4422)
    assert plan["would_serve"] is True
    assert plan["dry_run"] is True
    assert plan["token_exists"] is False
    assert not (tmp_path / ".ack").exists()

    with pytest.raises(AckError) as caught:
        startup_plan(tmp_path, host="0.0.0.0")
    assert caught.value.code == "E303"
    assert not (tmp_path / ".ack").exists()


def test_ack_serve_dry_run_emits_a_parseable_envelope(tmp_path: Path) -> None:
    """``ack serve --dry-run --json`` behaves like every other command."""
    import json

    from agenticcarekit.cli.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(
        app, ["serve", "--dry-run", "--json", "--path", str(tmp_path)], catch_exceptions=False
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "serve"
    assert payload["ok"] is True
    assert payload["data"]["url"] == "http://127.0.0.1:4422"
    assert not (tmp_path / ".ack").exists()


def test_a_missing_serve_extra_is_a_coded_error_with_a_literal_fix() -> None:
    """Without the extra, the user is told exactly what to install."""
    from agenticcarekit.serve.runner import _missing_extra

    err = _missing_extra(ImportError("No module named 'uvicorn'"))
    assert err.fix == 'uv pip install "agenticcarekit[serve]"'
    assert "uvicorn" in err.why
    assert err.details["missing"] == ["fastapi", "uvicorn", "sse-starlette", "mcp"]


def test_run_http_refuses_a_remote_bind_before_binding_anything(tmp_path: Path) -> None:
    """The refusal happens before a socket exists, so nothing is ever exposed."""
    from agenticcarekit.serve.runner import run_http

    with pytest.raises(AckError) as caught:
        run_http(tmp_path, host="0.0.0.0", allow_remote=False, quiet=True)
    assert caught.value.code == "E303"
