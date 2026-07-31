/**
 * Contract 3 — `tool()`: one declaration, four artifacts.
 *
 *   1. a JSON schema for native function calling,
 *   2. a permission declaration (`network` / `sensitive` / `writes`),
 *   3. a **mock implementation** (not optional — it is what makes
 *      `ack demo --offline` real, invariant 5),
 *   4. a doc entry (the description).
 *
 * Python derives the parameter schema from type hints. TypeScript types are
 * erased at runtime, so the schema is declared explicitly rather than
 * guessed — declaring less costs a loud error, inventing more costs a
 * silent wrong call.
 *
 * @example
 * const add = tool({
 *   name: "add",
 *   description: "Add two integers.",
 *   parameters: { type: "object", properties: { a: { type: "integer" }, b: { type: "integer" } }, required: ["a", "b"] },
 *   fn: ({ a, b }) => a + b,
 *   mock: () => 3,
 * });
 * add.spec.name;        // "add"
 * add.spec.mock({});    // 3
 */

import { AckError } from "./errors.js";

export type Permission = "network" | "sensitive" | "writes";

export const VALID_PERMISSIONS: readonly Permission[] = ["network", "sensitive", "writes"];

export type ToolFn = (args: Record<string, unknown>) => unknown;

export interface ToolSpecInit {
  name: string;
  description?: string;
  jsonSchema?: Record<string, unknown>;
  permissions?: Iterable<string>;
  fn: ToolFn;
  mock: ToolFn;
}

/** The four artifacts a tool declaration emits, in one place. */
export class ToolSpec {
  readonly name: string;
  readonly description: string;
  readonly jsonSchema: Record<string, unknown>;
  readonly permissions: ReadonlySet<string>;
  readonly fn: ToolFn;
  readonly mock: ToolFn;

  constructor(init: ToolSpecInit) {
    this.name = init.name;
    this.description = init.description ?? "";
    this.jsonSchema = init.jsonSchema ?? { type: "object", properties: {} };
    this.permissions = new Set(init.permissions ?? []);
    this.fn = init.fn;
    this.mock = init.mock;
  }

  /** Provider-facing function-calling declaration. */
  asFunctionSchema(): Record<string, unknown> {
    return {
      type: "function",
      function: {
        name: this.name,
        description: this.description,
        parameters: this.jsonSchema,
      },
    };
  }

  /** Entry for the tool manifest (`spec/schemas/tool-manifest.schema.json`). */
  toManifest(): Record<string, unknown> {
    return {
      name: this.name,
      description: this.description,
      permissions: [...this.permissions].sort(),
      parameters: this.jsonSchema,
      has_mock: true,
    };
  }
}

/**
 * Callable wrapper carrying its `ToolSpec`. Calling it calls the real
 * function; offline mode swaps in `spec.mock` — in the agent loop, never
 * inside the tool itself (Contract 3).
 */
export class Tool {
  readonly spec: ToolSpec;

  constructor(spec: ToolSpec) {
    this.spec = spec;
  }

  get name(): string {
    return this.spec.name;
  }

  call(args: Record<string, unknown> = {}): unknown {
    return this.spec.fn(args);
  }

  asFunctionSchema(): Record<string, unknown> {
    return this.spec.asFunctionSchema();
  }

  get description(): string {
    return this.spec.description;
  }
}

export interface ToolOptions {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
  permissions?: Iterable<string>;
  fn: ToolFn;
  /** Mandatory. No mock → E502, at declaration time, not at call time. */
  mock?: ToolFn;
}

/**
 * Declare a tool.
 *
 * @throws AckError E502 — the tool has no mock (mocks are not optional).
 * @throws AckError E503 — an unknown permission was declared.
 */
export function tool(options: ToolOptions): Tool {
  const permissions = new Set(options.permissions ?? []);
  const unknown = [...permissions]
    .filter((p) => !(VALID_PERMISSIONS as readonly string[]).includes(p))
    .sort();
  if (unknown.length > 0) {
    throw new AckError(`unknown tool permission(s): ${unknown.join(", ")}`, {
      code: "E503",
      why: `valid permissions are: ${[...VALID_PERMISSIONS].sort().join(", ")}`,
      fix: 'use tool({ permissions: ["network"], ... })',
    });
  }
  if (options.mock === undefined || options.mock === null) {
    throw new AckError(`tool '${options.name}' declared without a mock`, {
      code: "E502",
      why: "every tool ships a mock — it is what makes `ack demo --offline` real.",
      fix: `tool({ name: "${options.name}", mock: mock${options.name}, ... })  // returns canned data`,
    });
  }
  return new Tool(
    new ToolSpec({
      name: options.name,
      description: options.description ?? "",
      jsonSchema: options.parameters ?? { type: "object", properties: {} },
      permissions,
      fn: options.fn,
      mock: options.mock,
    }),
  );
}
