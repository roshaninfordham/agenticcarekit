"""The egress enforcement engine — W-B, the implementation side of Contract 2.

``Policy`` is the one and only code path that reveals a ``Sensitive`` value on
its way to a provider. ``Sensitive.unwrap_for(provider, policy)`` delegates
here; nothing else in the toolkit calls ``dangerously_reveal()`` on data that
is about to leave the process.

The enforcement matrix (``docs/CONTRACTS.md``, Contract 2), implemented
literally in :meth:`Policy.unwrap`:

===========================  ========  =================  ================
value → provider egress      device    trusted-network    public-cloud
===========================  ========  =================  ================
non-sensitive                allow     allow              allow
``Sensitive``, no redactor   allow     allow if policy    **raise E301**
                                       egress ≥ trusted
``Sensitive``, redactor      allow     allow (raw or      allow **redacted
declared                     (raw)     redacted per       only**
                                       policy)
===========================  ========  =================  ================

Plus one rule that sits above the table: any provider whose egress class is
broader than the project's ``[policy] egress`` limit is refused outright
(**E303**) — sensitive value or not. That check is
:meth:`Policy.check_provider`, and the sensitive path runs it first.

Every decision — allow, allow-redacted, deny — emits a ``TraceEvent``
(``kind="policy"``). Redactions emit an additional ``kind="redaction"`` event
with ``bytes_out=0``, because redacting egresses nothing; the bytes are
counted by the provider event that follows.

Threat Model
------------
What this boundary guarantees:

* A ``Sensitive`` value cannot reach a ``public-cloud`` provider through
  ``unwrap_for`` / ``unwrap`` without a declared redactor. There is exactly
  one enforcement path, so there is exactly one place to audit.
* A provider broader than the configured limit is refused before any value is
  revealed, so a mis-wired fallback chain fails closed.
* Neither the raw value nor any redacted-away span is ever written into a
  trace payload. The audit trail records *decisions*, never *data*.

What it cannot guarantee, honestly:

* **It defends against accident, not malice.** Python has no private state.
  A determined developer can read ``value._Sensitive__value`` directly, or
  rebuild the object from its name-mangled slot. Nothing here stops that, and
  nothing in Python could. What stops it in practice is review: the only
  sanctioned raw accessor is named ``dangerously_reveal`` precisely so that
  ``git grep dangerously_reveal`` is a complete audit of raw access.
* **Declared-capability spoofing is out of scope.** ``Policy`` trusts
  ``provider.capabilities().egress``. A provider that declares ``device`` and
  then opens a socket to a third party defeats the boundary, and no runtime
  check inside this module can detect it. Providers are code you install; vet
  them like code you install. (Invariant 2 makes declaration the contract:
  the runtime negotiates against what a provider *declares*.)
* **Once a value is unwrapped it is an ordinary object.** The redacted string
  returned by :meth:`Policy.unwrap` carries no taint. Logging it, storing it,
  or re-sending it elsewhere is outside the boundary.
* **Redaction quality is the redactor's problem.** ``Policy`` verifies that a
  redactor ran and returned text; it cannot verify that the redactor is any
  good. Packs publish precision and recall for theirs (W-F).

See ``THREATMODEL.md`` next to this file for the closed-bypass list.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from agenticcarekit.kernel.contracts import (
    EgressClass,
    PolicyViolation,
    Provider,
    Redaction,
    Redactor,
    Sensitive,
    TraceEvent,
)

__all__ = ["Policy"]

#: Ordering of the three egress classes, narrowest first. The privacy boundary
#: is defined over these and nothing else (Contract 1).
_RANK: dict[EgressClass, int] = {
    EgressClass.DEVICE: 0,
    EgressClass.TRUSTED_NETWORK: 1,
    EgressClass.PUBLIC_CLOUD: 2,
}

_REDACTOR_FIX = (
    "declare a redactor in ack.toml:\n\n           [policy]\n"
    '           redactor = "healthcare.phi"\n\n'
    "       ...or route this call to a device/trusted-network provider."
)


def _rank(egress: EgressClass | str) -> int:
    return _RANK[EgressClass(egress)]


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "name", type(provider).__name__))


def _provider_egress(provider: Any) -> EgressClass:
    """Egress class a provider declares. Never inferred (invariant 2)."""
    try:
        egress = provider.capabilities().egress
    except AttributeError as exc:  # not a Provider at all
        raise TypeError(
            f"{_provider_name(provider)} does not satisfy the Provider protocol "
            f"(needs .name and .capabilities()): {exc}"
        ) from exc
    return EgressClass(egress)


class Policy:
    """Egress policy for one project — the ``PolicyContext`` implementation.

    ``egress`` is the project's limit, i.e. ``[policy] egress`` in ``ack.toml``.
    ``redactors`` maps name → :class:`Redactor` (implementations live in packs,
    e.g. ``healthcare.phi``); ``default_redactor`` names the one that applies to
    values without a label-specific match, i.e. ``[policy] redactor``.

    Example — a device-only project lets a ``Sensitive`` value through raw,
    because nothing leaves the machine:

        >>> from agenticcarekit.kernel.contracts import Capabilities, Sensitive
        >>> class Ollama:                        # a Provider stub; only .egress
        ...     name = "ollama"                  # matters to the policy engine
        ...     def capabilities(self):
        ...         return Capabilities(frozenset(), frozenset(), True, True,
        ...                             131072, True, EgressClass.DEVICE)
        >>> policy = Policy(EgressClass.DEVICE)
        >>> note = Sensitive("Jane Doe, MRN 99321", label="intake_note")
        >>> note.unwrap_for(Ollama(), policy)
        'Jane Doe, MRN 99321'

    The same value headed for public cloud with no redactor is refused, and the
    error names the field, the call site and the provider:

        >>> class Hosted:
        ...     name = "cerebras"
        ...     def capabilities(self):
        ...         return Capabilities(frozenset(), frozenset(), True, True,
        ...                             131072, True, EgressClass.PUBLIC_CLOUD)
        >>> open_policy = Policy(EgressClass.PUBLIC_CLOUD)
        >>> try:
        ...     note.unwrap_for(Hosted(), open_policy)
        ... except PolicyViolation as exc:
        ...     print(exc.code, exc.field_name, exc.provider)
        E301 intake_note cerebras
    """

    def __init__(
        self,
        egress: EgressClass = EgressClass.DEVICE,
        redactors: dict[str, Redactor] | None = None,
        default_redactor: str | None = None,
        emit: Callable[[TraceEvent], None] | None = None,
        *,
        redact_at_or_above: EgressClass = EgressClass.PUBLIC_CLOUD,
        run_id: str | None = None,
    ) -> None:
        """Build a policy.

        ``redact_at_or_above`` resolves the matrix's "raw or redacted per
        policy" cell: by default redaction is applied only where the contract
        requires it (``public-cloud``); set it to ``TRUSTED_NETWORK`` to redact
        for self-hosted destinations too. ``run_id`` correlates emitted events
        with a wider run (W-C supplies one when it owns the emitter).

        If exactly one redactor is declared and no default is named, that one
        is the default — it is the only candidate, not a guess. Naming a
        default that is not installed fails immediately with **E302** rather
        than at the first unwrap.

            >>> Policy(EgressClass.DEVICE).egress_limit
            <EgressClass.DEVICE: 'device'>
            >>> Policy(redactors={}, default_redactor="healthcare.phi")
            Traceback (most recent call last):
                ...
            agenticcarekit.kernel.contracts.errors.PolicyViolation: redactor "healthcare.phi" is declared in ack.toml but no installed pack provides it
        """
        self.egress_limit: EgressClass = EgressClass(egress)
        self.redactors: dict[str, Redactor] = dict(redactors or {})
        self.redact_at_or_above: EgressClass = EgressClass(redact_at_or_above)
        self.run_id = run_id or f"policy-{uuid.uuid4().hex[:12]}"
        self._emit = emit

        if default_redactor is None and len(self.redactors) == 1:
            default_redactor = next(iter(self.redactors))
        if default_redactor is not None and default_redactor not in self.redactors:
            raise PolicyViolation(
                f'redactor "{default_redactor}" is declared in ack.toml but no '
                "installed pack provides it",
                code="E302",
                why=(
                    "the policy engine refuses to guess — a silently missing "
                    "redactor would be an open boundary."
                ),
                fix="ack doctor --json | grep redactors   # then fix [policy] redactor",
                details={"declared": default_redactor, "installed": sorted(self.redactors)},
            )
        self.default_redactor = default_redactor

    def __repr__(self) -> str:
        return (
            f"Policy(egress={self.egress_limit.value!r}, "
            f"redactors={sorted(self.redactors)!r}, "
            f"default_redactor={self.default_redactor!r})"
        )

    # ── the boundary ────────────────────────────────────────────────────

    def check_provider(self, provider: Provider) -> EgressClass:
        """Refuse a provider broader than the project's limit (**E303**).

        This is the non-sensitive path: it applies to every provider call,
        because a project that declared ``egress = "device"`` did not agree to
        send *anything* to a third party. Returns the provider's declared
        egress class so callers can label their own trace events.

            >>> from agenticcarekit.kernel.contracts import Capabilities
            >>> class Hosted:
            ...     name = "cerebras"
            ...     def capabilities(self):
            ...         return Capabilities(frozenset(), frozenset(), True, True,
            ...                             131072, True, EgressClass.PUBLIC_CLOUD)
            >>> try:
            ...     Policy(EgressClass.DEVICE).check_provider(Hosted())
            ... except PolicyViolation as exc:
            ...     print(exc.code, exc.provider)
            E303 cerebras
        """
        egress = self._check_egress(provider, value=None)
        self._policy_event(
            decision="allow",
            reason=(
                f"provider egress {egress.value} is within the project limit "
                f"{self.egress_limit.value}; value is not Sensitive"
            ),
            egress=egress,
            provider=provider,
            value=None,
        )
        return egress

    def unwrap(self, value: Sensitive, provider: Provider) -> Any:
        """Authorize (and if required redact) ``value`` for ``provider``.

        The single sanctioned path from a ``Sensitive`` box to a string on the
        wire. Returns the raw value where the destination is inside the
        boundary, the redacted text where it is not, and raises
        :class:`PolicyViolation` where neither is permitted.

            >>> from agenticcarekit.kernel.contracts import Capabilities, Redaction, Sensitive
            >>> class Hosted:
            ...     name = "cerebras"
            ...     def capabilities(self):
            ...         return Capabilities(frozenset(), frozenset(), True, True,
            ...                             131072, True, EgressClass.PUBLIC_CLOUD)
            >>> class Mask:                      # real ones live in packs
            ...     name = "demo.mask"
            ...     def redact(self, text):
            ...         return "[NAME]", [Redaction("NAME", 0, len(text), "[NAME]")]
            >>> policy = Policy(EgressClass.PUBLIC_CLOUD, {"demo.mask": Mask()})
            >>> policy.unwrap(Sensitive("Jane Doe", label="patient_name"), Hosted())
            '[NAME]'
        """
        if not isinstance(value, Sensitive):
            raise TypeError(
                "Policy.unwrap() takes a Sensitive value; got "
                f"{type(value).__name__}. Non-sensitive values need no "
                "authorization — call Policy.check_provider(provider) instead."
            )

        egress = self._check_egress(provider, value=value)
        redactor = self.redactor_for(value)

        # Row: device — nothing leaves the machine, so raw is fine.
        if egress is EgressClass.DEVICE:
            return self._allow_raw(
                value,
                provider,
                egress,
                reason="destination is device egress; the value never leaves the machine",
            )

        # Row: trusted-network — reachable only because the project limit
        # already allows it (else _check_egress raised E303 above).
        if egress is EgressClass.TRUSTED_NETWORK:
            if redactor is not None and _rank(self.redact_at_or_above) <= _rank(egress):
                return self._allow_redacted(value, provider, egress, redactor)
            return self._allow_raw(
                value,
                provider,
                egress,
                reason=(
                    "destination is trusted-network and the project limit "
                    f"({self.egress_limit.value}) permits un-redacted egress there"
                ),
            )

        # Row: public-cloud — redacted only, never raw.
        if redactor is None:
            self._deny(
                message=(
                    f'sensitive value "{value.label}" cannot reach public-cloud '
                    f'provider "{_provider_name(provider)}" un-redacted'
                ),
                why=(
                    f"it was created at {value.origin} and no redactor is declared "
                    "for it — sensitivity is a type, not a convention, so the "
                    "engine refuses rather than hoping the text is harmless."
                ),
                fix=_REDACTOR_FIX,
                code="E301",
                value=value,
                provider=provider,
                egress=egress,
                reason="public-cloud destination with no declared redactor",
            )
        return self._allow_redacted(value, provider, egress, redactor)

    def redactor_for(self, value: Sensitive) -> Redactor | None:
        """The redactor that would apply to ``value``, if any.

        Resolution order: a redactor registered under the value's own label
        (so one project can hold ``"transcript"`` to a stricter transform than
        the rest), then the project default.

            >>> class Mask:
            ...     name = "demo.mask"
            ...     def redact(self, text):
            ...         return "[REDACTED]", []
            >>> policy = Policy(redactors={"demo.mask": Mask()})
            >>> policy.redactor_for(Sensitive("x", label="note")).name
            'demo.mask'
            >>> Policy().redactor_for(Sensitive("x")) is None
            True
        """
        specific = self.redactors.get(value.label)
        if specific is not None:
            return specific
        if self.default_redactor is not None:
            return self.redactors[self.default_redactor]
        return None

    # ── internals ───────────────────────────────────────────────────────

    def _check_egress(self, provider: Provider, value: Sensitive | None) -> EgressClass:
        """E303 gate: refuse anything broader than the project limit."""
        egress = _provider_egress(provider)
        if _rank(egress) > _rank(self.egress_limit):
            self._deny(
                message=(
                    f'provider "{_provider_name(provider)}" egresses to '
                    f"{egress.value}, above this project's limit of "
                    f"{self.egress_limit.value}"
                ),
                why=(
                    "the project declared a stricter boundary than this provider "
                    "satisfies; the engine refuses rather than quietly widening it."
                ),
                fix=(
                    "use a device/trusted-network provider, or raise the limit "
                    "deliberately in ack.toml:\n\n           [policy]\n"
                    f'           egress = "{egress.value}"'
                ),
                code="E303",
                value=value,
                provider=provider,
                egress=egress,
                reason=(
                    f"provider egress {egress.value} exceeds project limit "
                    f"{self.egress_limit.value}"
                ),
            )
        return egress

    def _allow_raw(
        self,
        value: Sensitive,
        provider: Provider,
        egress: EgressClass,
        *,
        reason: str,
    ) -> Any:
        self._policy_event(
            decision="allow",
            reason=reason,
            egress=egress,
            provider=provider,
            value=value,
        )
        return value.dangerously_reveal()

    def _allow_redacted(
        self,
        value: Sensitive,
        provider: Provider,
        egress: EgressClass,
        redactor: Redactor,
    ) -> str:
        raw = value.dangerously_reveal()
        if not isinstance(raw, str):
            self._deny(
                message=(
                    f'sensitive value "{value.label}" holds '
                    f"{type(raw).__name__}, which cannot be redacted"
                ),
                why=(
                    f"it was created at {value.origin}; redactors operate on text "
                    "only, so the engine has no way to de-identify this value "
                    "before it crosses the boundary."
                ),
                fix=(
                    "render it to text first (and wrap the result in Sensitive), "
                    "or keep this call on a device/trusted-network provider."
                ),
                code="E301",
                value=value,
                provider=provider,
                egress=egress,
                reason=f"non-str payload ({type(raw).__name__}) cannot be redacted",
            )

        clean, redactions = redactor.redact(raw)
        if not isinstance(clean, str):
            raise TypeError(
                f'redactor "{getattr(redactor, "name", redactor)}" returned '
                f"{type(clean).__name__}, expected str — the boundary cannot "
                "authorize output it cannot verify."
            )

        self._redaction_event(redactor, redactions)
        self._policy_event(
            decision="allow-redacted",
            reason=(
                f'redacted by "{getattr(redactor, "name", "?")}" before '
                f"{egress.value} egress ({len(redactions)} span(s) replaced)"
            ),
            egress=egress,
            provider=provider,
            value=value,
        )
        return clean

    def _deny(
        self,
        *,
        message: str,
        why: str,
        fix: str,
        code: str,
        value: Sensitive | None,
        provider: Provider,
        egress: EgressClass,
        reason: str,
    ) -> None:
        """Emit the deny event, then raise. Never returns."""
        self._policy_event(
            decision="deny",
            reason=reason,
            egress=egress,
            provider=provider,
            value=value,
        )
        raise PolicyViolation(
            message,
            code=code,
            why=why,
            fix=fix,
            field_name=None if value is None else value.label,
            call_site=None if value is None else value.origin,
            provider=_provider_name(provider),
        )

    # ── trace ───────────────────────────────────────────────────────────

    def _policy_event(
        self,
        *,
        decision: str,
        reason: str,
        egress: EgressClass,
        provider: Provider,
        value: Sensitive | None,
    ) -> None:
        self._emit_event(
            kind="policy",
            egress=egress,
            payload={
                "decision": decision,
                "reason": reason,
                "call_site": None if value is None else value.origin,
                "label": None if value is None else value.label,
                "provider": _provider_name(provider),
            },
        )

    def _redaction_event(self, redactor: Redactor, redactions: list[Redaction]) -> None:
        # bytes_out=0 and egress=device: redacting is a local transform. The
        # bytes are counted by the provider event that actually sends them.
        self._emit_event(
            kind="redaction",
            egress=EgressClass.DEVICE,
            payload={
                "redactor": getattr(redactor, "name", type(redactor).__name__),
                "categories": sorted({r.category for r in redactions}),
                "count": len(redactions),
            },
        )

    def _emit_event(self, *, kind: str, egress: EgressClass, payload: dict[str, Any]) -> None:
        """Emit one TraceEvent. Payloads carry decisions, never data — no
        wrapped value and no redacted span is ever written to the trace."""
        if self._emit is None:
            return
        self._emit(
            TraceEvent(
                ts=time.time(),
                run_id=self.run_id,
                span_id=uuid.uuid4().hex[:12],
                parent_span_id=None,
                kind=kind,  # type: ignore[arg-type]
                egress=egress,
                bytes_out=0,
                payload=payload,
            )
        )
