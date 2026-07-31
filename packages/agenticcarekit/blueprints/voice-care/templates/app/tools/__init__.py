# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Tools available to the voice-care intake assistant.

Every tool declared here ships a mandatory mock (Contract 3 —
``agenticcarekit.kernel.contracts.tool``): decoration itself fails at
import time if a mock is missing (E502). That is what makes
``make demo`` / ``ack demo --offline`` real — the agent loop dispatches
to ``spec.mock`` instead of ``spec.fn`` when running offline, so this
project never needs a live network to demo end to end.

This is an example seam, not a complete tool library — add the tools
your clinic actually needs and give each one an equally honest mock.
"""

from __future__ import annotations

from agenticcarekit.kernel.contracts import tool

# The clinic's current triage routing labels. In a real deployment this
# would come from the clinic's scheduling system (a "network" permission
# tool); here it is canned so the demo is honest about being a mock.
_MOCK_TRIAGE_ROUTES = [
    "routine",
    "urgent-review",
    "callback-requested",
    "same-day-nurse-line",
]


def mock_get_triage_routes() -> list[str]:
    """Canned routing labels — used automatically when running offline."""
    return list(_MOCK_TRIAGE_ROUTES)


@tool(permissions={"network"}, mock=mock_get_triage_routes)
def get_triage_routes() -> list[str]:
    """Fetch the clinic's current triage routing labels.

    Decision support only: this tool returns *labels the clinic defined*,
    it does not decide which one applies. That judgment is either made by
    a human, or by an explicit rule the clinic supplied elsewhere — never
    inferred by the model from clinical reasoning.

    Real implementation note: replace the body below with a call to your
    clinic's scheduling/EHR API. Keep the mock (``mock_get_triage_routes``)
    in sync with realistic values so offline demos stay meaningful.
    """
    raise NotImplementedError(
        "get_triage_routes has no live implementation in this template — "
        "wire it to your clinic's scheduling system, or keep running with "
        "--offline to use the mock."
    )
