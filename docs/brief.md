# agenticcarekit — build brief

**For:** an orchestrating coding agent that will spawn specialised subagents and work in parallel.
**Read this whole document before spawning anything.**

---

## 0. Mission

Build `agenticcarekit`: an open-source toolkit that generates production-shaped AI applications for healthcare, running on open-weight models, private by default.

**One-liner (use verbatim in the README):**

> agenticcarekit — the open-model stack for health AI. Runs on your laptop. Ships with the privacy boundary built in.

**What it is:** a scaffolder, a thin runtime, and a local sidecar. `ack init` interrogates the machine, recommends a model, and generates a repo the user fully owns.

**What it is NOT:** a framework. Nothing inverts control. Nothing hides the provider. Every abstraction is ejectable.

**Product thesis:** the value is not the code, it is the *encoded judgment* — provider quirks, privacy boundaries, evaluation, demo resilience. Anyone can `pip install ollama` in four minutes. Evaluate every decision against: *does this encode hard-won judgment, or is it plumbing anyone could write?* Plumbing goes in templates. Judgment goes in the runtime where it cannot be accidentally deleted.

---

## 0b. Naming

Package name `agenticcarekit`. Typed command **`ack`**. Deliberately decoupled — short commands are better tools, and a package rename should never break muscle memory, scripts, or CI.

- Ship console-script entry points for both `ack` (primary, documented) and `agenticcarekit` (alias, so `uvx agenticcarekit` works).
- Always the single fused lowercase token in docs, headings, and metadata.
- README carries: *not affiliated with, endorsed by, or derived from Apple Inc.*
- Keep internal imports behind one top-level module so a rename is a one-line change.

---

## 1. Non-negotiable invariants

Not preferences. A change violating one of these is wrong even if tests pass.

1. **Sensitivity is a type, not a convention.** `Sensitive[T]` cannot reach a `public-cloud` provider without a declared redactor. Enforced at runtime. A comment saying "don't send PHI here" is not a boundary.
2. **Providers declare capabilities; the runtime negotiates.** Never silently degrade. Blueprint needs audio, model lacks it → fail at startup with the list of models that have it.
3. **Everything is ejectable.** Drop the blueprint, keep the packs. Drop the packs, keep the capabilities. Drop the capabilities, call the kernel directly.
4. **Determinism.** Identical inputs produce a byte-identical tree. Agents navigate by convention and break on surprises.
5. **Offline must work.** Every tool ships a mock. `ack demo --offline` runs with networking disabled.
6. **No telemetry, ever.** Stated in the CLI header and README.
7. **Never fake progress.** No artificial delays. If detection takes 180 ms, print at 180 ms.
8. **Domain is a pack, not the architecture.** `packs/_template/` ships from day one to prove the seam.
9. **Append-only terminal output.** One small live region at the bottom. Full-screen TUIs destroy scrollback.
10. **Non-TTY is first class.** Every command supports `--json`.
11. **One canonical implementation per tier; the spec is the source of truth.** Ports conform to a published conformance suite or they don't ship. Never hand-maintain parallel logic.

---

## 2. Ground truth — do not hallucinate these

Current as of July 2026, verified against Ollama's registry and Google's model card.

### Gemma 4 (Apache 2.0, released April 2026)

| Ollama tag | Size | Context | Modalities in |
|---|---|---|---|
| `gemma4:e2b` | 7.2 GB | 128K | text, image, **audio** |
| `gemma4:e4b` | 9.6 GB | 128K | text, image, **audio** |
| `gemma4:12b` | 7.6 GB | 256K | text, image |
| `gemma4:26b` | 18 GB | 256K | text, image |
| `gemma4:31b` | 20 GB | 256K | text, image |

- `-mlx` variants exist for Apple Silicon (`gemma4:e4b-mlx`) — prefer on arm64 Darwin.
- `gemma4:cloud` and `gemma4:31b-cloud` are hosted, no download.
- "E" means *effective* parameters. E2B ≈ 2.3B effective, E4B ≈ 4.5B effective.
- 26B is MoE with ~3.8B active. 31B is dense.
- **Output is text only on every variant.** No native speech output.

