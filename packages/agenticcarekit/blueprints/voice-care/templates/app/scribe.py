# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Turn an intake transcript into a structured, human-reviewed note.

Uses ``agenticcarekit.capabilities.extract.extract`` for schema-validated
extraction (with agenticcarekit's built-in repair retry) against the
``IntakeNote`` model below. The scribe prompt (``prompts/scribe.md``)
instructs the model to transcribe what was actually said — this is
documentation, never a clinical assessment.
"""

from __future__ import annotations

from pathlib import Path

from agenticcarekit.capabilities.extract import extract
from agenticcarekit.kernel.contracts import EgressClass, Provider, Sensitive
from agenticcarekit.kernel.policy import Policy
from agenticcarekit.kernel.trace import Tracer
from pydantic import BaseModel, Field

SyntheticTranscript = list[tuple[str, str]]

_SCRIBE_PROMPT_PATH = Path(__file__).parent / "prompts" / "scribe.md"


class IntakeNote(BaseModel):
    """Structured intake note produced from a transcript.

    Decision support only — not diagnosis, not treatment. Every field is
    documentation the patient or clinic already stated; nothing here is
    inferred clinical judgment.
    """

    chief_complaint: str = Field(
        description="The patient's own words for why they are here."
    )
    history_of_present_illness: str = Field(
        description="Short narrative timeline, exactly as reported — no added inference."
    )
    reported_symptoms: list[str] = Field(default_factory=list)
    triage_route: str = Field(
        description="One of the clinic's own routing labels (see app.tools.get_triage_routes)."
    )
    follow_up_needed: bool = False
    notes_for_clinician: str = Field(
        default="",
        description="Anything the human reviewer should double-check before acting on this note.",
    )


def transcribe_to_note(
    transcript: SyntheticTranscript,
    *,
    provider: Provider,
    tracer: Tracer,
    policy: Policy | None = None,
) -> IntakeNote:
    """Extract an ``IntakeNote`` from a turn transcript.

    The raw transcript may carry patient identifiers, so it is wrapped in
    ``Sensitive`` and passed through ``unwrap_for`` — the one enforced
    path to a provider (Contract 2) — rather than read directly. With the
    default ``egress=DEVICE`` policy and a local/mock provider, nothing
    here leaves the machine; point this at a hosted fallback provider and
    the same call site will refuse to send raw PHI unless a redactor is
    configured (``ack.toml``'s ``[policy] redactor``).
    """
    policy = policy or Policy(egress=EgressClass.DEVICE)
    raw_text = "\n".join(f"{speaker}: {text}" for speaker, text in transcript)
    sensitive_transcript = Sensitive(raw_text, label="intake_transcript")
    clean_text = sensitive_transcript.unwrap_for(provider, policy)

    instructions = _SCRIBE_PROMPT_PATH.read_text(encoding="utf-8")
    return extract(
        text=clean_text,
        schema=IntakeNote,
        instructions=instructions,
        provider=provider,
        tracer=tracer,
    )
