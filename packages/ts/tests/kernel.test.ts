/**
 * Kernel unit tests.
 *
 * The conformance corpus is the ground truth for the kernel, so these tests
 * cover only what a corpus of JSON fixtures cannot carry: rule 6's
 * filesystem branch, the TOML reader's refusals, and the policy engine's
 * constructor-time checks.
 */

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  AckConfig,
  AckError,
  EgressClass,
  GenerateRequest,
  Message,
  Policy,
  PolicyViolation,
  Sensitive,
  TomlParseError,
  buildOllamaChat,
  encodeMedia,
  imagePart,
  parseToml,
  splitThinking,
} from "../src/index.js";

function provider(name: string, egress: EgressClass) {
  return {
    name,
    capabilities: () =>
      ({ egress }) as unknown as ReturnType<
        import("../src/index.js").Provider["capabilities"]
      >,
    generate() {
      throw new Error("unused");
    },
  };
}

describe("encodeMedia (rule 6)", () => {
  it("base64-encodes raw bytes", () => {
    assert.equal(encodeMedia(new TextEncoder().encode("IMG")), "SU1H");
  });

  it("passes an already-base64 string through untouched", () => {
    assert.equal(encodeMedia("SU1H"), "SU1H");
  });

  it("reads and encodes a string that names a file on disk", () => {
    // Rule 6's third branch: a corpus of JSON fixtures cannot carry a
    // filesystem, so the spec hands this case to implementations.
    const dir = mkdtempSync(join(tmpdir(), "ack-media-"));
    const path = join(dir, "scan.bin");
    writeFileSync(path, Buffer.from("IMG"));
    assert.equal(encodeMedia(path), "SU1H");
  });

  it("builds a payload from a file-backed image part", () => {
    const dir = mkdtempSync(join(tmpdir(), "ack-media-"));
    const path = join(dir, "xray.bin");
    writeFileSync(path, Buffer.from("XRAY"));
    const payload = buildOllamaChat(
      new GenerateRequest({
        messages: [new Message({ role: "user", parts: [imagePart(path, "ocr")] })],
      }),
      "gemma4:e4b",
    );
    const messages = payload["messages"] as Record<string, unknown>[];
    assert.deepEqual(messages[0]?.["images"], ["WFJBWQ=="]);
    assert.equal((payload["options"] as Record<string, unknown>)["vision_tokens"], 1120);
  });
});

describe("splitThinking", () => {
  it("lifts an inline thought block out of the text", () => {
    assert.deepEqual(splitThinking("<|think|>weigh it<|/think|>Answer: 4"), [
      "Answer: 4",
      "weigh it",
    ]);
  });

  it("passes a sidecar thought through unchanged", () => {
    assert.deepEqual(splitThinking("plain", "sidecar thought"), ["plain", "sidecar thought"]);
  });
});

describe("TOML reader", () => {
  it("parses the ack.toml grammar", () => {
    assert.deepEqual(
      parseToml('[project]\nblueprint = "on-device"  # a comment\n\n[capabilities]\nenabled = [\n  "voice",\n]\n'),
      { project: { blueprint: "on-device" }, capabilities: { enabled: ["voice"] } },
    );
  });

  it("throws on an unterminated table header", () => {
    assert.throws(() => parseToml("[project\nblueprint = \"x\"\n"), TomlParseError);
  });

  it("throws on a duplicate key rather than silently keeping one", () => {
    assert.throws(() => parseToml('[a]\nk = "1"\nk = "2"\n'), TomlParseError);
  });

  it("refuses grammar it does not implement instead of guessing", () => {
    assert.throws(() => parseToml("[[servers]]\nname = 'x'\n"), TomlParseError);
    assert.throws(() => parseToml('a = """multi"""\n'), TomlParseError);
  });
});

describe("AckConfig", () => {
  it("round trips to a byte-identical fixpoint", () => {
    const cfg = AckConfig.parse(
      '[project]\nblueprint = "on-device"\npack = "healthcare"\n\n[model]\nprimary = "ollama:gemma4:e4b"\n',
    );
    const once = cfg.toToml();
    const twice = AckConfig.parse(once).toToml();
    assert.equal(once, twice);
  });

  it("preserves unknown user sections", () => {
    const cfg = AckConfig.parse(
      '[project]\nblueprint = "on-device"\npack = "healthcare"\n\n[model]\nprimary = "ollama:gemma4:e4b"\n\n[team]\noncall = "dr-lin"\n',
    );
    assert.deepEqual(Object.keys(cfg.raw).sort(), ["model", "project", "team"]);
  });

  it("names the missing key in an E402", () => {
    assert.throws(
      () => AckConfig.parse('[project]\nblueprint = "x"\npack = "y"\n'),
      (err: AckError) => err.code === "E402" && err.message.includes("[model]"),
    );
  });

  it("reports a missing file as E404, not a stack trace", () => {
    assert.throws(
      () => AckConfig.load(join(tmpdir(), "definitely-not-here", "ack.toml")),
      (err: AckError) => err.code === "E404",
    );
  });
});

describe("Policy", () => {
  it("refuses a declared default redactor that is not installed (E302)", () => {
    assert.throws(
      () => new Policy({ redactors: {}, defaultRedactor: "healthcare.phi" }),
      (err: PolicyViolation) => err.code === "E302",
    );
  });

  it("adopts the only installed redactor as the default", () => {
    const only = { name: "demo.mask", redact: (t: string) => [t, []] as [string, never[]] };
    assert.equal(new Policy({ redactors: { "demo.mask": only } }).defaultRedactor, "demo.mask");
  });

  it("carries field, call site and provider on a violation", () => {
    const policy = new Policy({ egress: EgressClass.PUBLIC_CLOUD });
    const note = new Sensitive("MRN 12345", "intake_note");
    assert.throws(
      () => note.unwrapFor(provider("cerebras", EgressClass.PUBLIC_CLOUD), policy),
      (err: PolicyViolation) =>
        err.code === "E301" &&
        err.fieldName === "intake_note" &&
        err.provider === "cerebras" &&
        /kernel\.test\.[jt]s:\d+$/.test(err.callSite ?? ""),
    );
  });

  it("emits a trace event for every decision, allowed or denied", () => {
    const events: { kind: string; payload: Record<string, unknown> }[] = [];
    const policy = new Policy({
      egress: EgressClass.DEVICE,
      emit: (e) => events.push({ kind: e.kind, payload: e.payload }),
    });
    new Sensitive("MRN 12345", "note").unwrapFor(provider("ollama", EgressClass.DEVICE), policy);
    assert.equal(events.length, 1);
    assert.equal(events[0]?.kind, "policy");
    assert.equal(events[0]?.payload["decision"], "allow");
    // The audit trail records decisions, never data.
    assert.equal(JSON.stringify(events[0]).includes("12345"), false);
  });

  it("refuses to unwrap a value that is not Sensitive", () => {
    assert.throws(
      () =>
        (new Policy() as unknown as { unwrap: (v: unknown, p: unknown) => unknown }).unwrap(
          "plain",
          provider("ollama", EgressClass.DEVICE),
        ),
      TypeError,
    );
  });
});
