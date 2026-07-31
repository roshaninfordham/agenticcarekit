# Recipe — swap the model (or the fallback, pack, redactor, egress class)

**Task:** change what a generated project runs on, without regenerating it.

## Command

```bash
ack swap model ollama:gemma4:12b
```

Five things are swappable:

| `what` | `value` | Example |
|---|---|---|
| `model` | `provider:model` | `ack swap model ollama:gemma4:e4b-mlx` |
| `fallback` | `provider:model` | `ack swap fallback cerebras:gemma-4-31b` |
| `pack` | pack name | `ack swap pack _template` |
| `redactor` | redactor name | `ack swap redactor healthcare.phi` |
| `egress` | `device` \| `trusted-network` \| `public-cloud` | `ack swap egress device` |

## Machine-readable

```bash
ack swap model ollama:gemma4:12b --json
```

```json
{"command": "swap", "data": {"from": "ollama:gemma4:e4b-mlx",
 "to": "ollama:gemma4:12b", "what": "model"},
 "envelope_version": 1, "ok": true, "version": "0.1.0", "error": null}
```

It edits `ack.toml` and preserves unknown keys — `AckConfig` keeps the full parsed dict,
so your own tables survive. Rewriting is deterministic: identical configs produce
byte-identical files.

## Choosing a tag

| Tag | Size | Context | Modalities in |
|---|---|---|---|
| `gemma4:e2b` | 7.2 GB | 128K | text, image, **audio** |
| `gemma4:e4b` | 9.6 GB | 128K | text, image, **audio** |
| `gemma4:12b` | 7.6 GB | 256K | text, image |
| `gemma4:26b` | 18 GB | 256K | text, image (MoE, ~3.8B active) |
| `gemma4:31b` | 20 GB | 256K | text, image (dense) |

- `-mlx` variants (`gemma4:e4b-mlx`) are preferred on Apple Silicon.
- `gemma4:cloud` and `gemma4:31b-cloud` are hosted — nothing to download.
- "E" is *effective* parameters: E2B ≈ 2.3B, E4B ≈ 4.5B.
- **Output is text only on every variant.** Speech output needs a separate TTS provider.

## Let detection choose instead

```bash
ack init --why           # full ranked table: every candidate, every filter that eliminated one
ack doctor               # what this machine can hold, honestly
```

The ranking applies seven hard filters (modalities, context, tool calling, RAM ×0.6,
VRAM ×0.9, disk ×1.15) and nine soft scores (quality tier, already-pulled bonus,
platform fit, download ETA, headroom). Every one contributes a human-readable reason.

## Swapping raises egress, deliberately and visibly

Adding a hosted fallback makes the project's runtime egress `public-cloud`, which is
the opposite of "private by default". That is why `ack init` defaults to `ollama` alone
with `egress = "device"`, and why the reason string says
*"add one with `--providers ollama,cerebras`"* rather than doing it for you.

After `ack swap fallback cerebras:...`, either declare a redactor or expect **E301** the
first time a `Sensitive` value reaches the hosted provider — and **E303** for
non-sensitive traffic if `[policy] egress` is still `device`.

```bash
ack swap egress public-cloud
ack swap redactor healthcare.phi
```

## Verify

```bash
ack manifest --json      # model, policy, capabilities, file list, discovered tools
ack sync                 # reconcile the tree against ack.toml
```

## Related

- [add-phi-redaction.md](add-phi-redaction.md) · [../privacy.md](../privacy.md)
- [explain-an-error-code.md](explain-an-error-code.md)
