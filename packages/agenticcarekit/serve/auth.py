"""The sidecar's local token file.

The sidecar holds the privacy boundary for every thin client on the machine,
so "it only listens on loopback" is not a sufficient answer: any process on
the box can reach loopback. Every endpoint except ``/v1/health`` requires
``Authorization: Bearer <token>``.

The token is a file, not a flag and not an environment variable:

* ``<root>/.ack/serve.token``, mode ``0600``, directory ``0700``
* created on first run with :func:`secrets.token_urlsafe` (32 bytes of
  ``os.urandom``)
* **never logged, never echoed, never put in a trace payload or an error
  message.** The startup banner prints the *path*, so a client can read it;
  the value never crosses the terminal.
* compared with :func:`secrets.compare_digest` — a length-independent,
  constant-time compare, so a client cannot time its way to the value.

Reused across runs: a second ``ack serve`` picks up the same token, which is
what lets a long-lived agent keep working across sidecar restarts.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from agenticcarekit.kernel.contracts import AckError

__all__ = ["TOKEN_BYTES", "TokenStore", "bearer_token"]

#: Bytes of entropy behind the token (``token_urlsafe`` renders ~43 chars).
TOKEN_BYTES = 32

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def bearer_token(header: str | None) -> str | None:
    """Extract the token from an ``Authorization`` header value.

    Returns ``None`` for anything that is not a well-formed bearer header —
    the caller then fails closed, identically to a wrong token.

    Example:
        >>> bearer_token("Bearer abc123")
        'abc123'
        >>> bearer_token("bearer  abc123  ")
        'abc123'
        >>> bearer_token("Basic abc123") is None
        True
        >>> bearer_token(None) is None
        True
    """
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None


class TokenStore:
    """The token file for one project root.

    Example:
        >>> import tempfile, os, stat
        >>> root = Path(tempfile.mkdtemp())
        >>> store = TokenStore(root)
        >>> token = store.ensure()
        >>> store.path.relative_to(root).as_posix()
        '.ack/serve.token'
        >>> oct(stat.S_IMODE(store.path.stat().st_mode))
        '0o600'
        >>> TokenStore(root).ensure() == token       # reused, never regenerated
        True
        >>> store.verify(f"Bearer {token}")
        True
        >>> store.verify("Bearer not-the-token")
        False
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        #: Where the token lives. The *path* is safe to print; the contents
        #: never are.
        self.path = self.root / ".ack" / "serve.token"
        self._token: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────────

    def ensure(self) -> str:
        """Read the existing token, or mint one on first run.

        Creating the file uses ``O_CREAT | O_EXCL`` with mode ``0600`` so the
        secret is never briefly world-readable between ``open`` and ``chmod``.
        """
        if self._token is not None:
            return self._token
        existing = self.read()
        if existing is not None:
            self._token = existing
            return existing
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, _DIR_MODE)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
        except FileExistsError:  # pragma: no cover - two sidecars racing
            return self.ensure()
        except OSError as exc:
            raise AckError(
                f"cannot create the sidecar token file at {self.path}",
                code="E030",
                why=f"{type(exc).__name__}: {exc}",
                fix=f"ack serve --path <a directory you can write to>   # tried {self.root}",
            ) from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        self._token = token
        return token

    def use(self, token: str) -> str:
        """Adopt a caller-supplied token instead of the file's.

        For an embedder that already manages secrets (and for tests). The file
        is left alone: this store simply verifies against what it was handed.
        """
        self._token = token
        return token

    def read(self) -> str | None:
        """The stored token, or ``None`` when no sidecar has run here yet.

        Re-tightens the mode on every read: a token file that has drifted to
        ``0644`` (a careless ``chmod -R``, a copied tree) is a real leak, and
        silently trusting it would be worse than fixing it.
        """
        if not self.path.is_file():
            return None
        mode = stat.S_IMODE(self.path.stat().st_mode)
        if mode != _FILE_MODE:
            os.chmod(self.path, _FILE_MODE)
        value = self.path.read_text(encoding="utf-8").strip()
        return value or None

    # ── the check ───────────────────────────────────────────────────────

    def verify(self, header: str | None) -> bool:
        """Constant-time check of an ``Authorization`` header value.

        Fails closed: a missing file, a missing header, a wrong scheme and a
        wrong value are indistinguishable to the caller and to the clock.
        """
        expected = self._token or self.read()
        presented = bearer_token(header)
        if not expected or not presented:
            return False
        return secrets.compare_digest(expected, presented)
