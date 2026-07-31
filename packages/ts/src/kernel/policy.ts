/**
 * The egress enforcement engine — the implementation side of Contract 2.
 *
 * `Policy` is the one and only code path that reveals a `Sensitive` value on
 * its way to a provider. `Sensitive.unwrapFor(provider, policy)` delegates
 * here; nothing else in the toolkit calls `dangerouslyReveal()` on data that
 * is about to leave the process.
 *
 * The enforcement matrix (`docs/CONTRACTS.md`, Contract 2), implemented
 * literally in `unwrap`:
 *
 * | value → provider egress    | device | trusted-network            | public-cloud     |
 * |----------------------------|--------|----------------------------|------------------|
 * | non-sensitive              | allow  | allow                      | allow            |
 * | Sensitive, no redactor     | allow  | allow if policy ≥ trusted  | **throw E301**   |
 * | Sensitive, redactor        | raw    | raw or redacted per policy | redacted only    |
 *
 * Plus one rule above the table: any provider whose egress class is broader
 * than the project's `[policy] egress` limit is refused outright (**E303**),
 * sensitive value or not. That check is `checkProvider`, and the sensitive
 * path runs it first — a declared redactor cannot buy egress the project
 * never allowed.
 *
 * Threat model, honestly: this defends against accident, not malice.
 * `#value` is a real private field, so it is harder to reach than Python's
 * name-mangled slot, but a determined developer can still call
 * `dangerouslyReveal()`. What stops that in practice is review: the only
 * sanctioned raw accessor is named so that grepping for it is a complete
 * audit. Declared-capability spoofing is out of scope — `Policy` trusts
 * `provider.capabilities().egress`, and vetting providers is vetting code
 * you install.
 */

import { PolicyViolation } from "../contracts/errors.js";
import type { PolicyContext, Redactor } from "../contracts/policy.js";
import { Sensitive } from "../contracts/policy.js";
import { EgressClass, isEgressClass, Provider } from "../contracts/provider.js";
import { TraceEvent } from "../contracts/trace.js";

/**
 * Ordering of the three egress classes, narrowest first. The privacy
 * boundary is defined over these and nothing else (Contract 1).
 */
const RANK: Record<EgressClass, number> = {
  [EgressClass.DEVICE]: 0,
  [EgressClass.TRUSTED_NETWORK]: 1,
  [EgressClass.PUBLIC_CLOUD]: 2,
};

const REDACTOR_FIX =
  'declare a redactor in ack.toml:\n\n           [policy]\n           redactor = "healthcare.phi"\n\n' +
  "       ...or route this call to a device/trusted-network provider.";

function rank(egress: EgressClass): number {
  return RANK[egress];
}

function providerName(provider: Provider): string {
  return String(provider?.name ?? "<unnamed provider>");
}

/** Egress class a provider declares. Never inferred (invariant 2). */
function providerEgress(provider: Provider): EgressClass {
  if (typeof provider?.capabilities !== "function") {
    throw new TypeError(
      `${providerName(provider)} does not satisfy the Provider interface ` +
        "(needs .name and .capabilities())",
    );
  }
  const egress = provider.capabilities().egress;
  if (!isEgressClass(egress)) {
    throw new TypeError(`${providerName(provider)} declares unknown egress class '${egress}'`);
  }
  return egress;
}

export type PolicyDecision = "allow" | "allow-redacted" | "deny";

export interface PolicyOptions {
  /** The project's `[policy] egress` limit. */
  egress?: EgressClass;
  /** name → Redactor. Implementations live in packs. */
  redactors?: Record<string, Redactor> | Map<string, Redactor>;
  /** `[policy] redactor` — the one that applies without a label match. */
  defaultRedactor?: string | null;
  /**
   * Resolves the matrix's "raw or redacted per policy" cell: by default
   * redaction is applied only where the contract requires it
   * (`public-cloud`). Set to `trusted-network` to redact for self-hosted
   * destinations too.
   */
  redactAtOrAbove?: EgressClass;
  emit?: ((event: TraceEvent) => void) | null;
  runId?: string | null;
}

let spanCounter = 0;
function nextSpanId(): string {
  spanCounter += 1;
  return `policy-span-${spanCounter}`;
}

/** Egress policy for one project — the `PolicyContext` implementation. */
export class Policy implements PolicyContext {
  readonly egressLimit: EgressClass;
  readonly redactors: Map<string, Redactor>;
  readonly redactAtOrAbove: EgressClass;
  readonly defaultRedactor: string | null;
  readonly runId: string;
  readonly #emit: ((event: TraceEvent) => void) | null;

