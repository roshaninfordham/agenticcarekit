/**
 * Contract-level unit tests: the behaviour the conformance corpus cannot
 * reach because it is language-local (masking, decoration-time failures,
 * canonical serialization).
 */

import assert from "node:assert/strict";
import { inspect } from "node:util";
import { describe, it } from "node:test";

import {
  AckError,
  Capabilities,
  Sensitive,
  TraceEvent,
  canonicalJson,
  errorRegistry,
  explain,
  tool,
} from "../src/index.js";

describe("Sensitive", () => {
  it("masks the value in every string conversion", () => {
    const s = new Sensitive("John Smith, MRN 12345", "intake_note");
    assert.equal(String(s).includes("John"), false);
    assert.equal(`${s}`.includes("12345"), false);
    assert.equal(inspect(s).includes("John"), false);
    assert.match(String(s), /^Sensitive\(<intake_note>, origin=/);
  });

  it("captures the construction call site in origin", () => {
    const s = new Sensitive("x");
    assert.match(s.origin, /contracts\.test\.[jt]s:\d+$/);
  });

  it("refuses structured serialization", () => {
    const s = new Sensitive("secret");
    assert.throws(() => JSON.stringify(s), TypeError);
  });

  it("reveals only through the loudly-named accessor", () => {
    const s = new Sensitive("secret", "note");
    assert.equal(s.dangerouslyReveal(), "secret");
  });

  it("keeps mapped values inside the box", () => {
    const s = new Sensitive("abc").map((v) => v.toUpperCase());
    assert.ok(s instanceof Sensitive);
    assert.equal(s.dangerouslyReveal(), "ABC");
  });
});

describe("tool()", () => {
  it("refuses a tool with no mock at declaration time (E502)", () => {
    assert.throws(
      () => tool({ name: "lookup", fn: () => null } as never),
      (err: AckError) => err.code === "E502",
    );
  });

  it("refuses an unknown permission (E503)", () => {
    assert.throws(
      () => tool({ name: "lookup", permissions: ["telepathy"], fn: () => null, mock: () => null }),
      (err: AckError) => err.code === "E503",
    );
  });

  it("emits the four artifacts", () => {
    const t = tool({
      name: "add",
      description: "Add two integers.",
      parameters: {
        type: "object",
        properties: { a: { type: "integer" }, b: { type: "integer" } },
        required: ["a", "b"],
      },
      permissions: ["network"],
      fn: (args) => (args["a"] as number) + (args["b"] as number),
      mock: () => 3,
    });
    assert.equal(t.spec.name, "add");
    assert.deepEqual([...t.spec.permissions], ["network"]);
    assert.equal(t.call({ a: 1, b: 2 }), 3);
    assert.equal(t.spec.mock({}), 3);
    assert.deepEqual(t.asFunctionSchema(), {
      type: "function",
      function: {
        name: "add",
        description: "Add two integers.",
        parameters: {
          type: "object",
          properties: { a: { type: "integer" }, b: { type: "integer" } },
          required: ["a", "b"],
        },
      },
    });
    assert.equal(t.spec.toManifest()["has_mock"], true);
  });
});

describe("Capabilities.missing", () => {
  const caps = new Capabilities({
    modalitiesIn: ["text"],
    modalitiesOut: ["text"],
    toolCalling: false,
    streaming: false,
    contextTokens: 8192,
    thinking: false,
    egress: "device",
  });

  it("returns the contract gap strings in contract order", () => {
    assert.deepEqual(
      caps.missing({
        modalitiesIn: ["image", "audio", "text"],
        modalitiesOut: ["audio", "image"],
        toolCalling: true,
        streaming: true,
        contextTokens: 131072,
        thinking: true,
      }),
      [
        "audio input",
        "image input",
        "audio output",
        "image output",
        "tool calling",
        "streaming",
        "context window (131072 needed, 8192 available)",
        "thinking",
      ],
    );
  });

  it("treats an absent requirement as 'not required'", () => {
    assert.deepEqual(caps.missing(), []);
  });
});

describe("TraceEvent", () => {
  const event = new TraceEvent({
    ts: 1767225601.5,
    runId: "run-01",
    spanId: "span-02",
    parentSpanId: "span-01",
    kind: "tool",
    egress: "device",
    bytesOut: 0,
    payload: { tool: "lookup_patient", ok: true },
  });

  it("serializes with sorted keys and compact separators", () => {
    assert.equal(
      event.toJson(),
      '{"bytes_out":0,"egress":"device","kind":"tool","parent_span_id":"span-01",' +
        '"payload":{"ok":true,"tool":"lookup_patient"},"run_id":"run-01",' +
        '"span_id":"span-02","ts":1767225601.5}',
    );
  });

  it("round trips through fromDict/toDict", () => {
    assert.deepEqual(TraceEvent.fromDict(event.toDict()).toDict(), event.toDict());
  });

  it("escapes non-ASCII the way Python's json.dumps does", () => {
    assert.equal(canonicalJson({ note: "café\n" }), '{"note":"caf\\u00e9\\n"}');
  });
});

describe("error registry", () => {
  it("loads the shared spec/errors.json, not a vendored copy", () => {
    assert.ok(errorRegistry().size > 0);
    assert.equal(explain("E203")?.code, "E203");
    assert.equal(explain("e203")?.code, "E203");
    assert.equal(explain("E999"), undefined);
  });

  it("renders the canonical CLI shape", () => {
    const err = new AckError("boom", { code: "E999", why: "because", fix: "ack doctor" });
    assert.match(err.render(), /✗ E999 {2}boom/);
    assert.ok(err.render().includes("ack doctor"));
  });
});
