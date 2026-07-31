"""Contract 2 — ``Sensitive[T]``, ``PolicyContext``, ``Redactor``.

Sensitivity is a type, not a convention (invariant 1). A ``Sensitive[T]``
value cannot reach a ``public-cloud`` provider without a declared redactor,
enforced at runtime by the policy engine (``agenticcarekit.kernel.policy``,
W-B). A comment saying "don't send PHI here" is not a boundary.

Design split:
    * ``Sensitive`` is a sealed box. It stores the value, captures the call
      site where it was constructed, masks itself in ``repr``/``str``, and
      refuses casual access.
    * ``PolicyContext`` (a protocol here; implemented by the policy engine)
      owns ALL enforcement. ``Sensitive.unwrap_for`` delegates to it —
      there is exactly one enforcement path.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from .provider import Provider

__all__ = [
    "PolicyContext",
    "Redaction",
    "Redactor",
    "Sensitive",
]

T = TypeVar("T")


@dataclass(frozen=True)
class Redaction:
    """One span a redactor replaced. Emitted into the trace (kind="redaction")."""

    category: str      # e.g. "NAME", "MRN", "DATE", "PHONE"
    start: int
    end: int
    replacement: str


@runtime_checkable
class Redactor(Protocol):
    """De-identification transform. Implementations live in packs
    (e.g. ``healthcare.phi`` covers the 18 HIPAA identifiers)."""

    name: str

    def redact(self, text: str) -> tuple[str, list[Redaction]]: ...


class Sensitive(Generic[T]):
    """Wraps a value that must not reach public-cloud egress un-redacted.

    Example:
        >>> s = Sensitive("John Smith, MRN 12345", label="intake_note")
        >>> "John" in repr(s)
        False
        >>> s.label
        'intake_note'

    The wrapped value is reachable only through ``unwrap_for`` (the
    enforced path) or the loudly-named ``dangerously_reveal`` (which the
    policy engine uses after authorization, and which is greppable in
    review precisely because of its name).
    """

    __slots__ = ("__value", "label", "origin")

    def __init__(self, value: T, *, label: str = "sensitive") -> None:
        self.__value = value
        self.label = label
        self.origin = _caller()

    def unwrap_for(self, provider: Provider, policy: PolicyContext) -> T:
        """Return the value (possibly redacted) if policy allows it for
        this provider's egress class.

        Raises ``PolicyViolation`` — naming this wrapper's construction
        site, its label, and the offending provider — if egress is
        disallowed and no redactor is declared. Never bypass this.
        """
        return policy.unwrap(self, provider)

    def dangerously_reveal(self) -> T:
        """Raw value, no policy check. For the policy engine after it has
        authorized egress, and for code that stays on-device by
        construction. The name is the audit trail."""
        return self.__value

    def map(self, fn) -> Sensitive:
        """Transform the inner value; the result stays Sensitive.

        Example:
            >>> Sensitive("abc").map(str.upper).dangerously_reveal()
            'ABC'
        """
        return Sensitive(fn(self.__value), label=self.label)

    # ── leak resistance ──────────────────────────────────────────────
    def __repr__(self) -> str:
        return f"Sensitive(<{self.label}>, origin={self.origin})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return self.__repr__()

    def __reduce__(self):
        raise TypeError(
            "Sensitive values cannot be pickled — serialize the redacted "
            "form via unwrap_for() instead."
        )


def _caller() -> str:
    """``module.py:lineno`` of the frame that constructed the Sensitive.

    This is what lets PolicyViolation name the exact call site."""
    frame = inspect.currentframe()
    try:
        f = frame.f_back.f_back if frame and frame.f_back else None
        if f is None:
            return "<unknown>"
        return f"{f.f_code.co_filename}:{f.f_lineno}"
    finally:
        del frame


class PolicyContext(Protocol):
    """The enforcement engine. One implementation
    (``agenticcarekit.kernel.policy.Policy``, W-B) is the only code path
    that reveals Sensitive values headed for a provider.
    """

    #: Most permissive egress class this context allows un-redacted.
    egress_limit: object

    def unwrap(self, value: Sensitive, provider: Provider) -> object:
        """Authorize (and possibly redact) ``value`` for ``provider``.

        Must raise ``PolicyViolation`` (E3xx) with the wrapper's origin,
        label, and provider name when egress is disallowed and no redactor
        is declared. Must emit a trace event for every decision, allowed
        or denied.
        """
        ...

    def redactor_for(self, value: Sensitive) -> Redactor | None:
        """The redactor that would apply to this value, if any."""
        ...
