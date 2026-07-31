"""Template redactor — a passthrough implementation of the
``agenticcarekit.kernel.contracts.Redactor`` protocol.

To build a real redactor (see ``agenticcarekit.packs.healthcare.phi`` for
a fully worked example covering the 18 HIPAA identifier categories):

1. Give it a unique, dotted ``name`` (``"<pack>.<purpose>"``, e.g.
   ``"healthcare.phi"``) — this is what ``ack.toml``'s
   ``[policy] redactor`` and the policy engine's `redactor_for` refer to.
2. Implement ``redact(text) -> (clean_text, list[Redaction])``. Spans in
   the returned ``Redaction`` objects are always offsets into the
   **original** ``text``, never the redacted output.
3. Keep it honest: document what it catches and what it misses in the
   module docstring. Nobody should mistake pattern-matching for a
   compliance certification.
4. Score it against a hand-labelled golden set and publish the measured
   precision/recall in your pack's README (see
   ``agenticcarekit.packs.healthcare.scoring.score_phi_redactor`` for the
   scoring shape to imitate).

This template's ``TemplateRedactor`` does none of that — it returns the
text unchanged with zero redactions. It exists purely to prove a pack
can declare a redactor at all.
"""

from __future__ import annotations

from agenticcarekit.kernel.contracts import Redaction

__all__ = ["TemplateRedactor"]


class TemplateRedactor:
    """Passthrough ``Redactor``: always returns the input text unchanged
    and an empty redaction list. Replace ``redact`` with real logic.

    Example:
        >>> r = TemplateRedactor()
        >>> r.name
        '_template.none'
        >>> r.redact("Jane Doe, MRN 12345")
        ('Jane Doe, MRN 12345', [])
    """

    name = "_template.none"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        return text, []