  constructor(options: PolicyOptions = {}) {
    this.egressLimit = options.egress ?? EgressClass.DEVICE;
    this.redactors =
      options.redactors instanceof Map
        ? new Map(options.redactors)
        : new Map(Object.entries(options.redactors ?? {}));
    this.redactAtOrAbove = options.redactAtOrAbove ?? EgressClass.PUBLIC_CLOUD;
    this.runId = options.runId ?? `policy-${Math.random().toString(16).slice(2, 14)}`;
    this.#emit = options.emit ?? null;

    let defaultRedactor = options.defaultRedactor ?? null;
    // Exactly one installed redactor and no named default: that one is the
    // only candidate, not a guess.
    if (defaultRedactor === null && this.redactors.size === 1) {
      defaultRedactor = [...this.redactors.keys()][0] as string;
    }
    if (defaultRedactor !== null && !this.redactors.has(defaultRedactor)) {
      throw new PolicyViolation(
        `redactor "${defaultRedactor}" is declared in ack.toml but no installed pack provides it`,
        {
          code: "E302",
          why: "the policy engine refuses to guess — a silently missing redactor would be an open boundary.",
          fix: "ack doctor --json | grep redactors   # then fix [policy] redactor",
          details: { declared: defaultRedactor, installed: [...this.redactors.keys()].sort() },
        },
      );
    }
    this.defaultRedactor = defaultRedactor;
  }

  // ── the boundary ────────────────────────────────────────────────────

