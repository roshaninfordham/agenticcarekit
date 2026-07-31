# The conformance suite

A corpus of plain-JSON fixtures plus a ~200-line harness. Any implementation
claiming to be agenticcarekit passes this corpus, in CI, on every commit
(invariant 11). Nothing here is Python-specific except one convenience
harness and one adapter — both are replaceable in an afternoon.

```
spec/conformance/
├── README.md          # this file — the protocol and the encodings
├── runner.py          # the harness (stdlib only, no project dependency)
├── adapters/
│   └── python.py      # adapter for the canonical Python implementation
└── cases/
    ├── message-build.json
    ├── capability-negotiation.json
    ├── policy.json
    ├── trace-shape.json
    └── config.json
```

Run it:

```bash
python spec/conformance/runner.py -- python spec/conformance/adapters/python.py
python spec/conformance/runner.py --filter policy -v -- ./my-adapter
python spec/conformance/runner.py --json -- node adapters/ts.mjs
```

Options: `--filter <area>` · `--case <id>` · `--cases <dir>` · `--json`
(machine summary: totals, failing ids, per-case diffs) · `--one-shot` (one
adapter process per case) · `-v` (list skips too).

Exit codes: **0** every case passed · **1** at least one failed · **2** the
harness or the adapter broke.

---

## The adapter protocol

An implementation proves conformance by shipping an **adapter executable**.
The adapter is glue: it decodes a case, calls the real implementation, and
encodes the answer. An adapter that re-implements the logic it is meant to
verify proves nothing.

### Transport

Default: **JSON lines**. The harness writes one compact JSON object per line
to the adapter's stdin and reads one JSON object per line from its stdout,
**in the same order**, then closes stdin. Anything on stderr is diagnostic
and only shown when the adapter misbehaves.

With `--one-shot` the harness instead spawns one process per case, writes
that single case, and expects a single line back. Adapters that read stdin to
EOF and answer per line satisfy both modes for free.

### Case in

```json
{"id": "mb-001-defaults-text-only", "area": "message-build", "input": { ... }}
```

The adapter never sees `expected`. It cannot pass by echoing.

### Result out

Exactly one of:

```json
{"id": "...", "output": { ... }}                    // it ran, here is the result
{"id": "...", "error": {"code": "E301", ...}}       // the implementation raised
{"id": "...", "unsupported": "why not"}             // cannot run this case
```

`id` is optional but strongly recommended — the harness rejects a result
whose `id` names a different case, which catches ordering bugs immediately.

### Capability probe

The harness first runs `<adapter> --describe` and expects one JSON object:

```json
{"name": "python", "language": "python", "areas": ["config", "trace-shape"]}
```

Cases from areas the adapter does not list are counted as **skipped**, not
failed — a port under construction reports honest partial conformance
instead of a red wall. An adapter that does not implement `--describe` is
assumed to implement everything, so its gaps fail loudly. Skipped is never
the same as passed: only `passed == total` is conformance.

### Comparison

Both sides are normalized with sorted keys and compared with deep equality.
JSON has one number type, so `1` and `1.0` compare equal; booleans never
compare equal to numbers.

Error expectations are a **subset** match. `{"error": {"code": "E203"}}`
accepts any raised error carrying that code, whatever else the implementation
attaches (`message`, `why`, `fix`, `details`). Codes are the contract; prose
is not. Every code a fixture expects must exist in `spec/errors.json` —
`tests/test_conformance_registry.py` enforces that.

---

## Areas and their encodings

Five areas, one file each. Case ids carry an area prefix (`mb-`, `cn-`,
`pol-`, `tr-`, `cfg-`) so a failure is readable out of context.

### `message-build`

The big one, and the one that most repays care: it pins the exact Ollama
`/api/chat` payload for every Gemma 4 quirk. Ground truth is
`docs/CONTRACTS.md` → "Canonical message build", rules 1–6.

**Input** — a JSON encoding of `GenerateRequest`:

```json
{
  "model": "gemma4:e4b",
  "request": {
    "messages": [
      {
        "role": "system|user|assistant|tool",
        "parts": [ <part>, ... ],
        "thinking": "prior thought block, or absent",
        "tool_calls": [{"id": "call_1", "name": "f", "arguments": {}}],
        "tool_call_id": "f"
      }
    ],
    "think": false,
    "tools": [{"name": "f", "description": "...", "parameters": { ...json schema... }}],
    "temperature": null, "top_p": null, "top_k": null,
    "max_tokens": null, "stop": []
  }
}
```

