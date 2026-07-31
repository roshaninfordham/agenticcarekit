# `agenticcarekit` — TypeScript port (Tier 1)

The kernel and a deliberately small slice of the capabilities, built against
the five frozen contracts in [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md).
Not a rewrite of the CLI — scaffolding, blueprints, packs and `ack serve`
stay Python (`docs/brief.md` §6, W-L).

Conformance is the whole point of this package:

```
69/69 passed · 0 failed · 0 skipped
```

against the shared corpus in [`spec/conformance/`](../../spec/conformance/),
via the adapter at `spec/conformance/adapters/typescript.mjs`. The corpus is
shared, never vendored — a port that copies the fixtures has forked the spec.

## Support matrix

Read this before depending on anything here. Claiming broader support than
has been tested is an explicit anti-goal (`docs/brief.md` §13).

| Area | Status | Verified by |
|---|---|---|
| Contract 1 — `Capabilities`, `Message`, `GenerateRequest`/`Response`, `Chunk` | **ported** | conformance (`capability-negotiation`, `message-build`) |
| Contract 2 — `Sensitive`, `Redactor`, `PolicyContext`, `Policy` engine | **ported** | conformance (`policy`) + unit tests |
| Contract 3 — `ToolSpec` / `tool()` with mandatory mock | **ported** | conformance (`message-build` tool schemas) + unit tests |
| Contract 4 — `TraceEvent`, canonical JSON, `bytesEgressed` | **ported** | conformance (`trace-shape`) + unit tests |
| Contract 5 — `AckConfig`, `ModelRef`, TOML reader | **ported** | conformance (`config`) + unit tests |
| Errors — `AckError`, `CapabilityMismatch`, `PolicyViolation`, registry loader | **ported** | conformance (error codes) + unit tests |
| Gemma 4 message builder (`buildOllamaChat`, all six rules) | **ported** | conformance (`message-build`, 20 cases) |
| `GEMMA4_MODELS` table + `ensureSupported` pre-network check | **ported** | conformance (`capability-negotiation`) |
| `extract` — schema validation, exactly one repair retry | **ported, unit-tested only** | `tests/capabilities.test.ts` |
| `agents` — `AgentLoop`, step budget, offline mocks, cancellation | **ported, unit-tested only** | `tests/capabilities.test.ts` |
| `voice` — mic, barge-in, Twilio adapter | **NOT PORTED** | — |
| `rag` — index, retrieval | **NOT PORTED** | — |
| Providers (Ollama / Cerebras HTTP clients) | **NOT PORTED** | — |
| CLI, scaffolder, blueprints, packs, evals, `ack serve` | **NOT PORTED** (Python owns these) | — |

"Ported, unit-tested only" means exactly that: the conformance corpus does
not cover those areas, so their correctness rests on the tests in this
package and nothing more.

### Known differences from the Python implementation

Honest, and neither is reachable by the corpus:

1. **Tool schemas are declared, not derived.** Python reads type hints;
   TypeScript types are erased at runtime, so `tool()` takes an explicit
   `parameters` JSON Schema. Inventing a schema from erased types would be
   guessing, which is worse than asking.
2. **`TraceEvent.toJson()` cannot reproduce Python's float formatting.**
   JavaScript has one number type, so an integral float serializes as `1`
   where Python writes `1.0`. Key ordering, separators and ASCII escaping
   are byte-identical; the conformance harness compares parsed JSON, where
   the two are the same number.
3. **`Sensitive` uses a real private field (`#value`).** Harder to reach
   than Python's name-mangled slot, but the boundary still defends against
   accident, not malice: `dangerouslyReveal()` remains the one greppable
   raw accessor. See the threat-model notes in `src/kernel/policy.ts`.

## Install and build

```bash
cd packages/ts
npm install          # devDependencies only — zero runtime dependencies
npm run build        # tsc + copy prompt .md files into dist/
npm test             # node:test unit tests (45)
npm run conformance  # the shared corpus, through the Python harness
```

From the repo root, the corpus can also be driven directly:

```bash
python3 spec/conformance/runner.py node spec/conformance/adapters/typescript.mjs
python3 spec/conformance/runner.py --filter policy -v node spec/conformance/adapters/typescript.mjs
```

`pytest tests/test_conformance_ts.py` is the CI gate; it skips cleanly when
Node or the built package is unavailable.

### Dependencies

Runtime: **none.** Dev: `typescript` and `@types/node`. The TOML reader
(`src/kernel/toml.ts`) and the JSON Schema validator
(`src/kernel/jsonschema.ts`) are small, deliberate subsets that *throw* on
grammar they do not implement rather than guessing — a silent misparse of
the privacy boundary is the outcome worth engineering against.

`errorRegistry()` reads `spec/errors.json` by walking up from the module,
so it resolves in a repo checkout. Publishing this package standalone would
need `spec/` shipped alongside it; the registry is deliberately not vendored
into `src/`, because a copied registry is a forked spec.

## Usage

```ts
import {
  AckConfig, AgentLoop, Capabilities, GenerateRequest, Message,
  Policy, Sensitive, buildOllamaChat, extract, tool,
} from "agenticcarekit";

// The canonical Ollama payload — every Gemma 4 quirk applied exactly once.
const payload = buildOllamaChat(
  new GenerateRequest({ messages: [Message.text("user", "Summarise the intake note.")], think: true }),
  "gemma4:e4b",
);

// The privacy boundary. One enforcement path, and it is this one.
const policy = new Policy({ egress: "device" });
const note = new Sensitive("Patient J. Rivera, MRN 12345", "intake_note");
const visible = note.unwrapFor(ollamaProvider, policy);  // raw on device, E301 to public cloud
```

## Layout

```
src/
├── contracts/     the five contracts, mirroring agenticcarekit.kernel.contracts
│   ├── provider.ts    Capabilities, Message/parts, GenerateRequest/Response, Chunk
│   ├── policy.ts      Sensitive, Redactor, Redaction, PolicyContext
│   ├── tools.ts       ToolSpec, Tool, tool() — mandatory mock, E502/E503
│   ├── trace.ts       TraceEvent, canonicalJson, bytesEgressed
│   └── errors.ts      AckError, CapabilityMismatch, PolicyViolation, registry
├── kernel/        the conformance-verified implementation
│   ├── builder.ts     buildOllamaChat — the single quirk-application point
│   ├── policy.ts      Policy — the egress enforcement engine (E301/E302/E303)
│   ├── models.ts      GEMMA4_MODELS, ensureSupported (E201–E204)
│   ├── config.ts      AckConfig, ModelRef (E401/E402/E403/E404)
│   ├── toml.ts        the ack.toml reader
│   └── jsonschema.ts  the validator both trace-shape and extract use
└── capabilities/  unit-tested ports
    ├── extract.ts     one repair retry, then E504
    └── agents.ts      AgentLoop: step budget, offline mocks, AbortSignal
```

Contract drift is fixed in `docs/CONTRACTS.md` first — code, schema and doc
in one commit — never patched around here.
