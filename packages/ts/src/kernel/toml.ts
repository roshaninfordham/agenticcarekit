/**
 * A small TOML reader, sized to `ack.toml`.
 *
 * `ack.toml` is a deliberately narrow file (Contract 5): a handful of
 * tables, string values, and one array of strings. Pulling a full TOML
 * implementation in for that would trade a real dependency for surface the
 * contract does not use, so this parser covers the documented grammar of
 * the file — tables, dotted keys, basic and literal strings, integers,
 * floats, booleans, arrays and inline tables — and *throws* on anything
 * else rather than guessing. A silent misparse of the privacy boundary is
 * the one outcome worth engineering against.
 *
 * Not supported (and rejected loudly): array-of-tables (`[[x]]`),
 * multi-line strings, dates and times. Nothing in `ack.toml` uses them; if
 * that changes, amend the contract first.
 */

export class TomlParseError extends Error {
  readonly line: number;

  constructor(message: string, line: number) {
    super(`${message} (line ${line})`);
    this.name = "TomlParseError";
    this.line = line;
  }
}

type TomlValue = string | number | boolean | TomlValue[] | { [key: string]: TomlValue };

interface Cursor {
  text: string;
  pos: number;
  line: number;
}

const BARE_KEY = /^[A-Za-z0-9_-]+$/;

function isWhitespace(ch: string): boolean {
  return ch === " " || ch === "\t";
}

function skipWhitespace(cur: Cursor): void {
  while (cur.pos < cur.text.length && isWhitespace(cur.text[cur.pos] as string)) cur.pos += 1;
}

/** Whitespace, newlines and comments — everything between two tokens. */
function skipTrivia(cur: Cursor): void {
  while (cur.pos < cur.text.length) {
    const ch = cur.text[cur.pos] as string;
    if (isWhitespace(ch) || ch === "\r") {
      cur.pos += 1;
    } else if (ch === "\n") {
      cur.pos += 1;
      cur.line += 1;
    } else if (ch === "#") {
      while (cur.pos < cur.text.length && cur.text[cur.pos] !== "\n") cur.pos += 1;
    } else {
      return;
    }
  }
}

function parseBasicString(cur: Cursor): string {
  if (cur.text.startsWith('"""', cur.pos)) {
    throw new TomlParseError("multi-line strings are not supported in ack.toml", cur.line);
  }
  cur.pos += 1; // opening quote
  let out = "";
  while (true) {
    if (cur.pos >= cur.text.length) throw new TomlParseError("unterminated string", cur.line);
    const ch = cur.text[cur.pos] as string;
    if (ch === '"') {
      cur.pos += 1;
      return out;
    }
    if (ch === "\n") throw new TomlParseError("unterminated string", cur.line);
    if (ch === "\\") {
      const next = cur.text[cur.pos + 1];
      cur.pos += 2;
      switch (next) {
        case "n":
          out += "\n";
          break;
        case "t":
          out += "\t";
          break;
        case "r":
          out += "\r";
          break;
        case '"':
          out += '"';
          break;
        case "\\":
          out += "\\";
          break;
        case "b":
          out += "\b";
          break;
        case "f":
          out += "\f";
          break;
        case "u":
        case "U": {
          const width = next === "u" ? 4 : 8;
          const hex = cur.text.slice(cur.pos, cur.pos + width);
          if (!/^[0-9A-Fa-f]+$/.test(hex) || hex.length !== width) {
            throw new TomlParseError("invalid unicode escape", cur.line);
          }
          out += String.fromCodePoint(parseInt(hex, 16));
          cur.pos += width;
          break;
        }
        default:
          throw new TomlParseError(`invalid escape sequence \\${next ?? ""}`, cur.line);
      }
      continue;
    }
    out += ch;
    cur.pos += 1;
  }
}

function parseLiteralString(cur: Cursor): string {
  if (cur.text.startsWith("'''", cur.pos)) {
    throw new TomlParseError("multi-line strings are not supported in ack.toml", cur.line);
  }
  cur.pos += 1;
  const end = cur.text.indexOf("'", cur.pos);
  if (end === -1 || cur.text.slice(cur.pos, end).includes("\n")) {
    throw new TomlParseError("unterminated literal string", cur.line);
  }
  const out = cur.text.slice(cur.pos, end);
  cur.pos = end + 1;
  return out;
}

function parseArray(cur: Cursor): TomlValue[] {
  cur.pos += 1; // "["
  const items: TomlValue[] = [];
  skipTrivia(cur);
  if (cur.text[cur.pos] === "]") {
    cur.pos += 1;
    return items;
  }
  while (true) {
    skipTrivia(cur);
    items.push(parseValue(cur));
    skipTrivia(cur);
    const ch = cur.text[cur.pos];
    if (ch === ",") {
      cur.pos += 1;
      skipTrivia(cur);
      if (cur.text[cur.pos] === "]") {
        cur.pos += 1;
        return items;
      }
      continue;
    }
    if (ch === "]") {
      cur.pos += 1;
      return items;
    }
    throw new TomlParseError("expected ',' or ']' in array", cur.line);
  }
}

function parseInlineTable(cur: Cursor): Record<string, TomlValue> {
  cur.pos += 1; // "{"
  const table: Record<string, TomlValue> = {};
  skipWhitespace(cur);
  if (cur.text[cur.pos] === "}") {
    cur.pos += 1;
    return table;
  }
  while (true) {
    skipWhitespace(cur);
    const key = parseKeyPath(cur);
    skipWhitespace(cur);
    if (cur.text[cur.pos] !== "=") throw new TomlParseError("expected '=' in inline table", cur.line);
    cur.pos += 1;
    skipWhitespace(cur);
    assignPath(table, key, parseValue(cur), cur.line);
    skipWhitespace(cur);
    const ch = cur.text[cur.pos];
    if (ch === ",") {
      cur.pos += 1;
      continue;
    }
    if (ch === "}") {
      cur.pos += 1;
      return table;
    }
    throw new TomlParseError("expected ',' or '}' in inline table", cur.line);
  }
}