### Behavioural quirks — encode as defaults

The highest-value content in the library. Each is a bug users will otherwise ship.

1. **Sampling:** `temperature=1.0`, `top_p=0.95`, `top_k=64`.
2. **Thinking:** enabled by a `<|think|>` token at the start of the system prompt. Expose as `chat(..., think=True)`.
3. **Multi-turn hygiene:** prior-turn thought blocks **must** be stripped from history. Silent correctness bug. The message builder does this automatically.
4. **Modality order:** image and audio must precede text in the prompt.
5. **Vision token budget:** 70, 140, 280, 560, 1120. Expose as presets (`image_detail="ocr"` → 1120, `"caption"` → 140).
6. **Native `system` role** supported (unlike Gemma 3). Use it.
7. **Native function calling** supported. Use it rather than prompt-hacking JSON.

### Testing scope — be honest

Gemma 4 via Ollama is the **only** verified path. README carries a support matrix distinguishing *verified* from *declared, untested*. Never imply broader testing than exists.

---

## 3. Distribution architecture

"Available everywhere" must not mean "reimplemented everywhere." Five hand-maintained ports diverge within a month. The structure that scales:

### Tier 0 — the spec (source of truth)

`spec/` contains language-neutral definitions, versioned independently of any implementation:

- `ack.toml` schema (JSON Schema)
- `TraceEvent` schema
- `Capabilities` / `ProviderSpec` schema
- Tool manifest schema
- Error code registry
- **Conformance suite**: a corpus of fixtures plus expected outputs. Any implementation claiming support must pass it. This is what makes multi-language real rather than aspirational.

### Tier 1 — canonical implementations (full, hand-written)

- **Python** — kernel, capabilities, packs, CLI. Primary. The AI and health-data ecosystems live here.
- **TypeScript** — kernel + capabilities. The one second implementation worth the maintenance, because voice UIs and clinical frontends are web. Must pass the conformance suite in CI on every commit.

### Tier 2 — the sidecar (how every other language gets support for free)

`ack serve` exposes the kernel over local HTTP + an MCP endpoint. Any language binds with zero SDK.

This is the important architectural move: the policy boundary, redaction, and trace live in **one process**. A thin Go client cannot accidentally bypass PHI enforcement because it never touches the enforcement path. Ports become convenience, not correctness surface.

Thin clients, generated from the OpenAPI spec, hand-polished only at the edges:
Go · Rust · Java/Kotlin · Swift (matters — on-device iOS health apps) · C#

### Tier 3 — distribution surfaces (installers, not implementations)

Homebrew · Scoop · Docker image · `curl | sh` · Nix flake · devcontainer feature · GitHub Action · uv/uvx · npx

Each is a thin wrapper resolving to one binary or one Python package. Follow the ruff/uv/biome pattern: one core, many front doors.

**Rule for the orchestrator:** do not spawn a subagent for a Tier 2 or Tier 3 target until the conformance suite is green on Python. A port built against a moving target is wasted work.

---

## 4. Repository layout

Each workstream owns its directory **exclusively**. No two subagents write the same file.

```
agenticcarekit/
├─ spec/                      # W-J — schemas + conformance suite
│  ├─ schemas/
│  └─ conformance/
├─ packages/
│  ├─ kernel/
│  │  ├─ contracts/           # Phase 0
│  │  ├─ providers/           # W-A
│  │  ├─ policy/              # W-B
│  │  └─ trace/               # W-C
│  ├─ capabilities/
│  │  ├─ voice/               # W-D
│  │  ├─ agents/  extract/  rag/   # W-E
│  ├─ packs/
│  │  ├─ healthcare/          # W-F
│  │  └─ _template/           # W-F
│  ├─ cli/                    # W-G
│  ├─ serve/                  # W-K — sidecar + MCP server
│  ├─ blueprints/             # W-I
│  └─ ts/                     # W-L — TypeScript port
├─ clients/                   # Tier 2, generated
├─ dist/                      # Tier 3 packaging
├─ docs/  llms.txt  AGENTS.md  README.md   # W-H
└─ registry.toml
```

