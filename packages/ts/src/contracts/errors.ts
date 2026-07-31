/**
 * Error contract — stable codes, honest messages, literal fixes.
 *
 * Mirror of `agenticcarekit.kernel.contracts.errors`. Every error carries a
 * stable searchable code (`E203`), what happened, why, and the literal
 * command that fixes it. The long-form registry lives in `spec/errors.json`
 * and is shared by every implementation — a code raised but not registered
 * is a bug, not a style choice.
 *
 * Code ranges:
 *   E0xx bootstrap/environment · E1xx model/provider/network ·
 *   E2xx capability · E3xx policy · E4xx config · E5xx generation · E6xx eval
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** One entry from the shared error registry (`spec/errors.json`). */
export interface ErrorEntry {
  code: string;
  title: string;
  what: string;
  why: string;
  fix: string;
}

export interface AckErrorOptions {
  code?: string;
  why?: string | null;
  fix?: string | null;
  details?: Record<string, unknown>;
}

/**
 * Base for every agenticcarekit error.
 *
 * @example
 * const err = new AckError("boom", { code: "E999", why: "because", fix: "ack doctor" });
 * err.code;                      // "E999"
 * err.render().includes("ack doctor"); // true
 */
export class AckError extends Error {
  readonly code: string;
  readonly why: string | null;
  readonly fix: string | null;
  readonly details: Record<string, unknown>;

  constructor(message: string, options: AckErrorOptions = {}) {
    super(message);
    this.name = "AckError";
    this.code = options.code ?? "E000";
    this.why = options.why ?? null;
    this.fix = options.fix ?? null;
    this.details = options.details ?? {};
  }

  /** The message, without Error's class-name prefix. */
  get messageText(): string {
    return this.message;
  }

  /** Plain-text rendering (no colour; the CLI layers colour on top). */
  render(): string {
    const lines = [`✗ ${this.code}  ${this.message}`];
    if (this.why) lines.push(`       ${this.why}`);
    if (this.fix) {
      lines.push("");
      lines.push(`       ${this.fix}`);
    }
    return lines.join("\n");
  }

  /** Machine-readable shape used by `--json` output and MCP. */
  toDict(): Record<string, unknown> {
    return {
      code: this.code,
      message: this.message,
      why: this.why,
      fix: this.fix,
      details: this.details,
    };
  }
}

export interface CapabilityMismatchOptions extends AckErrorOptions {
  missing?: string[];
  candidates?: string[];
}

/**
 * E2xx — a blueprint or request requires something the model lacks.
 *
 * Raised before any network call. Must name what is missing and which
 * models have it (invariant 2: never silently degrade).
 */
export class CapabilityMismatch extends AckError {
  readonly missing: string[];
  readonly candidates: string[];

  constructor(message: string, options: CapabilityMismatchOptions = {}) {
    const missing = options.missing ?? [];
    const candidates = options.candidates ?? [];
    super(message, {
      ...options,
      code: options.code ?? "E200",
      details: { ...(options.details ?? {}), missing, candidates },
    });
    this.name = "CapabilityMismatch";
    this.missing = missing;
    this.candidates = candidates;
  }
}

export interface PolicyViolationOptions extends AckErrorOptions {
  fieldName?: string | null;
  callSite?: string | null;
  provider?: string | null;
}

/**
 * E3xx — sensitive data was about to cross a disallowed egress boundary.
 *
 * Must name the exact call site and field: a vague policy error is one
 * nobody fixes (Contract 2).
 */
export class PolicyViolation extends AckError {
  readonly fieldName: string | null;
  readonly callSite: string | null;
  readonly provider: string | null;

  constructor(message: string, options: PolicyViolationOptions = {}) {
    const fieldName = options.fieldName ?? null;
    const callSite = options.callSite ?? null;
    const provider = options.provider ?? null;
    super(message, {
      ...options,
      code: options.code ?? "E301",
      details: {
        ...(options.details ?? {}),
        field: fieldName,
        call_site: callSite,
        provider,
      },
    });
    this.name = "PolicyViolation";
    this.fieldName = fieldName;
    this.callSite = callSite;
    this.provider = provider;
  }
}

// ── the shared registry ──────────────────────────────────────────────────

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * Candidate locations for `spec/errors.json`: every ancestor directory of
 * this module, nearest first.
 *
 * The registry is shared, never vendored (spec/README.md): a port that
 * copies it has forked the spec. Walking up finds it whether the package is
 * running from `src/`, from `dist/`, or from an install that ships `spec/`
 * beside the package root.
 */
function registryCandidates(): string[] {
  const paths: string[] = [];
  let dir = HERE;
  for (let i = 0; i < 10; i += 1) {
    paths.push(resolve(dir, "spec", "errors.json"));
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return paths;
}

let REGISTRY: Map<string, ErrorEntry> | null = null;

/** Load (and cache) the shared error registry from `spec/errors.json`. */
export function errorRegistry(): Map<string, ErrorEntry> {
  if (REGISTRY !== null) return REGISTRY;
  let raw: string | null = null;
  for (const candidate of registryCandidates()) {
    try {
      raw = readFileSync(candidate, "utf-8");
      break;
    } catch {
      // try the next location
    }
  }
  if (raw === null) {
    throw new AckError("spec/errors.json is not readable from this installation", {
      code: "E001",
      why: "the shared error registry is the source of truth for every code; it is never vendored per language.",
      fix: "run from a repo checkout, or ship spec/errors.json alongside the package",
    });
  }
  const doc = JSON.parse(raw) as { errors: ErrorEntry[] };
  const map = new Map<string, ErrorEntry>();
  for (const entry of doc.errors) {
    map.set(entry.code, {
      code: entry.code,
      title: entry.title,
      what: entry.what,
      why: entry.why,
      fix: entry.fix,
    });
  }
  REGISTRY = map;
  return map;
}

/**
 * Long-form explanation for a code, or `undefined` if unregistered.
 *
 * @example
 * explain("E203")?.title; // "Model does not support a required input modality"
 */
export function explain(code: string): ErrorEntry | undefined {
  return errorRegistry().get(code.toUpperCase());
}
