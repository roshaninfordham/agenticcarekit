# The five frozen contracts

Phase 0 output. Everything in agenticcarekit is built against — and only
against — what is exported from `agenticcarekit.kernel.contracts`. Matching
JSON Schemas live in `spec/schemas/`; the shared error registry in
`spec/errors.json`.

**Rule for every workstream:** if a contract doesn't fit, the fix is to
amend the contract *here* (code + schema + this doc, one commit), never to
patch around it downstream. Contract drift discovered in integration is
resolved the same way.

Import surface:

```python
from agenticcarekit.kernel.contracts import (
    # Contract 1
    Modality, EgressClass, Capabilities, Provider,
    Message, TextPart, ImagePart, AudioPart, ToolCall,
    GenerateRequest, GenerateResponse, Chunk, Usage, VISION_TOKEN_BUDGETS,
    # Contract 2
    Sensitive, PolicyContext, Redactor, Redaction,
    # Contract 3
    tool, Tool, ToolSpec, Permission,
    # Contract 4
    TraceEvent, EventKind,
    # Contract 5
    AckConfig, ModelRef,
    # Errors
    AckError, CapabilityMismatch, PolicyViolation, explain, error_registry,
)
```

---

## Contract 1 — `Capabilities` and `Provider`

*File: `kernel/contracts/provider.py` · Schemas: `capabilities.schema.json`, `provider-spec.schema.json`*

Providers **declare** capabilities; the runtime negotiates. Never infer,
never silently degrade (invariant 2).

- `EgressClass`: `device` | `trusted-network` | `public-cloud`. The privacy
  boundary (Contract 2) is defined over these three classes and nothing else.
- `Capabilities.missing(**requirements)` returns human-readable gap strings
  (`["audio input", "tool calling"]`). These strings feed
  `CapabilityMismatch` errors verbatim — they are part of the contract.
- `Provider` is a `Protocol`: `name`, `capabilities()`, `generate(req)`,
  `stream(req)`. Anything satisfying it plugs in — including third-party
  plugins. Concrete providers must also expose their raw client
  (attribute `client` by convention) — nothing hides the provider.

Messages: `Message(role, parts, thinking, tool_calls, tool_call_id)`.

- `thinking` lives **outside** `parts`. That is what makes quirk 3
  (strip prior-turn thought blocks from history) structural: the message
  builder simply never serializes `thinking` for historical turns.
- `Message.required_modalities()` / `GenerateRequest.required_modalities()`
  drive pre-network capability checks (W-A acceptance).
- Image detail presets: `minimal=70, caption=140, default=280, detail=560,
  ocr=1120` (`VISION_TOKEN_BUDGETS`).

Sampling: `GenerateRequest` fields `temperature/top_p/top_k` default to
`None` = "apply the model's known-good defaults" (Gemma 4: 1.0 / 0.95 / 64).
Providers apply the defaults; user code overrides deliberately.

`think=True` → the provider injects `<|think|>` at the start of the system
prompt (quirk 2). Modality ordering (image/audio before text, quirk 4) is
the message builder's job, not the caller's.

---

## Contract 2 — `Sensitive[T]` and `PolicyContext`

*File: `kernel/contracts/policy.py`*

Sensitivity is a type, not a convention (invariant 1).

- `Sensitive(value, label=...)` is a **sealed box**: masked `repr`/`str`/
  `format`, refuses pickling, captures its construction call site in
  `.origin` (`file.py:123`).
- `Sensitive.unwrap_for(provider, policy)` is the *only* sanctioned path to
  the value on the way to a provider. It delegates to
  `PolicyContext.unwrap(value, provider)` — exactly one enforcement path.
- `Sensitive.dangerously_reveal()` exists for the policy engine
  post-authorization and for code that stays on-device by construction.
  The name is the audit trail; greppable in review.
- `Redactor` protocol: `name`, `redact(text) -> (clean_text, [Redaction])`.
  Implementations live in packs (`healthcare.phi`).
- `PolicyViolation` (E3xx) **must** carry `field_name`, `call_site`
  (from `Sensitive.origin`), and `provider`. A vague policy error is one
  nobody fixes.
- Every policy decision — allowed or denied — emits a `TraceEvent`
  (`kind="policy"` or `"redaction"`).

Enforcement matrix (W-B implements):

| value → provider egress | `device` | `trusted-network` | `public-cloud` |
|---|---|---|---|
| non-sensitive | allow | allow | allow |
| `Sensitive`, no redactor | allow | allow if policy egress ≥ trusted | **raise E301** |
| `Sensitive`, redactor declared | allow (raw) | allow (raw or redacted per policy) | allow **redacted only** |

Additionally: any provider whose egress class is broader than the
project's `[policy] egress` limit is refused outright (E303), sensitive or
not.

---

## Contract 3 — `@tool`

*File: `kernel/contracts/tools.py` · Schema: `tool-manifest.schema.json`*

One decorator, four artifacts: JSON schema (from type hints), permission
declaration (closed set: `network` / `sensitive` / `writes`), **mandatory
mock**, doc entry (the docstring).

- No mock → decoration fails with **E502**. Not at call time — at import.
- Unknown permission → **E503**.
- `Tool.spec` carries the `ToolSpec`; `ToolSpec.as_function_schema()` is
  the provider-facing declaration; `ToolSpec.to_manifest()` the
  `ack manifest` entry.
- Offline mode (`ack demo --offline`) dispatches to `spec.mock` instead of
  `spec.fn`. The swap happens in the agent loop (W-E), never inside Tool.

---

## Contract 4 — `TraceEvent`

