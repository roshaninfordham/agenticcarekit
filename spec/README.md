# `spec/` — Tier 0, the source of truth

Everything in this directory is **language-neutral** and versioned
independently of any implementation. It is the answer to the question that
kills most multi-language projects: *what stops the ports from diverging?*

Five hand-maintained implementations diverge within a month. A published
spec plus a conformance suite is the structure that scales, because
"supports agenticcarekit" becomes a claim that can be *checked* rather than
asserted (`docs/brief.md` §3, invariant 11).

```
spec/
├── README.md                 # this file
├── errors.json               # the shared error registry
├── schemas/                  # JSON Schema (draft 2020-12) for the five contracts
│   ├── ack-toml.schema.json          Contract 5 — project state
│   ├── capabilities.schema.json      Contract 1 — what a model can do
│   ├── provider-spec.schema.json     Contract 1 — a named provider
│   ├── tool-manifest.schema.json     Contract 3 — `ack manifest --json`
│   ├── trace-event.schema.json       Contract 4 — one event shape
│   └── errors.schema.json            the registry's own schema
└── conformance/              # the fixture corpus + harness (see its README)
```

## What lives here, and what does not

**Here:** shapes and behaviour that every implementation must agree on — the
five contract schemas, the error registry, and a corpus of input/expected
fixtures covering message building, capability negotiation, the privacy
boundary, trace shape, and config parsing.

**Not here:** anything a single language owns. Import surfaces, class
hierarchies, CLI ergonomics, provider clients, the scaffolder. Tier 1
implementations (`packages/agenticcarekit`, `packages/ts`) are free to be
idiomatic as long as the corpus passes.

The prose contracts live in `docs/CONTRACTS.md`. When it and this directory
disagree, that is a bug in one of them and the fix is a single commit
amending code, schema and doc together — never a workaround downstream.

## The error registry

`errors.json` is read by every implementation, by the CLI (`ack explain
E203`), and by the MCP server. Each entry carries a stable code, what
happened, why, and the literal command that fixes it.

The rule, from `docs/CONTRACTS.md`: **a new code is added here first, then
raised in code.** A code raised anywhere in the tree but absent from this
file is a test failure — `tests/test_conformance_registry.py` enforces it by
scanning the package. Codes are permanent once shipped: users search for
them, and agents key off them.

Ranges: `E0xx` environment · `E1xx` model/provider/network · `E2xx`
capability · `E3xx` policy · `E4xx` config · `E5xx` generation · `E6xx` eval.

## Versioning

The spec carries its **own semantic version**, independent of any
implementation's. `errors.json` declares it in `version`; each conformance
suite file declares the version it was written against in `spec_version`.

- **Patch** — clarified wording, a new fixture that pins behaviour already
  required by an existing contract.
- **Minor** — additive: a new error code, a new optional schema field, a new
  case area. Conforming implementations stay conforming.
- **Major** — a changed expectation. Every implementation must be updated in
  the same release train, and the reason belongs in the changelog.

An implementation declares the spec version it conforms to. `agenticcarekit
0.2.0` passing spec `1.0.0` is a meaningful, checkable statement; "supports
agenticcarekit" is not.

## How conformance gates a port

Invariant 11: *one canonical implementation per tier; the spec is the source
of truth. Ports conform to a published conformance suite or they don't
ship.*

Concretely:

1. A port ships an **adapter** (see `conformance/README.md`) — thin glue from
   the corpus to its real implementation, never a re-implementation of the
   rules under test.
2. Its CI runs the corpus on every commit. `passed == total` is the bar;
   skipped areas are honest partial conformance, never a pass.
3. The corpus is shared, not copied. A port that vendors its own fixtures has
   forked the spec, which is the failure mode this directory exists to
   prevent.
4. Tier 2 clients (Go, Rust, Swift, …) do **not** need to conform, because
   they do not implement the kernel — they talk to `ack serve`, where the
   policy boundary, redaction and trace live in one process. That is the
   point of the sidecar: ports become convenience, not correctness surface.

`packages/agenticcarekit` (Python) is the canonical Tier 1 implementation and
passes the corpus today; `packages/ts` (W-L) is next, and this suite is what
it will be judged against.
