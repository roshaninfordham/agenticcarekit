# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
**strictly**:

- **MAJOR** — a breaking change to any of the five frozen contracts, the `ack.toml`
  schema, the `--json` envelope, an error code's meaning, or a documented CLI flag.
- **MINOR** — new commands, capabilities, packs, blueprints, providers, error codes, or
  conformance areas, added backwards-compatibly.
- **PATCH** — bug fixes and documentation that change no interface.

Two extra rules, because agents consume this file:

- The **conformance corpus**, the **error registry** (`spec/errors.json`), and the
  **`--json` envelope** are versioned independently of the package. Their versions are
  stated in each release below.
- **Removing or renarrowing an error code is a MAJOR change.** Adding one is MINOR. A
  code's `fix` string may be improved in a PATCH; its `code` and `title` may not.

Machine-readable equivalents: `ack --version`, `ack explain <code> --json`,
`ack manifest --json`, and `spec/errors.json`'s own `version` field.

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-07-31

First release. Everything below is new, so this entry is `Added` only.

### Added

#### Spec (Tier 0)

- `spec/schemas/` — JSON Schema (draft 2020-12) for all five contracts:
  `capabilities`, `provider-spec`, `tool-manifest`, `trace-event`, `ack-toml`, plus
  `errors`.
- `spec/errors.json` — the shared error registry, **version 1.0.0, 34 codes**, each with
  `title`, `what`, `why`, and a literal `fix` command. Read by the CLI, the Python
  implementation, the TypeScript port, and the MCP surface.
- `spec/conformance/` — a **69-case** language-neutral corpus across five areas
  (`message-build` 20, `capability-negotiation` 13, `config` 13, `policy` 12,
  `trace-shape` 11), a stdlib-only runner with a JSON-lines adapter protocol,
  `--describe` for partial support, and `--one-shot` transport. Skipped is never counted
  as passed.
- Expected values derived by hand from `docs/CONTRACTS.md` rather than generated from
  the implementation.

#### Kernel

- `kernel/contracts/` — the five frozen contracts and the error types:
  `Capabilities`/`Provider`/`Message`/`GenerateRequest`, `Sensitive`/`PolicyContext`/
  `Redactor`, `@tool`/`ToolSpec`, `TraceEvent`, `AckConfig`/`ModelRef`, `AckError`/
  `CapabilityMismatch`/`PolicyViolation`, `explain`, `error_registry`.
- `kernel/providers/` — `OllamaProvider`, `CerebrasProvider`,
  `OpenAICompatibleProvider`, `MockProvider`, `FallbackChain`, and `provider_for`.
- `kernel/providers/builder.py` — `build_ollama_chat`, the single point where every
  Gemma 4 quirk is applied: sampling defaults (1.0 / 0.95 / 64), `<|think|>` injection,
  history thought-block stripping, modality ordering, vision-token presets, media
  encoding.
- `kernel/providers/models.py` — the Gemma 4 catalogue, `audio_capable_tags()`, and
  `ensure_supported()`: the pre-network capability check that raises E201–E204.
- `kernel/policy/` — `Policy`, the one enforcement path, implementing the egress matrix
  literally, with E301/E302/E303 carrying field label, call site, and provider.
  `THREATMODEL.md` documents eight closed bypasses and three asserted gaps.
- `kernel/trace/` — `Tracer` with spans, `JsonlSink`, `ConsoleSink`, `read_jsonl`,
  `bytes_egressed`, `assert_zero_egress`.

#### Capabilities

- `voice` — `VoiceLoop` with partial transcripts and barge-in, `MockASR`/`MockTTS`,
  a Twilio media-stream adapter and a mic adapter behind the same interface. TTS is a
  separate provider by construction, because Gemma 4 has no speech output.
- `agents` — `AgentLoop` with a step budget, cancellation, offline mock dispatch, and
  tool errors fed back to the model rather than raised.
- `extract` — schema-validated structured extraction with **exactly one** repair retry,
  then E504.
- `rag` — `LocalIndex`, dependency-free TF-IDF with deterministic chunking and
  byte-identical save/load.

#### Packs