*(Orchestrator note: the Python implementation nests the import package at `packages/agenticcarekit/` — kernel/, capabilities/, packs/, cli/, serve/, blueprints/ live inside it — so the whole tree ships as the single top-level module `agenticcarekit` required by §0b. Directory ownership above maps 1:1 into that package.)*

---

## 5. Phase 0 — freeze the contracts (BLOCKING, single-threaded)

**Do not spawn parallel subagents until this is complete and committed.** Fan out before freezing and you get incompatible implementations and spend more time reconciling than you saved.

Output: types and docstrings with **zero implementation**, the matching JSON Schemas in `spec/`, and `docs/CONTRACTS.md`.

### Contract 1 — `ProviderSpec` and `Capabilities`

```python
class EgressClass(Enum):
    DEVICE = "device"                    # never leaves the machine
    TRUSTED_NETWORK = "trusted-network"  # self-hosted, user-controlled
    PUBLIC_CLOUD = "public-cloud"        # third-party API

@dataclass(frozen=True)
class Capabilities:
    modalities_in: frozenset[Modality]   # TEXT, IMAGE, AUDIO
    modalities_out: frozenset[Modality]
    tool_calling: bool
    streaming: bool
    context_tokens: int
    thinking: bool
    egress: EgressClass

class Provider(Protocol):
    name: str
    def capabilities(self) -> Capabilities: ...
    def generate(self, req: GenerateRequest) -> GenerateResponse: ...
    def stream(self, req: GenerateRequest) -> Iterator[Chunk]: ...
```

Capability negotiation is the highest-leverage abstraction in the design. It turns "audio is E2B/E4B only" from a doc footnote into a startup error with a fix attached.

### Contract 2 — `Sensitive[T]` and `PolicyContext`

```python
class Sensitive(Generic[T]):
    """Wraps a value that must not reach public-cloud egress un-redacted."""
    def unwrap_for(self, provider: Provider, policy: PolicyContext) -> T:
        """Raises PolicyViolation if egress is disallowed and no redactor
        is declared. Never bypass this."""

class Redactor(Protocol):
    name: str
    def redact(self, text: str) -> tuple[str, list[Redaction]]: ...
```

`PolicyViolation` must name the **exact call site and field**. A vague policy error is one nobody fixes.

### Contract 3 — `@tool`, which emits four artifacts

One decorator produces: a JSON schema for function calling, a permission declaration (`network` / `sensitive` / `writes`), a **mock implementation**, and a doc entry. The mock is not optional — it is what makes offline demo real.

```python
@tool(permissions={"network"}, mock=mock_search)
def web_search(query: str) -> list[SearchResult]: ...
```

### Contract 4 — `TraceEvent`

One schema emitted by every model call, tool call, redaction, and policy decision. This spine powers four surfaces for free: debug console, audit log, eval harness, demo UI.

```python
@dataclass
class TraceEvent:
    ts: float
    run_id: str
    span_id: str
    parent_span_id: str | None
    kind: Literal["model", "tool", "redaction", "policy", "error"]
    egress: EgressClass
    bytes_out: int          # powers the "0 bytes egressed" panel
    payload: dict
```

**Design this before anything emits into it.** Retrofitting a trace format is the most expensive mistake available here.

### Contract 5 — `ack.toml`

Declarative project state. Generator writes it, runtime reads it, agents edit it, `ack sync` reconciles the tree against it. Makes projects re-generatable rather than one-shot dumps.

```toml
[project]
blueprint = "voice-care"
pack = "healthcare"

[model]
primary = "ollama:gemma4:e4b-mlx"
fallback = "cerebras:gemma-4-31b"

[policy]
egress = "device"
redactor = "healthcare.phi"

[capabilities]
enabled = ["voice", "extract"]
```

