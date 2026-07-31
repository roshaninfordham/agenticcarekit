# Contributing

Thanks for looking. This document is short on ceremony and long on the two things that
actually gate a change here: the **conformance suite** and the **contracts**.

Before writing code, read [AGENTS.md](AGENTS.md). It is written as invariants, and it
applies to humans as much as to agents.

---

## Dev setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Node ≥ 20 only if you
touch the TypeScript port.

```bash
git clone https://github.com/roshaninfordham/agenticcarekit
cd agenticcarekit
uv sync --group dev
uv run ack --version          # agenticcarekit 0.1.0
```

`uv sync --group dev` installs `pytest`, `ruff`, `jsonschema`, and the sidecar deps.
Runtime dependencies are deliberately six — `typer`, `rich`, `questionary`, `pydantic`,
`httpx`, `psutil` — plus the `serve` extra (`fastapi`, `uvicorn`, `sse-starlette`,
`mcp`) for `ack serve`.

For the TypeScript port:

```bash
cd packages/ts && npm install && npm run build && npm test
```

---

## Test commands

```bash
# everything
uv run pytest -q

# one area
uv run pytest tests/test_policy_*.py -q
uv run pytest tests/test_providers_*.py -q

# doctests for a package (public functions carry runnable examples; they are executed)
uv run pytest --doctest-modules packages/agenticcarekit/kernel/policy/ -q

# lint and format
uv run ruff check packages tests spec
uv run ruff format --check packages tests spec
```

### Two things that will otherwise waste your afternoon

1. **`uv run pytest` from the repo root writes generated fixture files into the repo
   root** — `README.md`, `AGENTS.md`, `CLAUDE.md`, `ack.toml`, `Makefile`, `app/`,
   `prompts/`, `providers/`, `.cursor/`, `.github/copilot-instructions.md`. Run
   `git status` afterwards and clean up. Fixing that test isolation is a welcome PR.
2. **One test is known-failing**:
   `tests/test_cli_commands.py::test_eval_with_a_golden_set_but_no_provider_chain_names_the_gap`
   asserts the behaviour from before `kernel.providers.factory.provider_for` landed.
   514 pass, 1 fails. Do not chase it as a regression.

Never run a writing `ack` command (`init`, `new`, `eject`, `sync`) with the repo root as
the working directory. Pass an explicit path into a scratch directory instead.

---

## Conformance gates

This is the part that makes multi-language real rather than aspirational. **Both must
report `69/69 passed · 0 failed · 0 skipped`:**

```bash
uv run python spec/conformance/runner.py -- uv run python spec/conformance/adapters/python.py
python3 spec/conformance/runner.py node spec/conformance/adapters/typescript.mjs
```

Useful flags: `--filter <area>`, `-v`, `--json`, `--one-shot`, and `--describe` on an
adapter to see which areas it claims.

Rules:

- **A skip is not a pass.** `tests/test_conformance_ts.py` asserts `failed == 0` *and*
  `skipped == 0` *and* that `--describe` still lists all five areas — a shrinking area
  list cannot hide in a skip count. It skips cleanly when Node or the build is missing,
  so make sure you built the port before believing a green run.
- **The corpus is shared, never vendored.** A port that copies the fixtures has forked
  the spec.
- **Adding a case is a normative act.** Derive the expected value from
  `docs/CONTRACTS.md` by hand, not from the implementation — the corpus is what catches
  the implementation being wrong.

The five areas: `message-build` (20), `capability-negotiation` (13), `config` (13),
`policy` (12), `trace-shape` (11). The adapter protocol is documented in
[`spec/conformance/README.md`](spec/conformance/README.md).

---

## How contracts change

The five contracts in `agenticcarekit.kernel.contracts` are frozen. Frozen does not
mean unchangeable — it means the change happens in one place, in one commit:

> **Code + JSON Schema + `docs/CONTRACTS.md`, together, or not at all.**

Never patch around a contract downstream. Contract drift discovered during integration
is resolved the same way: amend the contract, then fix the callers.

A contract change is a **MAJOR** version bump. Expect it to be discussed before it is
merged.

### Adding an error code

1. Add the entry to `spec/errors.json` **first**: `code`, `title`, `what`, `why`, and a
   literal `fix` command. Pick the range: `E0xx` environment · `E1xx`
   model/provider/network · `E2xx` capability · `E3xx` policy · `E4xx` config · `E5xx`
   generation · `E6xx` eval.
2. Then raise it in code. A code raised but not registered fails
   `tests/test_conformance_registry.py`.
3. Regenerate `docs/errors.md`.

