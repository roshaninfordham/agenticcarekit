/**
 * Structured extraction with schema validation and exactly one repair retry.
 *
 * Flow: prompt the model (prompt loaded from a `.md` file — never a string
 * literal), parse and validate the JSON response against a JSON Schema. On
 * validation failure, exactly one repair request is sent, carrying the
 * validation errors and the malformed output verbatim. If the repair also
 * fails, the function throws `AckError` (code `E504`) summarising both
 * failures. It never loops more than twice, and it never throws for any
 * reason other than a doubly-failed validation.
 *
 * @example
 * const provider = {
 *   name: "stub",
 *   capabilities() { throw new Error("unused"); },
 *   generate: () => ({ text: '{"name": "Ada", "age": 36}' }),
 * };
 * await extract(provider, { type: "object", properties: { name: { type: "string" }, age: { type: "integer" } }, required: ["name", "age"] }, "Ada is 36 years old.");
 * // { name: "Ada", age: 36 }
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { AckError } from "../contracts/errors.js";
import {
  EgressClass,
  GenerateRequest,
  Message,
  Provider,
} from "../contracts/provider.js";
import { TraceEvent } from "../contracts/trace.js";
import { JsonSchema, validate } from "../kernel/jsonschema.js";

const PROMPTS_DIR = join(dirname(fileURLToPath(import.meta.url)), "prompts");

export interface ExtractOptions {
  /** Override the extraction prompt; the repair prompt is unchanged. */
  promptPath?: string;
  emit?: ((event: TraceEvent) => void) | null;
}

class MalformedOutput extends Error {
  readonly raw: string;
  readonly errors: string;

  constructor(raw: string, errors: string) {
    super(errors);
    this.name = "MalformedOutput";
    this.raw = raw;
    this.errors = errors;
  }
}

/** Remove a leading/trailing ``` fence, if present. */
function stripFences(text: string): string {
  const stripped = text.trim();
  if (!stripped.startsWith("```")) return stripped;
  let lines = stripped.split("\n");
  if (lines[0]?.startsWith("```")) lines = lines.slice(1);
  if (lines[lines.length - 1]?.trim().startsWith("```")) lines = lines.slice(0, -1);
  return lines.join("\n").trim();
}

/**
 * Tolerate leading/trailing prose by slicing from the first `{` to the last
 * `}`. Validation downstream stays strict — this only widens what we
 * *attempt* to parse.
 */
function extractJsonSpan(text: string): string {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1 || end < start) return text;
  return text.slice(start, end + 1);
}

function parseAndValidate(text: string, schema: JsonSchema): unknown {
  const candidate = extractJsonSpan(stripFences(text));
  let data: unknown;
  try {
    data = JSON.parse(candidate);
  } catch (exc) {
    throw new MalformedOutput(text, `invalid JSON: ${(exc as Error).message}`);
  }
  const errors = validate(data, schema);
  if (errors.length > 0) throw new MalformedOutput(text, errors.join("; "));
  return data;
}

let spanCounter = 0;
function nextId(prefix: string): string {
  spanCounter += 1;
  return `${prefix}-${spanCounter}`;
}

function emitModelEvent(
  emit: ((event: TraceEvent) => void) | null | undefined,
  runId: string,
  model: string,
): void {
  if (!emit) return;
  emit(
    new TraceEvent({
      ts: Date.now() / 1000,
      runId,
      spanId: nextId("extract-span"),
      parentSpanId: null,
      kind: "model",
      egress: EgressClass.DEVICE,
      bytesOut: 0,
      payload: { model },
    }),
  );
}

function emitErrorEvent(
  emit: ((event: TraceEvent) => void) | null | undefined,
  runId: string,
): void {
  if (!emit) return;
  emit(
    new TraceEvent({
      ts: Date.now() / 1000,
      runId,
      spanId: nextId("extract-span"),
      parentSpanId: null,
      kind: "error",
      egress: EgressClass.DEVICE,
      bytesOut: 0,
      payload: { code: "E504", attempts: 2 },
    }),
  );
}

/**
 * Extract `schema`-shaped data from `text` using `provider`, with exactly
 * one repair retry.
 *
 * @throws AckError E504 — the repair attempt also failed validation.
 *   `details` carries both raw responses and both error strings.
 */
export async function extract(
  provider: Provider,
  schema: JsonSchema,
  text: string,
  options: ExtractOptions = {},
): Promise<unknown> {
  const template = readFileSync(options.promptPath ?? join(PROMPTS_DIR, "extract.md"), "utf-8");
  const schemaJson = JSON.stringify(schema, null, 2);
  const prompt = template.replace("{schema_json}", schemaJson).replace("{text}", text);
  const runId = nextId("extract-run");

  const first = await provider.generate(
    new GenerateRequest({ messages: [Message.text("user", prompt)] }),
  );
  emitModelEvent(options.emit, runId, first.model ?? "");

  try {
    return parseAndValidate(first.text, schema);
  } catch (exc) {
    if (!(exc instanceof MalformedOutput)) throw exc;
    const firstFailure = exc;

    const repairTemplate = readFileSync(join(PROMPTS_DIR, "repair.md"), "utf-8");
    const repairPrompt = repairTemplate
      .replace("{schema_json}", schemaJson)
      .replace("{text}", text)
      .replace("{malformed}", firstFailure.raw)
      .replace("{errors}", firstFailure.errors);

    const second = await provider.generate(
      new GenerateRequest({ messages: [Message.text("user", repairPrompt)] }),
    );
    emitModelEvent(options.emit, runId, second.model ?? "");

    try {
      return parseAndValidate(second.text, schema);
    } catch (secondExc) {
      if (!(secondExc instanceof MalformedOutput)) throw secondExc;
      emitErrorEvent(options.emit, runId);
      throw new AckError("structured extraction failed validation after one repair attempt", {
        code: "E504",
        why: `first attempt: ${firstFailure.errors}\nrepair attempt: ${secondExc.errors}`,
        fix: "inspect the raw model output in `details` and adjust the schema or prompt",
        details: {
          first_raw: firstFailure.raw,
          first_errors: firstFailure.errors,
          second_raw: secondExc.raw,
          second_errors: secondExc.errors,
        },
      });
    }
  }
}