**Exit criteria:** five contracts typed and documented, matching JSON Schemas in `spec/schemas/`, `docs/CONTRACTS.md` written, committed.

---

## 6. Phase 1 — parallel fan-out

Each subagent gets: this document, `docs/CONTRACTS.md`, its section below, and exclusive write access to its directory. A workstream is complete when its acceptance test passes.

### W-A · Providers
**Owns:** `packages/agenticcarekit/kernel/providers/`
**Build:** `ollama`, `cerebras`, `openai-compatible`, `mock`. Message builder applying every quirk from §2 automatically. Fallback chains (primary → fallback on error/timeout).
**Acceptance:** a request needing audio against a text-only model raises a typed error naming audio-capable tags **before any network call**. Thought-block stripping proven against a recorded multi-turn transcript.

### W-B · Policy and redaction
**Owns:** `packages/agenticcarekit/kernel/policy/`
**Build:** `Sensitive[T]`, `PolicyContext`, `Redactor` protocol, egress enforcement, `PolicyViolation` with call-site attribution. Redactor *implementations* live in packs.
**Acceptance:** construct a `Sensitive[str]`, pass to a `public-cloud` provider with no redactor, assert raise. Second test asserts success through a declared redactor. **Then think of three ways to bypass the boundary and write a test closing each.**

### W-C · Trace and eval
**Owns:** `packages/agenticcarekit/kernel/trace/`, `evals/`
**Build:** event emitter, JSONL sink, terminal console view, eval harness (golden set + judge + scored table).
**Acceptance:** a run produces a trace from which total bytes-egressed is computable and assertable as zero in device-only mode.

### W-D · Voice capability
**Owns:** `packages/agenticcarekit/capabilities/voice/`
**Build:** ASR/TTS provider abstraction, turn loop, barge-in, partial transcripts, Twilio adapter behind the same interface as a local mic. Gemma 4 has no native speech output — TTS is a separate provider; make that explicit in the types.
**Acceptance:** turn loop runs end to end against mock ASR/TTS with no network.

### W-E · Agents, extraction, RAG
**Owns:** `packages/agenticcarekit/capabilities/{agents,extract,rag}/`
**Build:** tool-calling loop with step budget and cancellation; structured extraction with schema validation and exactly one repair retry; minimal local RAG.
**Acceptance:** extraction against a deliberately malformed response repairs once, then fails cleanly rather than looping.

### W-F · Healthcare pack + pack template
**Owns:** `packages/agenticcarekit/packs/healthcare/`, `packages/agenticcarekit/packs/_template/`
**Build:** FHIR-lite Pydantic models (`Patient`, `Encounter`, `Observation`, `DocumentReference`, `MedicationStatement`); PHI redactor covering the 18 HIPAA identifiers; **seeded** synthetic data generator (patients, encounters, vitals, med lists, ~20 realistic intake transcripts); clinical eval sets.
**Also `_template/`** — a near-empty second pack. A pack interface with one implementation is not an interface, it is a folder you will redesign the first time someone tries another domain.
**Acceptance:** same seed → byte-identical synthetic data. Redactor scored against a labelled test set with **published precision and recall**. Do not claim perfection.

### W-G · CLI
**Owns:** `packages/agenticcarekit/cli/`
**Build:** `init`, `add`, `swap`, `eject`, `doctor`, `eval`, `demo`, `sync`, `manifest`, `explain`, `new`. Stack: `typer` + `rich` + `questionary`. See §7 and §8 for the detection engine and error taxonomy — those are the substance of this workstream.
**Acceptance:** `init` twice → identical trees. Killed mid-pull and re-run → resumes. `--json` parses on every command.

### W-H · Agent-native surface and docs
**Owns:** `README.md`, `docs/`, `llms.txt`, `AGENTS.md`
See §9. Treat as product, not documentation cleanup.