Adding a code is MINOR. Removing or renarrowing one is MAJOR. A `fix` string may be
improved in a PATCH; `code` and `title` may not.

### Renaming things

These names are called by the conformance adapters in both languages. Renaming any of
them breaks conformance and is a MAJOR change:

`build_ollama_chat` · `ensure_supported` · `Capabilities.missing` · `Policy.unwrap` ·
`Policy.check_provider` · `TraceEvent.from_dict` / `to_dict` · `bytes_egressed` ·
`AckConfig.load` / `to_toml`

---

## Where code goes

Each area owns its directory. Do not write outside the one you are changing.

| Change | Directory | Test file prefix |
|---|---|---|
| Provider quirk, transport, fallback | `packages/agenticcarekit/kernel/providers/` | `tests/test_providers_*.py` |
| Egress enforcement, redaction plumbing | `packages/agenticcarekit/kernel/policy/` | `tests/test_policy_*.py` |
| Trace emitter, sinks, analysis | `packages/agenticcarekit/kernel/trace/` | `tests/test_trace_*.py` |
| Voice | `packages/agenticcarekit/capabilities/voice/` | `tests/test_voice_*.py` |
| Agents / extract / rag | `packages/agenticcarekit/capabilities/{agents,extract,rag}/` | `tests/test_agents_*.py`, `test_extract_*.py`, `test_rag_*.py` |
| Domain models, redactors, synthetic data | `packages/agenticcarekit/packs/<pack>/` | `tests/test_packs_*.py` |
| Generated project templates | `packages/agenticcarekit/blueprints/<name>/` | `tests/test_blueprints_*.py` |
| CLI, detection, recommendation | `packages/agenticcarekit/cli/` | `tests/test_cli_*.py`, `test_detect_*.py`, `test_recommend_*.py` |
| Sidecar, MCP | `packages/agenticcarekit/serve/` | `tests/test_serve_*.py` |
| Eval harness | `packages/agenticcarekit/evals/` | `tests/test_eval_*.py` |
| Schemas, error registry, conformance | `spec/` | `tests/test_conformance_*.py` |
| TypeScript port | `packages/ts/` | `packages/ts/tests/`, `tests/test_conformance_ts.py` |
| Docs, `llms.txt`, changelog, registry | `docs/`, repo root | — |

Tests are **flat**: `tests/test_<area>_<topic>.py`. Do not create subdirectories under
`tests/`.

---

## Style and expectations

- **Every public function carries a docstring with a runnable example.** Doctests are
  executed, `ack check` runs the contract doctests as its selftest, and retrieval
  surfaces the examples. A public function with no example is incomplete.
- **Prompts are `.md` files, never string literals.** `ack eject prompts` depends on it.
- **Every `@tool` ships a mock.** The decorator refuses at import otherwise (E502).
- **`--json` on every CLI command**, under the stable envelope.
- **Determinism**: no timestamps, no absolute paths, no `uuid4()`/`datetime.now()` in
  generation or synthetic data; sorted iteration, sorted JSON keys.
- **No telemetry.** Not in any form, ever.
- **Do not edit `pyproject.toml`** to add a dependency. Open an issue explaining why the
  six runtime deps are not enough.
- Ruff config lives in `pyproject.toml`: line length 100, target py311, rules
  `E,F,W,I,UP,B` with `E501` ignored.

### Honesty rules

These are not style preferences; they are what the project is for.

- **Never claim broader model support than has been tested.** The support matrix in the
  README distinguishes *supported* (conformance-verified, recorded transports) from
  *declared, untested*. If you add a provider, it starts in the second column.
- **Publish measured numbers, including the misses.** The healthcare pack's labelled set
  deliberately contains cases the redactor gets wrong, and a test asserts the published
  precision/recall appears verbatim in the README so the claim cannot drift.
- **Label roadmap as roadmap.** Documentation for something unimplemented must say so in
  the same paragraph.
- **Do not invent Gemma 4 facts.** If it is not in `docs/brief.md` §2, look it up or
  leave `TODO(verify)`.

---

## Before you open a PR

```bash
uv run pytest -q
uv run ruff check packages tests spec
uv run ruff format --check packages tests spec
uv run python spec/conformance/runner.py -- uv run python spec/conformance/adapters/python.py
python3 spec/conformance/runner.py node spec/conformance/adapters/typescript.mjs
git status                     # nothing generated into the repo root
```

Then: a `CHANGELOG.md` entry under `## [Unreleased]`, in the right group, written for
someone who was not in the conversation.

Anything that changes a contract, an error code's meaning, the `--json` envelope, or a
published measurement should say so in the PR description explicitly.

---

## License

Apache-2.0. By contributing you agree your contribution is licensed under it.
