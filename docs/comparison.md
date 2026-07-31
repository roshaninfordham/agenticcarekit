# Comparison — honest positioning

Honest comparisons get cited. Marketing pages get ignored. This document is written so
that someone who ends up **not** using agenticcarekit still finds it useful, and so
that an agent researching options can quote it without having to hedge.

Short version: agenticcarekit is narrow on purpose. It is a scaffolder plus a thin
runtime for **local, open-weight, healthcare-shaped** applications where the privacy
boundary is the hard part. If that is not your problem, one of the alternatives below
is a better tool and you should use it.

---

## At a glance

| | agenticcarekit | LangChain / LangGraph | LlamaIndex | Raw Ollama |
|---|---|---|---|---|
| Primary job | scaffold a private, local health-AI app | compose LLM applications and agent graphs | ingest, index, and query your data | run a local model |
| Integrations | 4 providers (1 tested) | hundreds | hundreds of loaders and vector stores | itself |
| Privacy enforcement | typed `Sensitive`, runtime egress refusal | none built in | none built in | none — but nothing leaves the box either |
| Domain content | FHIR-lite models, PHI redactor, synthetic patients | none | none | none |
| RAG depth | one TF-IDF local index, ~150 lines | integrations, not depth | **deep** — the point of the project | none |
| Agent orchestration | one loop: step budget, cancellation, mocks | **deep** — graphs, state, checkpointing, human-in-loop | agent workflows over data | none |
| Multi-language | Python + TypeScript, one shared 69-case conformance corpus | Python + JS, hand-maintained in parallel | Python + TS | any HTTP client |
| Scaffolding | machine detection, model recommendation with reasons, generated repo | none | `create-llama` | none |
| Offline story | mandatory tool mocks, `demo --offline` runs with networking disabled | depends on what you wired | depends | inherently local |
| Ejectable | explicit ladder down to the raw `httpx` client | you can, but the surface is large | you can | nothing to eject |
| Maturity | **0.1.0, days old** | years, huge community | years, huge community | mature, widely deployed |

---

## vs LangChain / LangGraph

**What LangChain does better — and it is a long list.**

- **Integration breadth.** Hundreds of model providers, vector stores, document
  loaders, and tools. agenticcarekit ships four providers and *one* of them is
  actually tested. If your model or store is not Ollama, you will write the adapter
  yourself here.
- **Orchestration depth.** LangGraph gives you real graph execution: branching,
  cycles, persistent state, checkpointing, human-in-the-loop interrupts, time travel.
  agenticcarekit's `AgentLoop` is a while-loop with a step budget, a cancel event, and
  offline mock dispatch. That is all it is, and it will never be more.
- **Observability and evaluation as a product.** LangSmith is a hosted tracing and
  eval platform with a UI, dataset management, and team features. agenticcarekit emits
  JSONL and prints a table.
- **Ecosystem and hiring.** Tutorials, StackOverflow answers, consultants, and people
  who already know it. That is a real engineering asset.
- **Streaming, batching, retries, and async plumbing** are mature and general.

**What agenticcarekit does that LangChain does not.**

- **A privacy boundary that is a type.** `Sensitive[T]` cannot reach a public-cloud
  provider un-redacted; enforcement is at runtime, on one path, with an error naming
  the field and the call site. LangChain has callbacks you could build something like
  this on top of. Nothing stops a chain from putting PHI in a prompt today.
- **No inversion of control.** LangChain owns the execution model — you write
  Runnables and it calls them. Here you call us; the raw provider client is one
  attribute away.
- **Gemma 4 quirks encoded as defaults.** Thought-block stripping across turns,
  `<|think|>` injection, modality ordering, vision-token presets. In a general
  framework those live in whichever integration happens to implement them.
- **Machine detection and model recommendation**, with reason strings asserted in
  tests. LangChain has no opinion on what your laptop can run.
- **A conformance corpus.** Both language implementations answer the same 69 cases,
  which is why the TypeScript kernel cannot drift from the Python one. Parallel ports
  maintained by hand always drift.

**Use LangChain instead when:** you need breadth of integrations, non-trivial
orchestration, or a hosted eval platform; when you are not in a regulated domain; or
when your team already knows it. Those are good reasons and not a consolation prize.

---

## vs LlamaIndex

**What LlamaIndex does better.**

- **Retrieval, properly.** Ingestion pipelines, node parsers, dozens of index types,
  hybrid and reranked retrieval, query transformations, evaluation of retrieval
  quality, and integrations with every serious vector store. agenticcarekit's
  `LocalIndex` is stdlib-only TF-IDF cosine similarity with fixed 800-character chunks
  and 200-character overlap. It exists so a demo can cite a document offline with zero
  dependencies. It is not competitive retrieval and does not try to be.