### W-I · Blueprints
**Owns:** `packages/agenticcarekit/blueprints/`
**Depends on:** contracts only — parallel-safe.
**Build three:** `voice-care` (intake / scribe), `care-copilot` (prior auth, referrals, scheduling), `on-device` (fully offline, with a "0 bytes egressed" panel driven by the trace).
**Scope constraint, all three:** decision support only — documentation, navigation, accessibility, triage routing, education. **Not diagnosis, not treatment.** Synthetic or public data only. State this in each blueprint README and in generated code comments.
**Acceptance:** each runs `make demo` offline and produces a coherent result.

### W-J · Spec and conformance suite
**Owns:** `spec/`
**Build:** JSON Schemas for all five contracts; a conformance corpus (fixtures + expected outputs) covering message building, quirk application, policy enforcement, trace shape, and config parsing; a runner any implementation can invoke.
**Acceptance:** Python implementation passes 100%. Suite is runnable standalone with no Python dependency assumption.

### W-K · Sidecar and MCP server
**Owns:** `packages/agenticcarekit/serve/`
**Build:** `ack serve` exposing the kernel over local HTTP (OpenAPI-documented) plus an MCP endpoint. Binds loopback by default. Auth via a local token file. Streams traces over SSE.
MCP tools to expose: `init_project`, `add_capability`, `doctor`, `run_eval`, `get_manifest`, `search_models`, `explain_error`.
**Acceptance:** an agent with only MCP access can scaffold a project, diagnose a broken environment, and run an eval without touching a shell.

### W-L · TypeScript port
**Owns:** `packages/ts/`
**Build:** kernel + capabilities against the frozen contracts. Not a rewrite of the CLI — scaffolding stays Python.
**Acceptance:** passes the same conformance suite as Python, in CI, on every commit.

---

## 7. Detection and recommendation engine

The judgment-densest code in the project. Lives in `packages/agenticcarekit/cli/detect/` and `packages/agenticcarekit/cli/recommend/`.

### 7.1 Probes

All run **concurrently**, each with an independent timeout and graceful degradation. A failed probe yields `unknown`, never blocks, never crashes the run. Start the network probe first, render it last.

| Probe | Method | Timeout |
|---|---|---|
| os / arch / kernel | platform module | 50 ms |
| cpu cores + model | platform / sysctl | 100 ms |
| RAM total + available | psutil | 100 ms |
| GPU vendor + VRAM | `nvidia-smi`, `system_profiler`, ROCm | 800 ms |
| Apple Silicon | arch == arm64 and Darwin | 50 ms |
| disk free at model dir | statvfs | 100 ms |
| Ollama installed / daemon / version | binary probe + `GET /api/version` | 600 ms |
| **already-pulled tags** | `GET /api/tags` | 600 ms |
| Docker present | binary probe | 400 ms |
| Python / Node versions | binary probe | 400 ms |
| network throughput | ranged GET against registry CDN, measure, abort at cap | 3 s |
| provider keys present | env var **presence only** | 10 ms |
| existing `ack.toml` in cwd | file read | 20 ms |

**Never log or transmit a key value.** Presence is a boolean.

### 7.2 Recommendation rules

Must be a **declarative rule table**, not buried conditionals. It has to be auditable, testable, and — critically — able to explain itself.

Input: blueprint requirements + machine facts + network measurement.
Output: ranked candidates, each carrying its reasons.

**Hard filters (eliminate):**
- required modalities ⊄ model modalities → eliminate *(audio → E2B/E4B only)*
- required context > model context → eliminate
- tool calling required and unsupported → eliminate
- local model size > available RAM × 0.6 → eliminate
- CUDA path and size > VRAM × 0.9 → eliminate
- disk free < size × 1.15 → eliminate

**Soft scoring (weighted):**
- quality tier: 31b > 26b > 12b > e4b > e2b
- **already pulled → large bonus** (zero download cost dominates almost everything)
- platform fit: `-mlx` on Apple Silicon
- download ETA = size ÷ measured throughput; steep penalty past 5 minutes
- capability headroom above the minimum requirement
- context headroom

