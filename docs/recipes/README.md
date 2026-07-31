# Recipes

Task → the exact command. Every command here was run against this repo at `0.1.0`
before it was written down. Where something is not implemented, the recipe says so
rather than showing you a command that will fail.

| Recipe | Task |
|---|---|
| [init-a-voice-app.md](init-a-voice-app.md) | Scaffold a voice intake / scribe project |
| [run-the-offline-demo.md](run-the-offline-demo.md) | Prove zero egress with networking disabled |
| [swap-the-model.md](swap-the-model.md) | Change model, fallback, pack, redactor, or egress class |
| [add-phi-redaction.md](add-phi-redaction.md) | Drop the privacy boundary into code you already have |
| [run-evals.md](run-evals.md) | Score a project against a golden set |
| [eject-prompts.md](eject-prompts.md) | Move packaged prompts into your own source |
| [scaffold-a-new-pack.md](scaffold-a-new-pack.md) | Add a domain pack, provider, redactor, capability, or blueprint |
| [drive-from-an-agent.md](drive-from-an-agent.md) | Run the whole toolkit from `--json`, no TTY |
| [run-the-sidecar.md](run-the-sidecar.md) | Expose the kernel over local HTTP or MCP |
| [explain-an-error-code.md](explain-an-error-code.md) | Turn a code into a fix |

## Two conventions used throughout

**Invoking `ack`.** Three ways, in ascending order of permanence:

```bash
uvx --from git+https://github.com/roshaninfordham/agenticcarekit ack <command>   # no install
uv tool install git+https://github.com/roshaninfordham/agenticcarekit           # ack on PATH
uv run ack <command>                                                            # inside a checkout
```

`ack doctor` and `ack explain` currently need the checkout form — see known issue 1 in
the [README](../../README.md).

**`--json` everywhere.** Every command takes `--json` and returns the same envelope:

```json
{"envelope_version": 1, "ok": true, "command": "...", "version": "0.1.0",
 "elapsed_ms": null, "data": {...}, "error": null}
```

Failure sets `ok: false`, fills `error`, and sets a non-zero process exit status.
