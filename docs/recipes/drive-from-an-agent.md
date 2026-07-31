# Recipe — drive everything from an agent

**Task:** scaffold, inspect, verify, and diagnose a project with no TTY, no prompts, and
no guessing.

Non-TTY is a first-class surface here, not an afterthought. Every command supports
`--json`, no command ever blocks on input when `--yes` is passed, and failure sets a
non-zero process exit status.

## The envelope

Identical on every command, success or failure:

```json
{
  "envelope_version": 1,
  "ok": true,
  "command": "explain",
  "version": "0.1.0",
  "elapsed_ms": null,
  "data": { },
  "error": null
}
```

`envelope_version` is `1` and the shape is frozen. Parse `ok` first; on failure `error`
carries the code, `what`, `why`, `fix`, and `details`.

*(A published `spec/schemas/cli-envelope.schema.json` is roadmap — the shape above is
stable but not yet schema'd.)*

## The loop

```bash
# 1 — what is actually on this machine (stop hallucinating fixes for problems that don't exist)
ack doctor --json

# 2 — scaffold, deterministically, asking nothing
ack init myapp --blueprint on-device --yes --offline --json

# 3 — describe what was generated
cd myapp && ack manifest --json

# 4 — verify, under 30 seconds, non-zero exit on failure
ack check --json

# 5 — run it with networking disabled
ack demo --offline --json

# 6 — turn any error code into a fix
ack explain E203 --json
```

`ack doctor` and `ack explain` currently need a repo checkout (`uv run ack ...`) — known
issue 1 in the [README](../../README.md).

## `ack doctor --json`

The whole machine, honestly, with problems as fixable codes:

```json
{"command": "doctor", "data": {
  "facts": {
    "os": "Darwin", "arch": "arm64", "cpu_model": "Apple M5", "cpu_cores": 10,
    "ram_total_gb": 17.18, "ram_available_gb": 3.26, "vram_gb": null,
    "gpu_vendor": "apple", "gpu_name": "Apple Silicon GPU (unified memory)",
    "disk_free_gb": 45.84, "model_dir": "/Users/you/.ollama/models",
    "ollama_installed": true, "ollama_daemon": false, "installed_tags": [],
    "python_version": "3.12.13", "node_version": "24.13.1", "docker_installed": true,
    "network_mbps": null, "ack_toml_present": false, "facts_version": 1,
    "provider_keys": {"CEREBRAS_API_KEY": false, "OPENAI_API_KEY": false},
    "probes": [{"name": "ollama", "status": "ok", "duration_ms": 3.77},
               {"name": "network", "status": "skipped", "detail": "offline"}]
  },
  "problems": [{"code": "E011", "title": "Ollama daemon is not running",
                "what": "the ollama binary exists but the daemon did not answer on 127.0.0.1:11434.",
                "fix": "ollama serve   # then re-run your command"}]
}, "envelope_version": 1, "ok": true}
```

Thirteen probes run **concurrently**, each with its own timeout. A failed probe yields
`unknown` — it never blocks and never crashes the run, so `probes[].status` is worth
reading before trusting a `null`.

`provider_keys` values are **booleans**. Key values are never read, never logged, never
serialized. Two tests assert exactly that.

## `ack manifest --json`

```json
{"command": "manifest", "data": {
  "manifest_version": 1,
  "project": {"name": "myapp", "blueprint": "voice-care", "pack": "healthcare"},
  "model": {"primary": "ollama:gemma4:e4b-mlx", "fallback": null},
  "policy": {"egress": "device", "redactor": "healthcare.phi"},
  "capabilities": ["voice", "extract"],
  "files": ["AGENTS.md", "ack.toml", "app/main.py", "..."],
  "tools": [], "tool_notes": []
}, "envelope_version": 1, "ok": true}
```

`tools` is populated by importing the project's tool modules; a module that fails to
import produces a `tool_notes` entry, never an exception. `manifest` describes a
project, it does not run it.

## `ack check --json`

The fast honest loop. Keep it under 30 seconds and it stays usable as an inner loop.

```json
{"command": "check", "data": {"ok": true, "budget_seconds": 30, "within_budget": true,
 "duration_ms": 72.6,
 "steps": [{"name": "lint", "status": "pass", "duration_ms": 11.9},
           {"name": "selftest", "status": "pass", "doctests_attempted": 24}]},
 "envelope_version": 1, "ok": true}
```

## Test hooks

Useful when driving from CI or a sandbox:

| Variable | Effect |
|---|---|
| `ACK_OFFLINE=1` | same as `--offline` everywhere |
| `ACK_MACHINE_FACTS=<path>` | inject a recorded `MachineFacts` JSON instead of probing — reproducible recommendations |
| `ACK_BLUEPRINT_PATH=<dir>` | extra blueprint search path |
| `ACK_THROUGHPUT_URL=<url>` | override the throughput probe endpoint |
| `NO_COLOR` / `FORCE_COLOR` | both respected; `NO_COLOR` wins |

## Agent-surface files in generated projects

`ack init` writes all four, and keeps them in sync on `add` / `swap` / `sync`:

```
AGENTS.md                        invariants for this project
CLAUDE.md -> AGENTS.md           symlink
.cursor/rules/agenticcarekit.mdc
.github/copilot-instructions.md
```

## MCP and HTTP

If you would rather not shell out at all, `ack serve` puts the same kernel behind local
HTTP (twelve `/v1` routes, OpenAPI at `/openapi.json`, loopback-bound, `0600` token
file) and `ack serve --mcp` behind MCP over stdio with seven tools: `init_project`,
`add_capability`, `doctor`, `run_eval`, `get_manifest`, `search_models`,
`explain_error`. Same envelope, same error codes.

See [run-the-sidecar.md](run-the-sidecar.md).

## Related

- [explain-an-error-code.md](explain-an-error-code.md)
- [../../llms.txt](../../llms.txt) · [../../llms-full.txt](../../llms-full.txt)
- [../../AGENTS.md](../../AGENTS.md)
