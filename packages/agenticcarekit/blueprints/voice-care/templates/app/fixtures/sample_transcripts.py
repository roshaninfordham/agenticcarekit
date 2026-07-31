# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Synthetic sample intake transcripts.

Every name, MRN, and detail below is fabricated for this demo — they
follow the same synthetic-data conventions as
``agenticcarekit.packs.healthcare.SyntheticGenerator`` (seeded,
reproducible, never derived from a real patient record). Use these to
run ``make demo`` end to end with no microphone and no network.

Each transcript is a list of ``(speaker, text)`` turns, standing in for
what ``VoiceLoop`` would normally receive from ``MockASR``.
"""

from __future__ import annotations

SyntheticTranscript = list[tuple[str, str]]

SAMPLE_TRANSCRIPTS: dict[str, SyntheticTranscript] = {
    "sample-001": [
        ("assistant", "Thanks for calling. Can you tell me what's bringing you in today?"),
        ("patient", "Hi, this is Jordan Rivera. I've had a sore throat and a mild fever "
                     "since Tuesday, MRN is SYN-100482."),
        ("assistant", "Sorry to hear that. On a scale you're comfortable with, how would "
                       "you describe the fever, and has it changed day to day?"),
        ("patient", "It's been low-grade, maybe 100.5, kind of the same each evening."),
        ("assistant", "Got it. Any other symptoms — cough, ear pain, trouble swallowing?"),
        ("patient", "A little cough, no ear pain, swallowing is uncomfortable but manageable."),
        ("assistant", "Thank you. I'll route this as a routine visit and a clinician will "
                       "confirm timing with you shortly."),
    ],
    "sample-002": [
        ("assistant", "What's the reason for today's visit?"),
        ("patient", "This is Alex Chen, MRN SYN-100119. I twisted my ankle playing soccer "
                     "yesterday and it's pretty swollen."),
        ("assistant", "Can you put weight on it at all right now?"),
        ("patient", "A little, it's painful but I can hobble."),
        ("assistant", "Understood — I'll flag this for urgent review given the swelling, "
                       "so a clinician can look at it sooner rather than at a routine slot."),
    ],
    "sample-003": [
        ("assistant", "How can I help you today?"),
        ("patient", "Hi, Priya Nair, MRN SYN-100773. I just have a general question about "
                     "prep instructions for my colonoscopy next week."),
        ("assistant", "Happy to point you to the clinic's prep handout — I'll have that "
                       "sent to you, and this doesn't need a triage route, just information."),
    ],
}
