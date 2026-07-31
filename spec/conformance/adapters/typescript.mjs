#!/usr/bin/env node
/**
 * Conformance adapter for the TypeScript implementation (`packages/ts`).
 *
 * Speaks the JSON-lines adapter protocol (`spec/conformance/README.md`) and
 * maps each area onto the real code — never onto a reimplementation of it.
 * An adapter that reproduces the logic it is meant to verify proves nothing:
 *
 *     message-build           -> kernel/builder.buildOllamaChat
 *     capability-negotiation  -> contracts.Capabilities.missing / kernel/models.ensureSupported
 *     policy                  -> kernel/policy.Policy
 *     trace-shape             -> contracts.TraceEvent + spec/schemas/trace-event.schema.json
 *     config                  -> kernel/config.AckConfig
 *
 * The only logic that lives here is the two spec-defined fixture redactors,
 * which the corpus explicitly requires adapters to implement locally.
 *
 * Usage:
 *     node spec/conformance/adapters/typescript.mjs --describe
 *     node spec/conformance/adapters/typescript.mjs < cases.jsonl > results.jsonl
 */

import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SPEC_ROOT = join(HERE, "..", "..");
const PACKAGE_ROOT = join(SPEC_ROOT, "..", "packages", "ts");

const AREAS = ["message-build", "capability-negotiation", "policy", "trace-shape", "config"];

/**
 * Areas the implementation cannot currently serve, with the reason. Each
 * area is independent: the adapter serves whatever has been built. Nothing
 * here fakes a result to make a suite go green.
 */
const UNAVAILABLE = {};

let ack = null;
try {
  ack = await import(join(PACKAGE_ROOT, "dist", "src", "index.js"));
} catch (exc) {
  const reason =
    `packages/ts is not built (${exc.message}). ` +
    "Run: cd packages/ts && npm install && npm run build";
  for (const area of AREAS) UNAVAILABLE[area] = reason;
}

// ── request decoding (the documented JSON encoding of GenerateRequest) ────

/**
 * `data_b64` passes through as a string; `data_utf8` becomes raw bytes.
 * The distinction is the point of rule 6: a base64 string must not be
 * re-encoded, raw bytes must be.
 */
function media(part) {
  if ("data_b64" in part) return String(part.data_b64);
  return new TextEncoder().encode(String(part.data_utf8));
}

function decodePart(part) {
  switch (part.type) {
    case "text":
      return ack.textPart(part.text);
    case "image":
      return ack.imagePart(media(part), part.detail ?? "default");
    case "audio":
      return ack.audioPart(media(part), part.format ?? "wav");
    default:
      throw new Error(`unknown part type '${part.type}'`);
  }
}

function decodeMessage(msg) {
  return new ack.Message({
    role: msg.role,
    parts: (msg.parts ?? []).map(decodePart),
    thinking: msg.thinking ?? null,
    toolCalls: (msg.tool_calls ?? []).map((tc) => ({
      id: tc.id,
      name: tc.name,
      arguments: tc.arguments ?? {},
    })),
    toolCallId: msg.tool_call_id ?? null,
  });
}

/**
 * A ToolSpec built straight from the fixture declaration.
 *
 * Fixtures carry `{name, description, parameters}` — the language-neutral
 * core of a tool. The wrapping into `{"type": "function", ...}` is what
 * `asFunctionSchema()` owns and what the fixtures assert.
 */
function decodeToolSpec(decl) {
  const unused = () => {
    throw new Error("conformance tools are declarations only");
  };
  return new ack.ToolSpec({
    name: decl.name,
    description: decl.description ?? "",
    jsonSchema: decl.parameters ?? { type: "object", properties: {} },
    permissions: decl.permissions ?? [],
    fn: unused,
    mock: unused,
  });
}

function decodeRequest(spec, model = null) {
  return new ack.GenerateRequest({
    messages: (spec.messages ?? []).map(decodeMessage),
    model,
    tools: (spec.tools ?? []).map(decodeToolSpec),
    think: Boolean(spec.think ?? false),
    temperature: spec.temperature ?? null,
    topP: spec.top_p ?? null,
    topK: spec.top_k ?? null,
    maxTokens: spec.max_tokens ?? null,
    stop: spec.stop ?? [],
  });
}