**Fallback rule:** if the best local candidate's ETA exceeds threshold, or RAM is insufficient, recommend a **hosted primary with a background local pull**, and say so in the reason string.

### 7.3 Reason generation

Every filter and score contributes a short human string. The top two render as the `←` annotation in the plan screen. This is where the toolkit teaches — a beginner learns *why* E4B, which is mentoring that doesn't scale any other way.

```
  Plan
    blueprint     voice-care
    model         gemma4:e4b-mlx        ← e4b: native audio input, fits 36 GB
    providers     ollama → cerebras     ← local primary, hosted fallback
    pack          healthcare

  ↵ accept   e edit   ? why these

  Re-run this exactly:
    ack init --blueprint voice-care --model gemma4:e4b-mlx \
      --providers ollama,cerebras --pack healthcare --yes
```

`? why` prints the full ranked table with every filter that eliminated a candidate. Printing the non-interactive equivalent is what makes teams adopt the tool — it goes in their README, their CI, and their message to a teammate.

**Ask at most two questions.** Every question is a detection you didn't do.

### 7.4 Testing the engine

Fixture-driven: a corpus of ~30 synthetic machine profiles (16 GB Intel Mac, 96 GB M4 Max, 8 GB Windows laptop, 24 GB RTX 4090, headless Linux server, machine with e4b already pulled, machine with no Ollama, machine on 2 Mbps wifi) each with an asserted recommendation **and asserted reason strings**. Reasons are part of the contract — a correct recommendation with a wrong explanation is a failed test.

---

## 8. Error taxonomy

Errors are where "best CLI" is decided. Nobody remembers a smooth happy path; everyone remembers the error that told them exactly what to do.

Every error: stable searchable code, what happened, why, and the literal fix command. Registry lives in `spec/errors.json` so every language and the MCP server share it.

| Range | Domain |
|---|---|
| E0xx | bootstrap / environment |
| E1xx | model / provider / network |
| E2xx | capability mismatch |
| E3xx | policy and privacy violations |
| E4xx | project config |
| E5xx | generation / templates |
| E6xx | eval |

```
  ✗ E203  gemma4:31b does not support audio input
          The voice-care blueprint needs an audio-capable model.
          Native audio is available on E2B and E4B only.

          ack init --model gemma4:e4b-mlx
```

`ack explain E203` prints the long form. `ack explain E203 --json` returns it structured, and the same content is served over MCP.

Also required: resumable model pulls; Ctrl-C leaves a valid state; re-running continues rather than restarts; `NO_COLOR` and `FORCE_COLOR` respected; degrades below 80 columns; elapsed time on the end screen.

---

## 9. Agent-native requirements

Not a checklist item — a primary distribution channel. Agents adopt what is *legible*.

- **MCP server** (§ W-K). The single biggest lever. Any agent drives the whole toolkit natively, no shell.
- **`llms.txt` and `llms-full.txt`** at repo root and docs root.
- **`--json` on every command**, with a versioned, documented output schema in `spec/`.
- **`ack manifest --json`** — machine-readable description of a generated project.
- **`ack check --json`** — fast, honest verification loop: lint, types, one eval. This is the loop an agent closes against. Keep it under 30 seconds.
- **`ack doctor --json`** — machine-readable environment state, so agents stop hallucinating fixes for problems that don't exist.
- **`AGENTS.md` written as invariants, not description.** Not "this folder has tools" but "*never* call a network provider inside a `Sensitive` scope; add tools only in `app/tools/`; verify with `ack check`." Symlink `CLAUDE.md`. Also emit `.cursor/rules/` and `.github/copilot-instructions.md` in generated projects.
- **Every public function has a docstring with a runnable example**, executed as a doctest in CI. This is what retrieval surfaces.
- **`docs/recipes/`** — task → exact command mappings. Agents pattern-match these.
- **Prompts as `.md` files**, never string literals, so behaviour changes without touching logic.
- **Deterministic generation**, byte-identical.
- **`docs/comparison.md`** — honest positioning against LangChain, LlamaIndex, raw Ollama, including what agenticcarekit is *worse* at. Honest comparisons get cited; marketing pages get ignored.
- **Machine-readable changelog** + strict semver.
- **Registry keywords** matching real search terms: healthcare AI, PHI redaction, de-identification, on-device LLM, local LLM, HIPAA, FHIR, medical scribe, gemma, open weights.

