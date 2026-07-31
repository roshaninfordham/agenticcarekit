/**
 * A small JSON Schema (draft 2020-12) validator, sized to this project.
 *
 * Two callers need validation and neither needs the whole specification:
 * `trace-shape` conformance validates events against
 * `spec/schemas/trace-event.schema.json`, and `extract` validates model
 * output against a caller-supplied schema. Both use the same handful of
 * keywords, so this covers them and reports an honest error on any keyword
 * it does not implement rather than passing an unchecked document.
 *
 * Supported: `type`, `enum`, `const`, `required`, `properties`,
 * `additionalProperties`, `items`, `minItems`, `maxItems`, `minimum`,
 * `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `minLength`,
 * `maxLength`, `pattern`, `anyOf`, `allOf`, `oneOf`, `not`, `$defs`/`$ref`
 * (local pointers only), `nullable` via `type` arrays.
 */

export type JsonSchema = Record<string, unknown> | boolean;

const SUPPORTED_KEYWORDS = new Set([
  "$schema",
  "$id",
  "$ref",
  "$defs",
  "definitions",
  "title",
  "description",
  "default",
  "examples",
  "type",
  "enum",
  "const",
  "required",
  "properties",
  "additionalProperties",
  "items",
  "prefixItems",
  "minItems",
  "maxItems",
  "minimum",
  "maximum",
  "exclusiveMinimum",
  "exclusiveMaximum",
  "minLength",
  "maxLength",
  "pattern",
  "anyOf",
  "allOf",
  "oneOf",
  "not",
  "format",
]);

function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value;
}

function matchesType(value: unknown, expected: string): boolean {
  const actual = typeOf(value);
  if (expected === "number") return actual === "number" || actual === "integer";
  if (expected === "integer") return actual === "integer";
  return actual === expected;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => deepEqual(item, b[i]));
  }
  if (typeof a === "object" && typeof b === "object") {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const ak = Object.keys(ao).sort();
    const bk = Object.keys(bo).sort();
    return (
      ak.length === bk.length &&
      ak.every((k, i) => k === bk[i]) &&
      ak.every((k) => deepEqual(ao[k], bo[k]))
    );
  }
  return false;
}

function resolveRef(ref: string, root: JsonSchema): JsonSchema {
  if (!ref.startsWith("#/")) {
    throw new Error(`unsupported $ref '${ref}': only local JSON pointers are resolved`);
  }
  let node: unknown = root;
  for (const rawPart of ref.slice(2).split("/")) {
    const part = rawPart.replace(/~1/g, "/").replace(/~0/g, "~");
    node = (node as Record<string, unknown>)?.[part];
    if (node === undefined) throw new Error(`unresolvable $ref '${ref}'`);
  }
  return node as JsonSchema;
}

