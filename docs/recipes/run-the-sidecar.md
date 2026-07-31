# Recipe — run the sidecar (HTTP and MCP)

**Task:** expose the kernel to another language, or to an agent, without an SDK.

## Command

```bash
cd myproject
ack serve                       # local HTTP, 127.0.0.1:4422
ack serve --mcp                 # MCP over stdio instead
```

| Option | Default | Meaning |
|---|---|---|
| `--path` | `.` | project directory |
| `--host` | `127.0.0.1` | bind address — **loopback only** unless you pass `--allow-remote` |
| `--port` | `4422` | bind port |
| `--mcp` | off | speak MCP over stdio; stdout becomes the transport, so the banner goes to stderr |
| `--allow-remote` | off | permit a non-loopback bind, deliberately |
| `--json` | off | print the envelope when the server stops; bind details go to stderr at startup |

## Startup

```
agenticcarekit 0.1.0 · ack — No telemetry, ever.
  listening   http://127.0.0.1:4422
  docs        http://127.0.0.1:4422/docs
  openapi     http://127.0.0.1:4422/openapi.json
  token       /path/to/myproject/.ack/serve.token   (mode 0600; send it as: Authorization: Bearer ...)
  root        /path/to/myproject
```

The token is generated into `.ack/serve.token` with mode `0600`. Every request needs
it:

```bash
TOKEN=$(cat .ack/serve.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:4422/v1/doctor
```

## Routes

Twelve, all under `/v1`, all returning the same envelope as the CLI
(`{envelope_version, ok, command, version, elapsed_ms, data, error}`):

| Method | Path |
|---|---|
| `GET` | `/v1/health` |
| `GET` | `/v1/doctor` |
| `GET` | `/v1/manifest` |
| `GET` | `/v1/models` |
| `GET` | `/v1/errors/{code}` |
| `GET` | `/v1/trace` |
| `GET` | `/v1/trace/stream` |
| `POST` | `/v1/init` |
| `POST` | `/v1/generate` |
| `POST` | `/v1/check` |
| `POST` | `/v1/eval` |
| `POST` | `/v1/capabilities/add` |

The OpenAPI document is served at `/openapi.json` and rendered at `/docs`. Generate a
client from it rather than hand-writing one.

## Why this exists

The naive way to support Go, Rust, and Swift is a client library each — after which the
privacy boundary exists five times, differs five times, and one of the five is wrong.

Here, `Policy`, the redactors, and the trace live in **this one process**. A thin client
cannot bypass PHI enforcement because it never holds a provider. Every additional
language becomes a convenience feature rather than a correctness risk. That is the
whole argument for the sidecar, and it is why `/v1/generate` exists but a
"give me the raw provider" route does not.

Loopback is the default and a remote bind takes an explicit flag, because a sidecar
that enforces PHI policy is exactly the process you do not want on `0.0.0.0` by
accident.

## MCP

```bash
ack serve --mcp
```

Seven tools, a deliberately closed list — an agent's mental model of this toolkit *is*
this list:

| Tool | Does |
|---|---|
| `init_project` | scaffold a project |
| `add_capability` | enable a capability in `ack.toml` |
| `doctor` | machine state, with problems as fixable codes |
| `run_eval` | score against the golden set |
| `get_manifest` | describe a generated project |
| `search_models` | find a model that fits a requirement (audio is E2B/E4B only) |
| `explain_error` | any code, before guessing |

Point an MCP client at the command. When your client cannot pass `--path`, set the
project root through the environment instead:

```json
{
  "mcpServers": {
    "agenticcarekit": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/agenticcarekit", "ack", "serve", "--mcp"],
      "env": { "ACK_SERVE_ROOT": "/path/to/myproject" }
    }
  }
}
```

An agent with only MCP access can scaffold a project, diagnose a broken environment, and
run an eval without touching a shell.

## Status

New — landed after the rest of the toolkit. Smoke-verified: the server starts, serves
its OpenAPI document, and answers `/v1/doctor` with the standard envelope. Treat it as
the newest surface in the project and read
[`packages/agenticcarekit/serve/`](../../packages/agenticcarekit/serve/) before relying
on a route's exact shape.

## Related

- [drive-from-an-agent.md](drive-from-an-agent.md) — the `--json` CLI surface
- [../architecture.md](../architecture.md) — why the sidecar is the enforcement chokepoint
- [../privacy.md](../privacy.md)
