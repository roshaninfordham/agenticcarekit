"""The privacy boundary — egress enforcement and redaction (W-B).

Invariant 1: *sensitivity is a type, not a convention*. ``Sensitive[T]``
cannot reach a ``public-cloud`` provider without a declared redactor, and
that is enforced at runtime, here, by :class:`Policy` — the single
``PolicyContext`` implementation.

    >>> from agenticcarekit.kernel.contracts import Capabilities, EgressClass
    >>> class Ollama:                    # a Provider stub; only .egress matters
    ...     name = "ollama"
    ...     def capabilities(self):
    ...         return Capabilities(frozenset(), frozenset(), True, True,
    ...                             131072, True, EgressClass.DEVICE)
    >>> policy = Policy(EgressClass.DEVICE)
    >>> Sensitive("Jane Doe, MRN 99321", label="note").unwrap_for(Ollama(), policy)
    'Jane Doe, MRN 99321'

``Redactor`` *implementations* live in packs (``healthcare.phi`` covers the
18 HIPAA identifiers); this package owns only the boundary they plug into.

Read ``THREATMODEL.md`` next to this file before trusting the boundary with
anything real: it states what is guaranteed, what is not, and which bypasses
have tests.
"""

from agenticcarekit.kernel.contracts import PolicyViolation, Sensitive

from .engine import Policy

__all__ = ["Policy", "PolicyViolation", "Sensitive"]