  /**
   * Refuse a provider broader than the project's limit (**E303**).
   *
   * The non-sensitive path: it applies to every provider call, because a
   * project that declared `egress = "device"` did not agree to send
   * *anything* to a third party. Returns the provider's declared egress
   * class so callers can label their own trace events.
   */
  checkProvider(provider: Provider): EgressClass {
    const egress = this.#checkEgress(provider, null);
    this.#policyEvent({
      decision: "allow",
      reason:
        `provider egress ${egress} is within the project limit ` +
        `${this.egressLimit}; value is not Sensitive`,
      egress,
      provider,
      value: null,
    });
    return egress;
  }

  /**
   * Authorize (and if required redact) `value` for `provider`.
   *
   * The single sanctioned path from a `Sensitive` box to a string on the
   * wire: raw where the destination is inside the boundary, redacted where
   * it is not, and `PolicyViolation` where neither is permitted.
   */
  unwrap(value: Sensitive<unknown>, provider: Provider): unknown {
    if (!(value instanceof Sensitive)) {
      throw new TypeError(
        "Policy.unwrap() takes a Sensitive value. Non-sensitive values need " +
          "no authorization — call Policy.checkProvider(provider) instead.",
      );
    }

    const egress = this.#checkEgress(provider, value);
    const redactor = this.redactorFor(value);

    // Row: device — nothing leaves the machine, so raw is fine.
    if (egress === EgressClass.DEVICE) {
      return this.#allowRaw(
        value,
        provider,
        egress,
        "destination is device egress; the value never leaves the machine",
      );
    }

    // Row: trusted-network — reachable only because the project limit
    // already allows it (else #checkEgress threw E303 above).
    if (egress === EgressClass.TRUSTED_NETWORK) {
      if (redactor !== null && rank(this.redactAtOrAbove) <= rank(egress)) {
        return this.#allowRedacted(value, provider, egress, redactor);
      }
      return this.#allowRaw(
        value,
        provider,
        egress,
        `destination is trusted-network and the project limit (${this.egressLimit}) ` +
          "permits un-redacted egress there",
      );
    }

    // Row: public-cloud — redacted only, never raw.
    if (redactor === null) {
      this.#deny({
        message:
          `sensitive value "${value.label}" cannot reach public-cloud provider ` +
          `"${providerName(provider)}" un-redacted`,
        why:
          `it was created at ${value.origin} and no redactor is declared for it — ` +
          "sensitivity is a type, not a convention, so the engine refuses rather " +
          "than hoping the text is harmless.",
        fix: REDACTOR_FIX,
        code: "E301",
        value,
        provider,
        egress,
        reason: "public-cloud destination with no declared redactor",
      });
    }
    return this.#allowRedacted(value, provider, egress, redactor as Redactor);
  }

  /**
   * The redactor that would apply to `value`, if any.
   *
   * Resolution order: a redactor registered under the value's own label (so
   * one project can hold `"transcript"` to a stricter transform than the
   * rest), then the project default.
   */
  redactorFor(value: Sensitive<unknown>): Redactor | null {
    const specific = this.redactors.get(value.label);
    if (specific !== undefined) return specific;
    if (this.defaultRedactor !== null) {
      return this.redactors.get(this.defaultRedactor) as Redactor;
    }
    return null;
  }

  // ── internals ───────────────────────────────────────────────────────

  /** E303 gate: refuse anything broader than the project limit. */
  #checkEgress(provider: Provider, value: Sensitive<unknown> | null): EgressClass {
    const egress = providerEgress(provider);
    if (rank(egress) > rank(this.egressLimit)) {
      this.#deny({
        message:
          `provider "${providerName(provider)}" egresses to ${egress}, above ` +
          `this project's limit of ${this.egressLimit}`,
        why:
          "the project declared a stricter boundary than this provider satisfies; " +
          "the engine refuses rather than quietly widening it.",
        fix:
          "use a device/trusted-network provider, or raise the limit deliberately " +
          `in ack.toml:\n\n           [policy]\n           egress = "${egress}"`,
        code: "E303",
        value,
        provider,
        egress,
        reason: `provider egress ${egress} exceeds project limit ${this.egressLimit}`,
      });
    }
    return egress;
  }

  #allowRaw(
    value: Sensitive<unknown>,
    provider: Provider,
    egress: EgressClass,
    reason: string,
  ): unknown {
    this.#policyEvent({ decision: "allow", reason, egress, provider, value });
    return value.dangerouslyReveal();
  }

  #allowRedacted(
    value: Sensitive<unknown>,
    provider: Provider,
    egress: EgressClass,
    redactor: Redactor,
  ): string {
    const raw = value.dangerouslyReveal();
    if (typeof raw !== "string") {
      this.#deny({
        message: `sensitive value "${value.label}" holds ${typeof raw}, which cannot be redacted`,
        why:
          `it was created at ${value.origin}; redactors operate on text only, so ` +
          "the engine has no way to de-identify this value before it crosses the boundary.",
        fix:
          "render it to text first (and wrap the result in Sensitive), or keep this " +
          "call on a device/trusted-network provider.",
        code: "E301",
        value,
        provider,
        egress,
        reason: `non-string payload (${typeof raw}) cannot be redacted`,
      });
    }

    const [clean, redactions] = redactor.redact(raw as string);
    if (typeof clean !== "string") {
      throw new TypeError(
        `redactor "${redactor.name}" returned ${typeof clean}, expected string — ` +
          "the boundary cannot authorize output it cannot verify.",
      );
    }

    this.#redactionEvent(redactor, redactions);
    this.#policyEvent({
      decision: "allow-redacted",
      reason:
        `redacted by "${redactor.name}" before ${egress} egress ` +
        `(${redactions.length} span(s) replaced)`,
      egress,
      provider,
      value,
    });
    return clean;
  }

  #deny(args: {
    message: string;
    why: string;
    fix: string;
    code: string;
    value: Sensitive<unknown> | null;
    provider: Provider;
    egress: EgressClass;
    reason: string;
  }): never {
    this.#policyEvent({
      decision: "deny",
      reason: args.reason,
      egress: args.egress,
      provider: args.provider,
      value: args.value,
    });
    throw new PolicyViolation(args.message, {
      code: args.code,
      why: args.why,
      fix: args.fix,
      fieldName: args.value ? args.value.label : null,
      callSite: args.value ? args.value.origin : null,
      provider: providerName(args.provider),
    });
  }

  /**
   * Every decision — allowed or denied — emits a trace event. Neither the
   * raw value nor any redacted-away span is ever written into a payload:
   * the audit trail records *decisions*, never *data*.
   */
  #policyEvent(args: {
    decision: PolicyDecision;
    reason: string;
    egress: EgressClass;
    provider: Provider;
    value: Sensitive<unknown> | null;
  }): void {
    if (this.#emit === null) return;
    this.#emit(
      new TraceEvent({
        ts: Date.now() / 1000,
        runId: this.runId,
        spanId: nextSpanId(),
        parentSpanId: null,
        kind: "policy",
        egress: args.egress,
        bytesOut: 0,
        payload: {
          decision: args.decision,
          reason: args.reason,
          provider: providerName(args.provider),
          label: args.value ? args.value.label : null,
          call_site: args.value ? args.value.origin : null,
        },
      }),
    );
  }

  #redactionEvent(redactor: Redactor, redactions: { category: string }[]): void {
    if (this.#emit === null) return;
    const categories = [...new Set(redactions.map((r) => r.category))].sort();
    this.#emit(
      new TraceEvent({
        ts: Date.now() / 1000,
        runId: this.runId,
        spanId: nextSpanId(),
        parentSpanId: null,
        kind: "redaction",
        // Redacting egresses nothing; the bytes are counted by the provider
        // event that follows.
        egress: EgressClass.DEVICE,
        bytesOut: 0,
        payload: { redactor: redactor.name, categories, count: redactions.length },
      }),
    );
  }
}
