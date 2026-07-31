/**
 * Contract 4 — `TraceEvent`: one spine, four surfaces.
 *
 * Every model call, tool call, redaction, policy decision and error emits
 * exactly this shape. The debug console, audit log, eval harness and demo
 * UI all read it — including the "0 bytes egressed" panel, which is just
 * `sum(bytes_out) over events where egress != device === 0`.
 *
 * Wire format is JSONL; the schema is
 * `spec/schemas/trace-event.schema.json`.
 */

import { EgressClass } from "./provider.js";

export type EventKind = "model" | "tool" | "redaction" | "policy" | "error";

export const EVENT_KINDS: readonly EventKind[] = [
  "model",
  "tool",
  "redaction",
  "policy",
  "error",
];

export interface TraceEventInit {
  ts: number;
  runId: string;
  spanId: string;
  parentSpanId: string | null;
  kind: EventKind;
  egress: EgressClass;
  /** Bytes that left the process toward the provider for this event. */
  bytesOut: number;
  payload?: Record<string, unknown>;
}

/**
 * One trace record.
 *
 * @example
 * const e = new TraceEvent({ ts: 0, runId: "r1", spanId: "s1",
 *   parentSpanId: null, kind: "model", egress: "device", bytesOut: 0,
 *   payload: { model: "gemma4:e4b" } });
 * JSON.parse(e.toJson()).kind; // "model"
 */
export class TraceEvent {
  readonly ts: number;
  readonly runId: string;
  readonly spanId: string;
  readonly parentSpanId: string | null;
  readonly kind: EventKind;
  readonly egress: EgressClass;
  readonly bytesOut: number;
  readonly payload: Record<string, unknown>;

  constructor(init: TraceEventInit) {
    this.ts = init.ts;
    this.runId = init.runId;
    this.spanId = init.spanId;
    this.parentSpanId = init.parentSpanId;
    this.kind = init.kind;
    this.egress = init.egress;
    this.bytesOut = init.bytesOut;
    this.payload = init.payload ?? {};
  }

  toDict(): Record<string, unknown> {
    return {
      ts: this.ts,
      run_id: this.runId,
      span_id: this.spanId,
      parent_span_id: this.parentSpanId,
      kind: this.kind,
      egress: this.egress,
      bytes_out: this.bytesOut,
      payload: this.payload,
    };
  }

  /**
   * Canonical JSONL line: sorted keys, no whitespace drift — determinism is
   * invariant 4. Byte-compatible with Python's
   * `json.dumps(sort_keys=True, separators=(",", ":"))`, with one honest
   * caveat documented in `canonicalJson`.
   */
  toJson(): string {
    return canonicalJson(this.toDict());
  }

  static fromDict(d: Record<string, unknown>): TraceEvent {
    return new TraceEvent({
      ts: d["ts"] as number,
      runId: d["run_id"] as string,
      spanId: d["span_id"] as string,
      parentSpanId: (d["parent_span_id"] as string | null) ?? null,
      kind: d["kind"] as EventKind,
      egress: d["egress"] as EgressClass,
      bytesOut: d["bytes_out"] as number,
      payload: (d["payload"] as Record<string, unknown>) ?? {},
    });
  }
}

const ESCAPES: Record<string, string> = {
  "\\": "\\\\",
  '"': '\\"',
  "\b": "\\b",
  "\f": "\\f",
  "\n": "\\n",
  "\r": "\\r",
  "\t": "\\t",
};

/**
 * Python-compatible JSON string literal: `ensure_ascii=True`, so anything
 * outside the printable ASCII range is escaped as `\uXXXX`.
 */
function encodeString(value: string): string {
  let out = '"';
  for (const ch of value) {
    const escape = ESCAPES[ch];
    if (escape !== undefined) {
      out += escape;
      continue;
    }
    const code = ch.codePointAt(0) as number;
    if (code >= 0x20 && code <= 0x7e) {
      out += ch;
    } else if (code > 0xffff) {
      // surrogate pair, exactly as CPython writes it
      const adjusted = code - 0x10000;
      const hi = 0xd800 + (adjusted >> 10);
      const lo = 0xdc00 + (adjusted & 0x3ff);
      out += `\\u${hi.toString(16).padStart(4, "0")}\\u${lo.toString(16).padStart(4, "0")}`;
    } else {
      out += `\\u${code.toString(16).padStart(4, "0")}`;
    }
  }
  return out + '"';
}

/**
 * Deterministic JSON: sorted keys, `(",", ":")` separators, ASCII-escaped
 * strings — the encoding Contract 4 pins for the JSONL wire format.
 *
 * Honest caveat: JavaScript has exactly one number type, so an integral
 * float serializes as `1`, where Python writes `1.0`. Every other value
 * (including non-integral floats, which both languages render with the
 * shortest round-trip repr) is byte-identical. The conformance corpus
 * compares parsed JSON, where `1` and `1.0` are the same number.
 */
export function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      return value === Infinity ? "Infinity" : value === -Infinity ? "-Infinity" : "NaN";
    }
    return String(value);
  }
  if (typeof value === "string") return encodeString(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    const parts = keys
      .filter((k) => record[k] !== undefined)
      .map((k) => `${encodeString(k)}:${canonicalJson(record[k])}`);
    return `{${parts.join(",")}}`;
  }
  throw new TypeError(`cannot serialize ${typeof value} into canonical JSON`);
}

/**
 * Total bytes that left the device across `events` — the exact definition
 * behind the "0 bytes egressed" panel.
 */
export function bytesEgressed(events: readonly TraceEvent[]): number {
  return events
    .filter((e) => e.egress !== EgressClass.DEVICE)
    .reduce((total, e) => total + e.bytesOut, 0);
}
