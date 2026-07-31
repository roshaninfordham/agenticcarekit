/**
 * Capability unit tests — the ported half of `packages/agenticcarekit/capabilities`.
 *
 * These are the *only* verification the capability ports have; the
 * conformance corpus does not cover this area. README.md says so explicitly.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  AckError,
  AgentLoop,
  GenerateRequest,
  GenerateResponse,
  Provider,
  ToolCall,
  extract,
  tool,
} from "../src/index.js";

const PERSON_SCHEMA = {
  type: "object",
  properties: { name: { type: "string" }, age: { type: "integer" } },
  required: ["name", "age"],
  additionalProperties: false,
};

/** A provider that replays a fixed script of responses. */
function scripted(responses: GenerateResponse[]): Provider & { requests: GenerateRequest[] } {
  const requests: GenerateRequest[] = [];
  let index = 0;
  return {
    name: "scripted",
    requests,
    capabilities() {
      throw new Error("unused");
    },
    generate(req: GenerateRequest) {
      requests.push(req);
      const response = responses[Math.min(index, responses.length - 1)];
      index += 1;
      return response as GenerateResponse;
    },
  };
}

describe("extract", () => {
  it("returns validated data on the first attempt", async () => {
    const provider = scripted([{ text: '{"name": "Ada", "age": 36}' }]);
    assert.deepEqual(await extract(provider, PERSON_SCHEMA, "Ada is 36."), {
      name: "Ada",
      age: 36,
    });
    assert.equal(provider.requests.length, 1);
  });

  it("tolerates fenced JSON wrapped in prose", async () => {
    const provider = scripted([
      { text: 'Here you go:\n```json\n{"name": "Ada", "age": 36}\n```\n' },
    ]);
    assert.deepEqual(await extract(provider, PERSON_SCHEMA, "Ada is 36."), {
      name: "Ada",
      age: 36,
    });
  });

  it("repairs exactly once and succeeds", async () => {
    const provider = scripted([{ text: "not json at all" }, { text: '{"name":"Ada","age":36}' }]);
    assert.deepEqual(await extract(provider, PERSON_SCHEMA, "Ada is 36."), {
      name: "Ada",
      age: 36,
    });
    assert.equal(provider.requests.length, 2);
    // The repair prompt carries the malformed output and the errors verbatim.
    const repairPrompt = (provider.requests[1] as GenerateRequest).messages[0]?.parts[0];
    assert.equal(repairPrompt?.type, "text");
    assert.ok((repairPrompt as { text: string }).text.includes("not json at all"));
  });

  it("repairs a schema-invalid response, not just unparseable JSON", async () => {
    const provider = scripted([
      { text: '{"name": "Ada"}' },
      { text: '{"name": "Ada", "age": 36}' },
    ]);
    assert.deepEqual(await extract(provider, PERSON_SCHEMA, "Ada is 36."), {
      name: "Ada",
      age: 36,
    });
  });

  it("throws E504 after exactly two failures and never loops again", async () => {
    const provider = scripted([{ text: "garbage" }]);
    await assert.rejects(
      () => extract(provider, PERSON_SCHEMA, "Ada is 36."),
      (err: AckError) => err.code === "E504" && "second_raw" in err.details,
    );
    assert.equal(provider.requests.length, 2);
  });
});

describe("AgentLoop", () => {
  const add = tool({
    name: "add",
    description: "Add two integers.",
    parameters: {
      type: "object",
      properties: { a: { type: "integer" }, b: { type: "integer" } },
      required: ["a", "b"],
    },
    fn: (args) => (args["a"] as number) + (args["b"] as number),
    mock: () => 7,
  });

  const call: ToolCall = { id: "1", name: "add", arguments: { a: 3, b: 4 } };

  it("dispatches a tool call and stops when the model answers in text", async () => {
    const provider = scripted([{ text: "", toolCalls: [call] }, { text: "The answer is 7." }]);
    const result = await new AgentLoop(provider, [add]).run("what is 3 + 4?");
    assert.equal(result.stoppedReason, "done");
    assert.equal(result.finalText, "The answer is 7.");
    assert.equal(result.steps.length, 2);
    assert.equal(result.steps[0]?.toolResults[0]?.[1], "7");
  });

  it("dispatches to the mock in offline mode, never to fn", async () => {
    const boom = tool({
      name: "add",
      parameters: { type: "object", properties: {} },
      fn: () => {
        throw new Error("the real tool must not run offline");
      },
      mock: () => 7,
    });
    const provider = scripted([{ text: "", toolCalls: [call] }, { text: "done" }]);
    const result = await new AgentLoop(provider, [boom], { offline: true }).run("go");
    assert.equal(result.steps[0]?.toolResults[0]?.[1], "7");
    assert.equal(result.stoppedReason, "done");
  });

  it("stops on the step budget without throwing", async () => {
    // A provider that always asks for another tool call.
    const provider = scripted([{ text: "", toolCalls: [call] }]);
    const result = await new AgentLoop(provider, [add], { maxSteps: 3 }).run("loop forever");
    assert.equal(result.stoppedReason, "budget");
    assert.equal(result.steps.length, 3);
  });

  it("cancels cooperatively via an AbortSignal", async () => {
    const controller = new AbortController();
    controller.abort();
    const provider = scripted([{ text: "never reached" }]);
    const result = await new AgentLoop(provider, [add]).run("hi", { signal: controller.signal });
    assert.equal(result.stoppedReason, "cancelled");
    assert.equal(provider.requests.length, 0);
  });

  it("feeds an unknown tool name back to the model instead of crashing", async () => {
    const provider = scripted([
      { text: "", toolCalls: [{ id: "1", name: "nope", arguments: {} }] },
      { text: "recovered" },
    ]);
    const result = await new AgentLoop(provider, [add]).run("go");
    assert.equal(result.steps[0]?.toolResults[0]?.[1], "error: unknown tool 'nope'");
    assert.equal(result.stoppedReason, "done");
  });

  it("feeds a thrown tool error back rather than aborting the run", async () => {
    const broken = tool({
      name: "add",
      parameters: { type: "object", properties: {} },
      fn: () => {
        throw new Error("upstream down");
      },
      mock: () => 0,
    });
    const provider = scripted([{ text: "", toolCalls: [call] }, { text: "handled" }]);
    const result = await new AgentLoop(provider, [broken]).run("go");
    assert.equal(result.steps[0]?.toolResults[0]?.[1], "error: upstream down");
  });
});