- **Document understanding.** PDFs, tables, complex layouts, multimodal parsing.
- **Scale.** LlamaIndex is built for corpora that do not fit in memory. `LocalIndex`
  recomputes TF-IDF over every chunk on every query.

**What agenticcarekit does that LlamaIndex does not.**

- The privacy boundary, the healthcare domain pack, machine detection, and project
  scaffolding — none of which are LlamaIndex's problem.
- Zero dependencies for retrieval, which matters exactly once: when you want
  `demo --offline` to work on a machine with nothing installed.

**Use LlamaIndex instead when:** retrieval quality is the product. If your hard problem
is "search 40,000 clinical documents well", use LlamaIndex — and wrap the sensitive
values you feed it in `Sensitive` if you want the boundary. The two compose fine.

---

## vs raw Ollama (or llama.cpp, or LM Studio)

This is the honest baseline, and the one to take most seriously. `pip install ollama`
and a `POST /api/chat` is four minutes of work.

**What raw Ollama does better.**

- **Nothing between you and the model.** No layer to learn, debug, or upgrade.
- **Zero abstraction risk.** Nothing can break in a dependency you did not choose.
- **Full API surface**, immediately, including anything added yesterday.
- **It is what agenticcarekit runs on.** We are a client, not a competitor.

**What you write yourself if you go raw.**

Every item here is a bug someone has shipped:

1. Sampling defaults — `temperature=1.0`, `top_p=0.95`, `top_k=64`. Wrong defaults
   degrade output quietly.
2. Thinking-token handling: `<|think|>` at the start of the system prompt, and
   **stripping prior-turn thought blocks from history**. Miss the second one and you
   get a silent multi-turn correctness bug.
3. Modality ordering — images and audio must precede text.
4. Vision token budgets (70 / 140 / 280 / 560 / 1120) and when each is appropriate.
5. Capability negotiation — knowing that audio input exists only on E2B and E4B
   *before* you ship a voice app that fails at runtime.
6. A privacy boundary, if any data is sensitive.
7. A trace format, before anything emits into it. Retrofitting one is the most
   expensive mistake available in this space.
8. Mocks for every tool, or your demo needs the network.
9. Model selection for the machine you are actually on.

That list is the entire product thesis. If you would enjoy writing it, or your app only
needs one text call, raw Ollama is genuinely the better choice.

**Use raw Ollama instead when:** you are prototyping, you need an API surface we do not
expose, or your data is not sensitive and your app is one call deep.

---

## vs an internally-built stack

Most teams in this space build their own. If yours has already encoded the Gemma 4
quirks and has a reviewed privacy boundary, you do not need this — take
[`spec/errors.json`](../spec/errors.json) and the conformance corpus if they are
useful and ignore the rest.

Where agenticcarekit tends to help is the second and third application, when the
question becomes "does the boundary work the same way in all of them" and the honest
answer is that nobody knows.

---

## When NOT to use agenticcarekit

Directly, because you deserve to find this out here rather than three weeks in.

- **You are not on open-weight models.** If you are calling a frontier hosted model,
  most of this project's value — local recommendation, offline demos, device egress —
  evaporates. The `Sensitive` boundary still applies, but it is one module and you
  could vendor the idea.
- **Retrieval quality is your product.** Use LlamaIndex.
- **You need complex orchestration.** Branching, persistent state, human-in-the-loop
  checkpoints, resumable long-running workflows. Use LangGraph. `AgentLoop` will not
  grow into that.
- **You need breadth of integrations.** Four providers, one tested. That is the honest
  count.
- **You are doing diagnosis or treatment recommendation.** Out of scope, deliberately
  and permanently. Every generated file says so.
- **You need a compliance artifact.** This is not HIPAA compliance, not Safe Harbor
  certification, not certified against anything. See [privacy.md](privacy.md).
- **You need production stability today.** 0.1.0. Six known issues are listed in the
  [README](../README.md). Two of the three blueprints do not run their demo yet.
- **You want an ORM-grade FHIR implementation.** The models are FHIR-*lite*: flattened
  fields, string references, no extensions. Use a real FHIR library if you exchange
  data with real systems.
- **You are on Windows without WSL.** Detection covers Windows profiles, but the
  developer testing happened on macOS and Linux.

## When it is a good fit

- Local-first healthcare prototypes where "prove nothing left the machine" is a real
  requirement someone will ask about.
- Teams starting their second or third open-model health app who want one boundary
  instead of three.
- Anyone who wants Gemma 4's quirks handled correctly without reading the model card
  twice.
- Agents scaffolding projects — `--json` on every command, a stable envelope, error
  codes with fixes attached, and deterministic generation.
- Teaching. The plan screen explains *why* E4B, which is mentoring that does not scale
  any other way.

---

## Related

- [architecture.md](architecture.md) · [privacy.md](privacy.md) · [errors.md](errors.md)
- [recipes/](recipes/) — task → exact command
- [../README.md](../README.md) — support matrix and known issues
