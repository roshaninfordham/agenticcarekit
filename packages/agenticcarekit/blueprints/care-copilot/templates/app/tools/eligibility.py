# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""``check_eligibility`` — look up synthetic payer coverage.

Administrative decision support only: this tool answers "is this service
covered, and does it need prior auth", never a clinical question about
whether the service is appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass

from agenticcarekit.kernel.contracts import tool

# Synthetic payer directory. Real deployments replace this with a call to
# a clearinghouse (Availity, Change Healthcare, etc.) or the payer's own
# eligibility API — keep the permission ("network") either way, since a
# real lookup leaves the process.
_MOCK_PAYERS = {
    "SYN-PAYER-AETNA": {"name": "Synthetic Aetna PPO", "requires_prior_auth": {"MRI", "physical_therapy"}},
    "SYN-PAYER-UHC": {"name": "Synthetic UnitedHealthcare HMO", "requires_prior_auth": {"MRI", "specialist_referral"}},
    "SYN-PAYER-BCBS": {"name": "Synthetic BCBS PPO", "requires_prior_auth": {"MRI", "CT", "specialist_referral"}},
}


@dataclass(frozen=True)
class EligibilityResult:
    """Result of an eligibility check. All synthetic in this template."""

    payer_name: str
    is_covered: bool
    requires_prior_auth: bool
    copay_usd: float
    notes: str


def mock_check_eligibility(payer_id: str, service: str) -> EligibilityResult:
    """Canned eligibility answers over the synthetic payer directory."""
    payer = _MOCK_PAYERS.get(payer_id)
    if payer is None:
        return EligibilityResult(
            payer_name="unknown payer",
            is_covered=False,
            requires_prior_auth=False,
            copay_usd=0.0,
            notes=f"No synthetic record for payer_id={payer_id!r}. Not a real denial — this is a demo fixture gap.",
        )
    requires_auth = service in payer["requires_prior_auth"]
    return EligibilityResult(
        payer_name=payer["name"],
        is_covered=True,
        requires_prior_auth=requires_auth,
        copay_usd=25.0 if not requires_auth else 50.0,
        notes="Synthetic eligibility record — not a real coverage determination.",
    )


@tool(permissions={"network"}, mock=mock_check_eligibility)
def check_eligibility(payer_id: str, service: str) -> EligibilityResult:
    """Check whether ``service`` is covered under ``payer_id`` and whether
    it requires prior authorization.

    Administrative lookup only — it reports what the payer's rules say,
    it does not judge whether the service is clinically appropriate.

    Real implementation note: replace the body below with a call to your
    clearinghouse or payer eligibility API (270/271 transaction, or a
    vendor's REST equivalent). Keep ``mock_check_eligibility`` realistic
    so offline demos stay meaningful.
    """
    raise NotImplementedError(
        "check_eligibility has no live implementation in this template — "
        "wire it to your clearinghouse/payer API, or keep running with "
        "--offline to use the mock."
    )