function decodeCapabilities(spec) {
  return new ack.Capabilities({
    modalitiesIn: spec.modalities_in,
    modalitiesOut: spec.modalities_out,
    toolCalling: Boolean(spec.tool_calling),
    streaming: Boolean(spec.streaming),
    contextTokens: Number(spec.context_tokens),
    thinking: Boolean(spec.thinking),
    egress: spec.egress,
  });
}

// ── fixture redactors (defined by the spec, not by any pack) ──────────────

const DIGIT_RUN = /\d+/g;

/**
 * Declared, runs, replaces nothing. Proves that the boundary condition is
 * "a redactor was declared and ran", not "the text changed".
 */
const passthroughRedactor = {
  name: "passthrough",
  redact(text) {
    return [text, []];
  },
};

/**
 * Every ASCII digit becomes `#`; one Redaction per maximal digit run.
 * Deterministic in any language, and enough of a transform that a redacted
 * payload is unmistakable in a fixture.
 */
const maskDigitsRedactor = {
  name: "mask-digits",
  redact(text) {
    const spans = [];
    for (const match of text.matchAll(DIGIT_RUN)) {
      const start = match.index;
      const end = start + match[0].length;
      spans.push({ category: "DIGITS", start, end, replacement: "#".repeat(end - start) });
    }
    return [text.replace(DIGIT_RUN, (run) => "#".repeat(run.length)), spans];
  },
};

const REDACTORS = { passthrough: passthroughRedactor, "mask-digits": maskDigitsRedactor };

/**
 * The smallest thing satisfying the Provider interface. Policy only ever
 * reads the egress class, so the generation methods exist to satisfy the
 * interface and nothing else.
 */
function stubProvider(name, egress) {
  const caps = new ack.Capabilities({
    modalitiesIn: ["text"],
    modalitiesOut: ["text"],
    toolCalling: true,
    streaming: true,
    contextTokens: 131072,
    thinking: true,
    egress,
  });
  return {
    name,
    capabilities: () => caps,
    generate() {
      throw new Error("conformance never generates");
    },
  };
}

// ── area handlers ────────────────────────────────────────────────────────

function runMessageBuild(input) {
  const req = decodeRequest(input.request ?? {}, input.model);
  return ack.buildOllamaChat(req, input.model);
}

function runCapabilityNegotiation(input) {
  const caps = decodeCapabilities(input.capabilities);
  if ("request" in input) {
    const model = input.model ?? "unknown:model";
    ack.ensureSupported(model, caps, decodeRequest(input.request, model));
    return { ok: true };
  }
  const reqs = input.requirements ?? {};
  return {
    missing: caps.missing({
      modalitiesIn: reqs.modalities_in ?? [],
      modalitiesOut: reqs.modalities_out ?? [],
      toolCalling: Boolean(reqs.tool_calling ?? false),
      streaming: Boolean(reqs.streaming ?? false),
      contextTokens: Number(reqs.context_tokens ?? 0),
      thinking: Boolean(reqs.thinking ?? false),
    }),
  };
}

/**
 * Construct the policy engine from a fixture's `policy` block. `[policy]
 * redactor` names one redactor out of those a project has installed; the
 * fixtures install exactly the one they name. No emitter is attached — the
 * harness asserts decisions, not trace plumbing.
 */
function buildPolicy(spec) {
  const name = spec.redactor ?? null;
  return new ack.Policy({
    egress: spec.egress ?? "device",
    redactors: name ? { [name]: REDACTORS[name] } : {},
    defaultRedactor: name,
  });
}

function runPolicy(input) {
  const policy = buildPolicy(input.policy ?? {});
  const provider = stubProvider(input.provider.name, input.provider.egress);
  const value = input.value;

  if (value.sensitive === false) {
    // The E303 ceiling applies to non-sensitive traffic too, so it cannot
    // travel through unwrap(): checkProvider is the pinned entry point for
    // the value-free half of the boundary.
    if (typeof policy.checkProvider !== "function") {
      throw new AdapterGap("kernel/policy.Policy exposes no checkProvider() egress pre-check");
    }
    policy.checkProvider(provider);
    return { text: value.text };
  }

  const sensitive = new ack.Sensitive(value.text, value.label ?? "sensitive");
  return { text: sensitive.unwrapFor(provider, policy) };
}

