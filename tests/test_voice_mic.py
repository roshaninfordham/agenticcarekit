"""`MicAdapter` must fail fast and helpfully when the optional
`sounddevice` dependency is not installed — never a bare `ImportError`
traceback.

`sounddevice` is not a project dependency (W-D deliberately does not add it
to pyproject.toml), so this path is exercised directly in the normal dev
environment. If a future environment happens to have it installed, this
test is skipped rather than asserting the impossible.
"""

from __future__ import annotations

import importlib.util

import pytest
from agenticcarekit.capabilities.voice import MicAdapter
from agenticcarekit.kernel.contracts import AckError

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sounddevice") is not None,
    reason="sounddevice is installed in this environment; the missing-dependency "
    "path is untestable here",
)


def test_missing_sounddevice_raises_helpful_ack_error():
    with pytest.raises(AckError) as excinfo:
        MicAdapter()

    err = excinfo.value
    assert err.code.startswith("E0")
    assert "sounddevice" in err.message
    assert err.why is not None and "sounddevice" in err.why
    assert err.fix is not None and "sounddevice" in err.fix


def test_error_render_is_helpful_not_a_bare_traceback():
    with pytest.raises(AckError) as excinfo:
        MicAdapter()

    rendered = excinfo.value.render()
    assert rendered.startswith("✗")
    assert "sounddevice" in rendered