const INTEGER = /^[+-]?(?:0|[1-9](?:_?[0-9])*)$/;
const FLOAT = /^[+-]?(?:0|[1-9](?:_?[0-9])*)(?:\.[0-9](?:_?[0-9])*)?(?:[eE][+-]?[0-9]+)?$/;

function parseValue(cur: Cursor): TomlValue {
  const ch = cur.text[cur.pos];
  if (ch === undefined) throw new TomlParseError("expected a value", cur.line);
  if (ch === '"') return parseBasicString(cur);
  if (ch === "'") return parseLiteralString(cur);
  if (ch === "[") return parseArray(cur);
  if (ch === "{") return parseInlineTable(cur);

  let end = cur.pos;
  while (end < cur.text.length && !",]}\n#".includes(cur.text[end] as string)) end += 1;
  const token = cur.text.slice(cur.pos, end).trim();
  cur.pos = end;
  if (token === "true") return true;
  if (token === "false") return false;
  if (INTEGER.test(token)) return Number.parseInt(token.replace(/_/g, ""), 10);
  if (FLOAT.test(token)) return Number.parseFloat(token.replace(/_/g, ""));
  if (token === "inf" || token === "+inf") return Infinity;
  if (token === "-inf") return -Infinity;
  if (token === "nan") return NaN;
  throw new TomlParseError(`unsupported or invalid value '${token}'`, cur.line);
}

/** A dotted key path: `a.b."c"` → `["a", "b", "c"]`. */
function parseKeyPath(cur: Cursor): string[] {
  const parts: string[] = [];
  while (true) {
    skipWhitespace(cur);
    const ch = cur.text[cur.pos];
    if (ch === '"') parts.push(parseBasicString(cur));
    else if (ch === "'") parts.push(parseLiteralString(cur));
    else {
      let end = cur.pos;
      while (end < cur.text.length && BARE_KEY.test(cur.text[end] as string)) end += 1;
      const bare = cur.text.slice(cur.pos, end);
      if (bare.length === 0) throw new TomlParseError("expected a key", cur.line);
      parts.push(bare);
      cur.pos = end;
    }
    skipWhitespace(cur);
    if (cur.text[cur.pos] === ".") {
      cur.pos += 1;
      continue;
    }
    return parts;
  }
}

function assignPath(
  root: Record<string, TomlValue>,
  path: string[],
  value: TomlValue,
  line: number,
): void {
  let node = root;
  for (const key of path.slice(0, -1)) {
    const existing = node[key];
    if (existing === undefined) {
      const created: Record<string, TomlValue> = {};
      node[key] = created;
      node = created;
    } else if (typeof existing === "object" && !Array.isArray(existing)) {
      node = existing as Record<string, TomlValue>;
    } else {
      throw new TomlParseError(`cannot redefine '${key}' as a table`, line);
    }
  }
  const last = path[path.length - 1] as string;
  if (last in node) throw new TomlParseError(`duplicate key '${last}'`, line);
  node[last] = value;
}

/**
 * Parse TOML text into a plain object.
 *
 * @throws TomlParseError on any syntax the file is not allowed to contain.
 *
 * @example
 * parseToml('[project]\nblueprint = "on-device"\n');
 * // { project: { blueprint: "on-device" } }
 */
export function parseToml(text: string): Record<string, TomlValue> {
  const cur: Cursor = { text, pos: 0, line: 1 };
  const root: Record<string, TomlValue> = {};
  let table: Record<string, TomlValue> = root;

  while (true) {
    skipTrivia(cur);
    if (cur.pos >= cur.text.length) break;

    if (cur.text.startsWith("[[", cur.pos)) {
      throw new TomlParseError("array-of-tables is not supported in ack.toml", cur.line);
    }

    if (cur.text[cur.pos] === "[") {
      cur.pos += 1;
      const path = parseKeyPath(cur);
      skipWhitespace(cur);
      if (cur.text[cur.pos] !== "]") throw new TomlParseError("unterminated table header", cur.line);
      cur.pos += 1;
      let node = root;
      for (const key of path) {
        const existing = node[key];
        if (existing === undefined) {
          const created: Record<string, TomlValue> = {};
          node[key] = created;
          node = created;
        } else if (typeof existing === "object" && !Array.isArray(existing)) {
          node = existing as Record<string, TomlValue>;
        } else {
          throw new TomlParseError(`cannot redefine '${key}' as a table`, cur.line);
        }
      }
      table = node;
    } else {
      const path = parseKeyPath(cur);
      skipWhitespace(cur);
      if (cur.text[cur.pos] !== "=") {
        throw new TomlParseError("expected '=' after key", cur.line);
      }
      cur.pos += 1;
      skipWhitespace(cur);
      assignPath(table, path, parseValue(cur), cur.line);
    }

    // Nothing but a comment may follow a table header or a key/value pair.
    skipWhitespace(cur);
    const trailing = cur.text[cur.pos];
    if (trailing !== undefined && trailing !== "\n" && trailing !== "\r" && trailing !== "#") {
      throw new TomlParseError(`unexpected '${trailing}' after value`, cur.line);
    }
  }
  return root;
}
