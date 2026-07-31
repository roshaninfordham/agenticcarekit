/**
 * Contract 2 — `Sensitive<T>`, `PolicyContext`, `Redactor`.
 *
 * Sensitivity is a type, not a convention (invariant 1). A `Sensitive<T>`
 * value cannot reach a `public-cloud` provider without a declared redactor,
 * and that is enforced at runtime by the policy engine
 * (`kernel/policy.ts`), not by a comment.
 *
 * Design split, identical to the Python contract:
 *   * `Sensitive` is a sealed box. It stores the value, captures the call
 *     site where it was constructed, masks itself in string/inspect/JSON
 *     conversions, and refuses casual access.
 *   * `PolicyContext` owns ALL enforcement. `Sensitive.unwrapFor` delegates
 *     to it — there is exactly one enforcement path.
 */

import type { Provider } from "./provider.js";

/** One span a redactor replaced. Emitted into the trace (kind="redaction"). */
export interface Redaction {
  /** e.g. "NAME", "MRN", "DATE", "PHONE" */
  readonly category: string;
  readonly start: number;
  readonly end: number;
  readonly replacement: string;
}

export function redaction(
  category: string,
  start: number,
  end: number,
  replacement: string,
): Redaction {
  return { category, start, end, replacement };
}

/**
 * De-identification transform. Implementations live in packs (e.g.
 * `healthcare.phi` covers the 18 HIPAA identifiers); the kernel owns only
 * the boundary they plug into.
 */
export interface Redactor {
  readonly name: string;
  redact(text: string): [string, Redaction[]];
}

/**
 * The enforcement engine. One implementation (`kernel/policy.ts`) is the
 * only code path that reveals Sensitive values headed for a provider.
 */
export interface PolicyContext {
  /** Most permissive egress class this context allows un-redacted. */
  readonly egressLimit: unknown;
  /**
   * Authorize (and possibly redact) `value` for `provider`.
   *
   * Must throw `PolicyViolation` (E3xx) carrying the wrapper's origin,
   * label and provider name when egress is disallowed and no redactor is
   * declared, and must emit a trace event for every decision.
   */
  unwrap(value: Sensitive<unknown>, provider: Provider): unknown;
  /** The redactor that would apply to this value, if any. */
  redactorFor(value: Sensitive<unknown>): Redactor | null;
}

const NODE_INSPECT = Symbol.for("nodejs.util.inspect.custom");

/**
 * `file.ts:123` of the frame that constructed the Sensitive.
 *
 * This is what lets `PolicyViolation` name the exact call site. JavaScript
 * has no frame objects, so the site comes from an Error stack — same
 * information, different mechanism.
 */
function callerSite(): string {
  const stack = new Error().stack;
  if (!stack) return "<unknown>";
  const lines = stack.split("\n").slice(1);
  // frame 0 = callerSite, frame 1 = the Sensitive constructor, frame 2 = the caller
  const frame = lines[2] ?? lines[lines.length - 1];
  if (!frame) return "<unknown>";
  const match = /\(?((?:file:\/\/|\/|[A-Za-z]:\\)[^()]*?):(\d+):(\d+)\)?\s*$/.exec(frame.trim());
  if (!match) return "<unknown>";
  const [, rawFile, line] = match;
  const file = (rawFile ?? "").startsWith("file://")
    ? decodeURIComponent(new URL(rawFile as string).pathname)
    : (rawFile ?? "");
  return `${file}:${line}`;
}

/**
 * Wraps a value that must not reach public-cloud egress un-redacted.
 *
 * @example
 * const s = new Sensitive("John Smith, MRN 12345", "intake_note");
 * String(s).includes("John"); // false
 * s.label;                    // "intake_note"
 *
 * The wrapped value is reachable only through `unwrapFor` (the enforced
 * path) or the loudly-named `dangerouslyReveal`, which the policy engine
 * uses after authorization and which is greppable in review precisely
 * because of its name.
 */
export class Sensitive<T> {
  readonly label: string;
  readonly origin: string;
  readonly #value: T;

  constructor(value: T, label = "sensitive") {
    this.#value = value;
    this.label = label;
    this.origin = callerSite();
  }

  /**
   * Return the value (possibly redacted) if policy allows it for this
   * provider's egress class.
   *
   * Throws `PolicyViolation` — naming this wrapper's construction site, its
   * label and the offending provider — if egress is disallowed and no
   * redactor is declared. Never bypass this.
   */
  unwrapFor(provider: Provider, policy: PolicyContext): unknown {
    return policy.unwrap(this as Sensitive<unknown>, provider);
  }

  /**
   * Raw value, no policy check. For the policy engine after it has
   * authorized egress, and for code that stays on-device by construction.
   * The name is the audit trail.
   */
  dangerouslyReveal(): T {
    return this.#value;
  }

  /** Transform the inner value; the result stays Sensitive. */
  map<U>(fn: (value: T) => U): Sensitive<U> {
    return new Sensitive(fn(this.#value), this.label);
  }

  // ── leak resistance ────────────────────────────────────────────────

  toString(): string {
    return `Sensitive(<${this.label}>, origin=${this.origin})`;
  }

  [NODE_INSPECT](): string {
    return this.toString();
  }

  /**
   * Refuse structured serialization outright. `JSON.stringify` on a
   * Sensitive is the JavaScript shape of the accident Python blocks by
   * refusing to pickle: serialize the *authorized* form from `unwrapFor`
   * instead.
   */
  toJSON(): never {
    throw new TypeError(
      "Sensitive values cannot be serialized — serialize the redacted form " +
        "returned by unwrapFor() instead.",
    );
  }
}

export function isSensitive(value: unknown): value is Sensitive<unknown> {
  return value instanceof Sensitive;
}