let TRACE_SCHEMA = null;
function traceSchema() {
  if (TRACE_SCHEMA === null) {
    TRACE_SCHEMA = JSON.parse(
      readFileSync(join(SPEC_ROOT, "schemas", "trace-event.schema.json"), "utf-8"),
    );
  }
  return TRACE_SCHEMA;
}

function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (typeof a !== "object") return false;
  const ak = Object.keys(a).sort();
  const bk = Object.keys(b).sort();
  return (
    ak.length === bk.length &&
    ak.every((k, i) => k === bk[i]) &&
    ak.every((k) => deepEqual(a[k], b[k]))
  );
}

function runTraceShape(input) {
  if ("events" in input) {
    const events = input.events.map((e) => ack.TraceEvent.fromDict(e));
    return { bytes_egressed: ack.bytesEgressed(events) };
  }
  const event = input.event;
  if (ack.validate(event, traceSchema()).length > 0) return { valid: false };
  // A schema-valid event must also survive the round trip: a schema that has
  // drifted from the contract is exactly what this suite exists to catch.
  if (!deepEqual(ack.TraceEvent.fromDict(event).toDict(), event)) return { valid: false };
  return { valid: true };
}

function normalizedConfig(cfg) {
  return {
    blueprint: cfg.blueprint,
    pack: cfg.pack,
    model_primary: { provider: cfg.modelPrimary.provider, model: cfg.modelPrimary.model },
    model_fallback: cfg.modelFallback
      ? { provider: cfg.modelFallback.provider, model: cfg.modelFallback.model }
      : null,
    egress: cfg.egress,
    redactor: cfg.redactor,
    capabilities: [...cfg.capabilities],
    raw_keys: Object.keys(cfg.raw).sort(),
  };
}

function runConfig(input) {
  const cfg = ack.AckConfig.parse(input.toml);
  if (input.mode !== "fixpoint") return normalizedConfig(cfg);

  const once = cfg.toToml();
  const roundTripped = ack.AckConfig.parse(once);
  const twice = roundTripped.toToml();
  const again = ack.AckConfig.parse(twice);
  const fixpoint =
    once === twice && deepEqual(normalizedConfig(roundTripped), normalizedConfig(again));
  return { fixpoint, normalized: normalizedConfig(roundTripped) };
}

const HANDLERS = {
  "message-build": runMessageBuild,
  "capability-negotiation": runCapabilityNegotiation,
  policy: runPolicy,
  "trace-shape": runTraceShape,
  config: runConfig,
};

/**
 * The implementation is missing a surface the suite needs to exercise.
 * Reported as an `EADAPTER` error so the case fails with the missing name in
 * the diff — never as a skip, which would hide the gap.
 */
class AdapterGap extends Error {}

// ── protocol loop ────────────────────────────────────────────────────────

function handle(kase) {
  const id = kase.id ?? "<unknown>";
  const area = kase.area;
  if (area in UNAVAILABLE) return { id, unsupported: UNAVAILABLE[area] };
  const handler = HANDLERS[area];
  if (handler === undefined) return { id, unsupported: `unknown area '${area}'` };
  try {
    return { id, output: handler(kase.input) };
  } catch (exc) {
    if (exc instanceof AdapterGap) {
      return { id, error: { code: "EADAPTER", message: exc.message } };
    }
    if (exc && typeof exc.code === "string" && exc.code.startsWith("E")) {
      return { id, error: { code: exc.code, message: exc.message } };
    }
    return {
      id,
      error: {
        code: "EUNCAUGHT",
        message: `${exc?.name ?? "Error"}: ${exc?.message ?? String(exc)}`,
        traceback: String(exc?.stack ?? "").split("\n").slice(0, 6).join("\n"),
      },
    };
  }
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) out[key] = canonical(value[key]);
    return out;
  }
  return value;
}

if (process.argv.includes("--describe")) {
  process.stdout.write(
    JSON.stringify(
      canonical({
        name: "typescript",
        language: "typescript",
        areas: AREAS.filter((a) => !(a in UNAVAILABLE)),
        unavailable: UNAVAILABLE,
      }),
    ) + "\n",
  );
} else {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    process.stdout.write(JSON.stringify(canonical(handle(JSON.parse(line)))) + "\n");
  }
}