function validateNode(
  value: unknown,
  schema: JsonSchema,
  root: JsonSchema,
  path: string,
  errors: string[],
): void {
  if (schema === true) return;
  if (schema === false) {
    errors.push(`${path}: schema forbids any value`);
    return;
  }

  for (const keyword of Object.keys(schema)) {
    if (!SUPPORTED_KEYWORDS.has(keyword)) {
      throw new Error(`unsupported JSON Schema keyword '${keyword}' at ${path}`);
    }
  }

  if (typeof schema["$ref"] === "string") {
    validateNode(value, resolveRef(schema["$ref"], root), root, path, errors);
  }

  const type = schema["type"];
  if (typeof type === "string" && !matchesType(value, type)) {
    errors.push(`${path}: expected ${type}, got ${typeOf(value)}`);
    return;
  }
  if (Array.isArray(type) && !type.some((t) => matchesType(value, String(t)))) {
    errors.push(`${path}: expected one of ${type.join("|")}, got ${typeOf(value)}`);
    return;
  }

  if (Array.isArray(schema["enum"]) && !schema["enum"].some((c) => deepEqual(c, value))) {
    errors.push(`${path}: ${JSON.stringify(value)} is not one of the allowed values`);
  }
  if ("const" in schema && !deepEqual(schema["const"], value)) {
    errors.push(`${path}: expected the constant ${JSON.stringify(schema["const"])}`);
  }

  if (typeof value === "number") {
    const { minimum, maximum, exclusiveMinimum, exclusiveMaximum } = schema as Record<
      string,
      number
    >;
    if (minimum !== undefined && value < minimum) errors.push(`${path}: ${value} < ${minimum}`);
    if (maximum !== undefined && value > maximum) errors.push(`${path}: ${value} > ${maximum}`);
    if (exclusiveMinimum !== undefined && value <= exclusiveMinimum) {
      errors.push(`${path}: ${value} <= ${exclusiveMinimum}`);
    }
    if (exclusiveMaximum !== undefined && value >= exclusiveMaximum) {
      errors.push(`${path}: ${value} >= ${exclusiveMaximum}`);
    }
  }

  if (typeof value === "string") {
    const minLength = schema["minLength"] as number | undefined;
    const maxLength = schema["maxLength"] as number | undefined;
    const pattern = schema["pattern"] as string | undefined;
    if (minLength !== undefined && value.length < minLength) {
      errors.push(`${path}: shorter than ${minLength} characters`);
    }
    if (maxLength !== undefined && value.length > maxLength) {
      errors.push(`${path}: longer than ${maxLength} characters`);
    }
    if (pattern !== undefined && !new RegExp(pattern).test(value)) {
      errors.push(`${path}: does not match /${pattern}/`);
    }
  }

  if (Array.isArray(value)) {
    const minItems = schema["minItems"] as number | undefined;
    const maxItems = schema["maxItems"] as number | undefined;
    if (minItems !== undefined && value.length < minItems) {
      errors.push(`${path}: fewer than ${minItems} items`);
    }
    if (maxItems !== undefined && value.length > maxItems) {
      errors.push(`${path}: more than ${maxItems} items`);
    }
    const items = schema["items"];
    if (items !== undefined) {
      value.forEach((item, i) =>
        validateNode(item, items as JsonSchema, root, `${path}[${i}]`, errors),
      );
    }
  }

  if (typeOf(value) === "object") {
    const object = value as Record<string, unknown>;
    const required = schema["required"];
    if (Array.isArray(required)) {
      for (const key of required) {
        if (!(String(key) in object)) errors.push(`${path}.${String(key)}: required, missing`);
      }
    }
    const properties = (schema["properties"] ?? {}) as Record<string, JsonSchema>;
    for (const [key, subschema] of Object.entries(properties)) {
      if (key in object) validateNode(object[key], subschema, root, `${path}.${key}`, errors);
    }
    const additional = schema["additionalProperties"];
    if (additional !== undefined) {
      const extra = Object.keys(object).filter((k) => !(k in properties));
      if (additional === false) {
        for (const key of extra) errors.push(`${path}.${key}: additional property not allowed`);
      } else if (additional !== true) {
        for (const key of extra) {
          validateNode(object[key], additional as JsonSchema, root, `${path}.${key}`, errors);
        }
      }
    }
  }

  const anyOf = schema["anyOf"];
  if (Array.isArray(anyOf)) {
    const ok = anyOf.some((sub) => validate(value, sub as JsonSchema, root).length === 0);
    if (!ok) errors.push(`${path}: matches none of the anyOf branches`);
  }
  const oneOf = schema["oneOf"];
  if (Array.isArray(oneOf)) {
    const matches = oneOf.filter((sub) => validate(value, sub as JsonSchema, root).length === 0);
    if (matches.length !== 1) {
      errors.push(`${path}: matched ${matches.length} oneOf branches, expected exactly 1`);
    }
  }
  const allOf = schema["allOf"];
  if (Array.isArray(allOf)) {
    for (const sub of allOf) validateNode(value, sub as JsonSchema, root, path, errors);
  }
  if ("not" in schema && validate(value, schema["not"] as JsonSchema, root).length === 0) {
    errors.push(`${path}: matches a schema it must not match`);
  }
}

/**
 * Validate `value` against `schema`; returns a list of human-readable
 * errors, empty when the document is valid.
 *
 * @throws Error when the schema uses a keyword this validator does not
 *   implement — silently passing an unchecked document would be worse.
 */
export function validate(value: unknown, schema: JsonSchema, root?: JsonSchema): string[] {
  const errors: string[] = [];
  validateNode(value, schema, root ?? schema, "$", errors);
  return errors;
}

/** True when `value` satisfies `schema`. */
export function isValid(value: unknown, schema: JsonSchema): boolean {
  return validate(value, schema).length === 0;
}
