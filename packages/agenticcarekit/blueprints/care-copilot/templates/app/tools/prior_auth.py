# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""``draft_prior_auth`` — draft (never submit) a prior authorization request.

This is the one tool in this blueprint where the "decision support, not
action" boundary matters most: a prior auth request is a real
administrative artifact with downstream consequences if it is wrong or
premature. This tool, and its mock, only ever produce a **draft** for a
staff member to review, edit, and submit through your organization's real
prior-auth channel (payer portal, fax, EDI 278). There is no submission
path here, mocked or otherwise — adding one is a deliberate decision you
would make outside this template, with the appropriate review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenticcarekit.kernel.contracts import tool


@dataclass(frozen=True)
class PriorAuthDraft:
    """A drafted prior-authorization request. Never auto-submitted."""

    payer_id: str
    service: str
    status: str = "draft-pending-human-review"
    draft_text: str = ""
    required_fields_missing: list[str] = field(default_factory=list)


def mock_draft_prior_auth(
    payer_id: str, service: str, patient_ref: str, clinical_justification: str
) -> PriorAuthDraft:
    """Canned draft assembly — no payer is ever contacted by this mock."""
    missing = [
        f
        for f, val in {
            "patient_ref": patient_ref,
            "clinical_justification": clinical_justification,
        }.items()
        if not val
    ]
    draft_text = (
        f"DRAFT PRIOR AUTHORIZATION REQUEST (synthetic, unsubmitted)\n"
        f"Payer: {payer_id}\n"
        f"Patient reference: {patient_ref}\n"
        f"Requested service: {service}\n"
        f"Clinical justification (as provided by referring clinician): "
        f"{clinical_justification}\n"
        f"--- Staff: review, correct, and submit via your organization's "
        f"prior-auth channel. This draft is not submitted anywhere. ---"
    )
    return PriorAuthDraft(
        payer_id=payer_id,
        service=service,
        draft_text=draft_text,
        required_fields_missing=missing,
    )


@tool(permissions={"writes"}, mock=mock_draft_prior_auth)
def draft_prior_auth(
    payer_id: str, service: str, patient_ref: str, clinical_justification: str
) -> PriorAuthDraft:
    """Draft a prior authorization request for human review.

    Never submits anything. ``clinical_justification`` is expected to be
    the referring clinician's own words, passed through verbatim into the
    draft — this tool assembles paperwork, it does not generate or
    evaluate clinical justification itself.

    Real implementation note: replace the body below with your
    organization's document-generation step (e.g. filling a payer's PDF
    form template). The output must still be a draft artifact routed to a
    human queue — never a submission call.
    """
    raise NotImplementedError(
        "draft_prior_auth has no live implementation in this template — "
        "wire it to your document-generation step, or keep running with "
        "--offline to use the mock. It must remain draft-only: do not add "
        "a submission path here."
    )