Every `request` field is optional; an absent field means the contract's
default (`think=false`, empty `tools`/`stop`, `null` sampling = "apply the
model's known-good defaults"). An absent field is *not* the same as an
explicit `0` — `temperature: 0.0` is a deliberate override, and
`mb-003` exists to catch implementations that confuse the two.

Parts:

| Encoding | Meaning |
|---|---|
| `{"type": "text", "text": "..."}` | a text part |
| `{"type": "image", "data_b64": "SU1H", "detail": "default"}` | image, data already base64 |
| `{"type": "image", "data_utf8": "IMG"}` | image, data is these literal bytes (UTF-8) |
| `{"type": "audio", "data_b64": "QVVE", "format": "wav"}` | audio, data already base64 |
| `{"type": "audio", "data_utf8": "AUD"}` | audio, data is these literal bytes |

`detail` is one of `minimal` `caption` `default` `detail` `ocr`
(70/140/280/560/1120 vision tokens) and defaults to `default`.

`data_b64` and `data_utf8` are the two halves of rule 6: base64 passes
through untouched, raw bytes get encoded. Fixture payloads are tiny literal
strings (`b"IMG"` → `SU1H`, `b"AUD"` → `QVVE`, `b"WAV"` → `V0FW`,
`b"XRAY"` → `WFJBWQ==`, `b"IMG2"` → `SU1HMg==`) so a diff stays readable.
Rule 6's third branch — a `str` naming a file on disk — is not covered here
because a corpus of JSON fixtures cannot carry a filesystem; implementations
own that with a local unit test.

Tools are declared in their language-neutral core (`name`, `description`,
`parameters`) precisely because the wrapping into
`{"type": "function", "function": {...}}` is what `as_function_schema()`
owns and what the expected payload asserts.

**Expected** — the complete `/api/chat` payload. Note the shapes that are
easy to get subtly wrong:

- `content` is always present and is the `"\n\n"`-join of the text parts, so
  a message with no text carries `"content": ""`.
- `images` / `audio` appear only when non-empty, in declaration order.
- `options.vision_tokens` appears only when the request contains an image.
- `tools` is absent entirely when the request has none.
- `thinking` never appears. Anywhere. For any turn.

### `capability-negotiation`

Two flavours in one file.

*Gap listing* — `{"capabilities": {...}, "requirements": {...}}` →
`{"missing": ["audio input", ...]}`. `capabilities` is a
`capabilities.schema.json` document. `requirements` takes the same field
names (`modalities_in`, `modalities_out`, `tool_calling`, `streaming`,
`context_tokens`, `thinking`); an absent field means "not required".

The strings are contract, not decoration — they are what a user reads in an
E2xx error. So is their order: input modalities (sorted by name), then output
modalities (sorted), then tool calling, streaming, context window, thinking.

*Pre-network check* — `{"model": ..., "capabilities": {...}, "request": {...}}`
where `request` uses the `message-build` encoding →
`{"ok": true}` or `{"error": {"code": "E20x"}}`. This is the check that must
fire *before a byte is sent*: E203 missing input modality, E204 missing
output modality, E202 no tool calling, E201 context too small.

### `policy`

```json
{
  "policy":   {"egress": "device|trusted-network|public-cloud", "redactor": null},
  "value":    {"label": "intake_note", "text": "...", "sensitive": true},
  "provider": {"name": "cerebras", "egress": "public-cloud"}
}
```

→ `{"text": "what the provider is allowed to see"}` or
`{"error": {"code": "E301" | "E303"}}`.

`sensitive` defaults to `true`. A sensitive value goes through the engine's
`unwrap(value, provider)`; a non-sensitive one through `check_provider(provider)`,
because the E303 ceiling applies to *all* traffic and so cannot be expressed
through an unwrap at all.

**Fixture redactors.** Pack redactors (`healthcare.phi`) are neither
language-neutral nor stable enough to assert against. The spec therefore
defines two of its own, and adapters implement them locally:

| name | behaviour |
|---|---|
| `passthrough` | declared, runs, replaces nothing. Returns the text unchanged and zero `Redaction`s. |
| `mask-digits` | every ASCII digit becomes `#`. One `Redaction` per maximal digit run, `category: "DIGITS"`, `replacement` a same-length run of `#`. |

Two readings of the enforcement matrix are resolved here, and both are
normative for implementations:

1. **A declared redactor satisfies the boundary even when it changes
   nothing** (`pol-005`). The condition is "a redactor was declared and
   ran", not "the text changed" — otherwise a clean note would be refused
   while a dirty one sailed through.
2. **Redaction applies at `public-cloud` and not below** (`pol-006`).
   The matrix's "raw or redacted per policy" cell resolves to raw for
   `device` and `trusted-network`; redacting where the boundary does not
   require it silently degrades on-device quality for no privacy gain.
   (The Python engine exposes this as `redact_at_or_above`, defaulting to
   `public-cloud`.)

Ordering is also asserted: the E303 provider ceiling is checked *before* the
redactor runs (`pol-008`). A declared redactor cannot buy egress the project
never allowed.

### `trace-shape`

`{"event": {...}}` → `{"valid": true|false}`, validated against
`spec/schemas/trace-event.schema.json`. Implementations may use any JSON
Schema validator; the Python adapter additionally requires that a
schema-valid event survives a `TraceEvent` round trip, which is what catches
the schema and the contract drifting apart.

`{"events": [...]}` → `{"bytes_egressed": N}`: the sum of `bytes_out` over
events whose `egress` is **not** `device`. This is the arithmetic behind the
"0 bytes egressed" panel, and it is the one number a user will point at when
deciding whether to trust the toolkit — so it is fixtures, not a comment.

### `config`

`{"toml": "<the literal file text>"}` → the normalized parsed form, or
`{"error": {"code": "E401" | "E402" | "E403"}}`.

Normalized form:

```json
{
  "blueprint": "voice-care",
  "pack": "healthcare",
  "model_primary":  {"provider": "ollama",   "model": "gemma4:e4b-mlx"},
  "model_fallback": {"provider": "cerebras", "model": "gemma-4-31b"},
  "egress": "device",
  "redactor": "healthcare.phi",
  "capabilities": ["voice", "extract"],
  "raw_keys": ["capabilities", "model", "policy", "project"]
}
```

`model_fallback` and `redactor` are `null` when absent; `egress` defaults to
`device`. `raw_keys` is the sorted list of top-level keys in the preserved
raw document — it is how the corpus asserts that unknown user sections
survive parsing, because `ack sync` must never destroy hand edits.

Adding `"mode": "fixpoint"` to the input asks for determinism (invariant 4)
instead: parse → serialize → parse → serialize, expecting
`{"fixpoint": true, "normalized": {...}}` where `fixpoint` means the two
serializations were byte-identical and `normalized` describes the config
*after* the round trip. Round-tripping fills in declared defaults, which is
why `cfg-013`'s expected `raw_keys` are wider than its input's.

---

## Adding a case

1. Pick the area file. Append a case with `id`, `area`, `note`, `input`,
   `expected`. The `id` prefix must match the area; `area` must equal the
   file's `suite`.
2. Derive `expected` **by hand from `docs/CONTRACTS.md`**, never by running
   an implementation and pasting the output. A fixture generated from the
   code under test asserts only that the code has not changed — which is not
   what conformance means.
3. The `note` says which rule the case pins and what breaks without it. It
   is printed on failure, so write it for whoever is debugging at 2am.
4. If the case expects an error, its code must already exist in
   `spec/errors.json` — add the entry first, then the fixture.
5. Run the suite. A new case that passes everywhere on the first try is
   worth a second look: it may be asserting something no implementation
   could get wrong.

Behaviour `docs/CONTRACTS.md` does not specify does not belong here. The fix
is to amend the contract first (code + schema + doc, one commit), then add
the fixture — never to let the corpus quietly become the spec.

## Writing an adapter for a new language

Roughly an afternoon:

1. Handle `--describe`; list only the areas you actually serve.
2. Read stdin line by line. For each line, decode `input` per the encodings
   above, call your implementation, print one JSON line.
3. Catch your error type and emit `{"error": {"code": ...}}`; let nothing
   else escape — an uncaught crash costs you the whole run, since the
   harness expects one result per case.
4. Keep stdout clean. Log to stderr.

Re-implementing the harness itself is also fine and occasionally preferable
(a TypeScript CI job that avoids a Python dependency, say). It is one file:
load the case JSON, feed the adapter, compare with sorted-key deep equality,
subset-match errors, print a summary. The corpus is the spec; `runner.py` is
just one way to read it.