*File: `kernel/contracts/trace.py` · Schema: `trace-event.schema.json`*

One schema for every model call, tool call, redaction, policy decision,
and error. Fields exactly: `ts, run_id, span_id, parent_span_id, kind,
egress, bytes_out, payload`.

- Wire format: JSONL, one event per line, `to_json()` = sorted keys, no
  whitespace drift (deterministic — invariant 4).
- `bytes_out` = bytes that left the process toward the provider for this
  event. The "0 bytes egressed" panel is
  `sum(e.bytes_out for e in run if e.egress != DEVICE) == 0`.
- `kind` is a closed set: `model | tool | redaction | policy | error`.
- Emitter, sinks (JSONL file, console view) and the eval harness are W-C;
  they consume this type, never extend it. New needs → amend here first.

Payload conventions (informative, not schema-enforced): `model` events
carry `{"model", "provider", "input_tokens", "output_tokens", "duration_ms"}`;
`redaction` events carry `{"redactor", "categories", "count"}`; `policy`
events carry `{"decision": "allow"|"allow-redacted"|"deny", "reason", "call_site"}`.

---

## Contract 5 — `ack.toml`

*File: `kernel/contracts/config.py` · Schema: `ack-toml.schema.json`*

Declarative project state: `[project] blueprint/pack`, `[model]
primary/fallback` (as `provider:model` refs), `[policy] egress/redactor`,
`[capabilities] enabled`.

- `AckConfig.load(path)` / `from_dict()` raise E40x errors naming the
  exact missing/invalid key.
- `AckConfig.to_toml()` is deterministic (byte-identical for identical
  configs).
- Unknown keys are **preserved** (`raw` holds the full parsed dict) —
  users and agents may extend the file; `ack sync` must never destroy
  their edits.

---

## Errors (cross-cutting)

*File: `kernel/contracts/errors.py` · Registry: `spec/errors.json` · Schema: `errors.schema.json`*

- `AckError(message, code=, why=, fix=, details=)`; `render()` produces the
  canonical CLI shape; `to_dict()` the `--json`/MCP shape.
- Code ranges: E0xx environment · E1xx model/provider/network · E2xx
  capability · E3xx policy · E4xx config · E5xx generation · E6xx eval.
- New error codes are **added to `spec/errors.json` first**, then raised in
  code. A code raised but not registered is a test failure (W-J enforces).

---

## Canonical message build (binds W-A and W-J)

`build_ollama_chat(req: GenerateRequest, model: str) -> dict` in
`kernel/providers/builder.py` is the single quirk-application point. Its
output is the exact Ollama `/api/chat` payload and the conformance
fixtures assert it byte-for-byte (as sorted-key JSON):

```json
{
  "model": "gemma4:e4b",
  "messages": [
    {"role": "system", "content": "<|think|>You are..."},
    {"role": "user", "content": "...", "images": ["<b64>"], "audio": ["<b64>"]}
  ],
  "tools": [ ...ToolSpec.as_function_schema()... ],
  "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
  "stream": false
}
```

Rules, in order:
1. Sampling defaults 1.0/0.95/64 land in `options`; request overrides win.
   `max_tokens` → `options.num_predict`; `stop` → `options.stop`;
   context is NOT sent (the model declares it).
2. `think=True` → prepend `<|think|>` to the system message content
   (creating a system message if none exists). Exactly once, at the start.
3. History hygiene: `Message.thinking` is NEVER serialized for any turn.
   Assistant `tool_calls` serialize to Ollama's `tool_calls` field; `tool`
   role turns carry `tool_call_id` as `tool_name` mapping per Ollama.
4. Modality order: within a message, images/audio serialize to their
   `images`/`audio` arrays (which Ollama places before text); multiple
   text parts join with "\n\n" into `content`.
5. `ImagePart.detail` preset maps to `options.vision_tokens` using
   `VISION_TOKEN_BUDGETS` when any image is present (highest preset wins
   across images).
6. Bytes data → base64; str data that is an existing file path → read and
   base64; other str → assumed already-base64 (passed through).

## Blueprint layout (binds W-G and W-I)

A blueprint is `packages/agenticcarekit/blueprints/<name>/` containing:

- `blueprint.toml` — `[blueprint] name, description, track`;
  `[requires] modalities_in = [...], tool_calling = bool,
  context_tokens = int`; `[defaults] capabilities = [...], pack = "..."`.
- `templates/` — the generated tree. Files ending `.tmpl` are rendered by
  simple `{{var}}` substitution then the suffix is stripped; everything
  else is copied verbatim. Renderer (W-G) substitutes exactly:
  `project_name`, `blueprint`, `pack`, `model_primary`, `model_fallback`,
  `egress`, `redactor`, `capabilities_list`, `ack_version`. Unknown
  `{{...}}` in a `.tmpl` file is an E501 error, not silence.
- `README.md` — states the decision-support-only scope.

Generation is deterministic: identical inputs → byte-identical trees
(no timestamps, no absolute paths, sorted file iteration).

## Conventions binding all workstreams

- Python ≥ 3.11. Imports always via the single top-level module
  (`from agenticcarekit.kernel.contracts import ...`).
- Tests live in `tests/test_<area>_*.py` (flat, exclusively named per
  workstream). Run: `uv run pytest`.
- Do not edit `pyproject.toml`; needed deps go in your report.
- Do not run git commands; the orchestrator commits.
- Prompts are `.md` files, never string literals.
- Public functions carry docstrings with runnable examples.
- No telemetry, no artificial delays, no full-screen TUIs, append-only
  terminal output, `--json` everywhere (invariants 6–10).