- `healthcare` — FHIR-lite Pydantic models (`Patient`, `Encounter`, `Observation`,
  `DocumentReference`, `MedicationStatement`), `PHIRedactor` covering the 18 HIPAA Safe
  Harbor identifier categories at **measured precision 0.9688 / recall 0.9394**, a
  seeded deterministic synthetic generator, `score_phi_redactor`, and two eval sets
  (33 labelled PHI sentences, 10 intake-extraction goldens).
- `_template` — a near-empty second pack, so the pack interface is an interface.

#### Blueprints

- `voice-care` — voice intake and clinical scribe.
- `care-copilot` — prior-authorization drafting, referral routing, scheduling.
  Draft-only: there is no submission path.
- `on-device` — fully offline intake summariser with a trace-driven
  "0 bytes egressed" panel.
- All three are decision-support only, stated in every generated `.py` file and README.

#### CLI

- Thirteen commands: `init`, `doctor`, `explain`, `new`, `manifest`, `sync`, `add`,
  `swap`, `eject`, `check`, `eval`, `demo`, `serve`.
- `--json` on every one, under a stable envelope (`envelope_version = 1`).
- Thirteen concurrent machine probes with independent timeouts and graceful
  degradation; provider keys probed for presence only.
- A declarative recommendation table — seven hard filters, nine soft scores — each
  carrying a human reason string, with 27 asserted (machine, blueprint) fixtures that
  check the winner *and* a verbatim reason.
- Deterministic generation: `init` twice produces a byte-identical tree.
- Resumable model pulls; `NO_COLOR`/`FORCE_COLOR` respected; degrades below 80 columns.
- Agent surface written into every generated project: `AGENTS.md`, `CLAUDE.md` symlink,
  `.cursor/rules/`, `.github/copilot-instructions.md`.

#### Sidecar

- `ack serve` — the kernel over local HTTP with twelve `/v1` routes (`health`, `doctor`,
  `manifest`, `models`, `errors/{code}`, `trace`, `trace/stream`, `init`, `generate`,
  `check`, `eval`, `capabilities/add`), an OpenAPI document at `/openapi.json`, a
  loopback-only default bind (`127.0.0.1:4422`; a remote bind needs `--allow-remote`),
  and a `0600` token file at `<project>/.ack/serve.token`.
- `ack serve --mcp` — MCP over stdio exposing seven tools: `init_project`,
  `add_capability`, `doctor`, `run_eval`, `get_manifest`, `search_models`,
  `explain_error`. `ACK_SERVE_ROOT` names the project root for clients that cannot pass
  `--path`.
- The policy boundary, redaction, and trace live in this one process, so a thin client
  in any language cannot route around PHI enforcement.

#### Evals

- Golden-set harness with exact match plus an optional LLM judge whose rubric is loaded
  from a markdown file, a static scored table, and a sorted-key `--json` report.

#### TypeScript port

- `packages/ts` — kernel plus `extract` and `agents`, **zero runtime dependencies**,
  passing the same shared corpus at 69/69 with no vendored fixtures. `voice`, `rag`, and
  provider HTTP clients are not ported; the package README states the matrix.

#### Docs and agent surface

- `README.md` with mermaid architecture and dataflow diagrams, an honest support matrix,
  and a known-issues list.
- `docs/architecture.md`, `docs/privacy.md`, `docs/comparison.md`, `docs/errors.md`,
  and ten verified recipes under `docs/recipes/`.
- `AGENTS.md` (with `CLAUDE.md` symlinked), `llms.txt`, `llms-full.txt`,
  `registry.toml`, `CONTRIBUTING.md`.

### Known issues

Enumerated in the [README](README.md#known-issues-010): the error registry path breaks
under a wheel install; the `voice-care` and `care-copilot` demos raise `TypeError` from
template/signature drift; `ack eval --init` is referenced by E601 but unimplemented; one
test fails; `uv run pytest` writes fixture files into the repo root; only `prompts` is
ejectable.

### Not in this release

`ack verify-plugin`, Tier 2 thin clients generated from the sidecar's OpenAPI spec, and
every Tier 3 installer beyond `uvx` / `uv tool install`. See the README roadmap.

[Unreleased]: https://github.com/roshaninfordham/agenticcarekit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/roshaninfordham/agenticcarekit/releases/tag/v0.1.0