---

## 10. Extensibility

Five extension points, all first-class, all discoverable, all documented with a working example:

| Point | Interface | Scaffolded by |
|---|---|---|
| Provider | `Provider` protocol | `ack new provider` |
| Redactor | `Redactor` protocol | `ack new redactor` |
| Pack | pack manifest + schemas | `ack new pack` |
| Capability | capability manifest | `ack new capability` |
| Blueprint | template dir + `blueprint.toml` | `ack new blueprint` |

- **Discovery** via Python entry points and npm `package.json` keys. No central registration required — install the package, it appears.
- **`registry.toml`** lists first-party plugins; a public community index links third-party ones.
- **Third-party plugins run the conformance suite.** `ack verify-plugin <name>` gives authors a green check before publishing.
- **Every default is overridable in `ack.toml`** — model, provider chain, redactor, prompts, eval set, generation paths.
- **`ack eject <thing>`** inlines any abstraction into user source. This is the promise that depending on the project is reversible, which is precisely what makes people willing to depend on it.

---

## 11. Phase 2 — integration

1. Wire blueprints against real implementations. Fix contract drift by **amending the contract**, not patching around it.
2. **Clean-machine test.** Fresh container, nothing installed, run the documented one-liner, time it. The only benchmark that matters.
3. Record the `init` flow with VHS or asciinema. For a tool whose pitch is "watch how fast this is," the terminal recording *is* the landing page.
4. Publish. Verify `uvx agenticcarekit` and `npx create-agenticcarekit` both work from a machine that has never seen the repo.

---

## 12. Definition of done

- [ ] Clean machine → working demo in **under five minutes**, measured and printed by the tool
- [ ] `ack check` passes: lint, types, one eval, under 30 s
- [ ] `ack demo --offline` works with networking disabled
- [ ] All three blueprints generate and run
- [ ] Policy boundary has a test for each of at least three attempted bypasses
- [ ] Conformance suite green on Python **and** TypeScript
- [ ] MCP server can scaffold, diagnose, and eval with no shell access
- [ ] `_template` pack and one third-party-style plugin prove the extension points
- [ ] Detection engine passes all ~30 machine-profile fixtures, reasons included
- [ ] `llms.txt`, `AGENTS.md`, honest support matrix present
- [ ] Zero telemetry, stated in CLI header and README

---

## 13. Anti-goals

- **Do not** build a framework abstraction that hides the provider. Users must always reach the raw client.
- **Do not** hand-maintain parallel implementations. Spec + conformance suite, or don't ship the port.
- **Do not** invent Gemma 4 facts. Not in §2 → look it up or leave `TODO(verify)`.
- **Do not** add telemetry, analytics, or a phone-home update check.
- **Do not** build a full-screen TUI.
- **Do not** skip mocks. Every tool ships one.
- **Do not** add artificial delay or fake progress.
- **Do not** generalise before one blueprint works end to end on a stranger's laptop. Generalise *from* a working thing, never *toward* one.
- **Do not** claim broader model support than has been tested.

---

## 14. Sequencing note for the orchestrator

Phase 0 is genuinely blocking — resist parallelising it. Then W-A through W-L run concurrently, with two ordering constraints: **W-J (spec) must land before W-L (TypeScript)**, and **no Tier 2/Tier 3 port starts before the conformance suite is green on Python.**

Surplus capacity goes to **W-B (policy)**, **W-G §7 (detection reasons)**, and **W-K (MCP)**. Those three carry disproportionate weight: the policy boundary is what makes the project credible in regulated domains, the reason strings are the mentoring that doesn't scale any other way, and MCP is how agents find and adopt you. Everything else is competent plumbing that any decent agent will get right.
