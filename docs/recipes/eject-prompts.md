# Recipe — eject prompts

**Task:** take ownership of every prompt the toolkit ships, so behaviour changes without
touching logic.

## Command

```bash
ack eject prompts
```

## Output

Run inside a generated project:

```
  Ejected prompts
    + prompts/blueprints/care-copilot/templates/app/system.md
    + prompts/blueprints/on-device/templates/app/system.md
    + prompts/blueprints/voice-care/templates/app/scribe.md
    + prompts/blueprints/voice-care/templates/app/system.md
    + prompts/capabilities/extract/extract.md
    + prompts/capabilities/extract/repair.md
    + prompts/capabilities/voice/assistant.md
    + prompts/evals/judge_rubric.md
```

Every prompt in agenticcarekit is a `.md` file on disk — never a string literal — which
is the only reason this command can exist.

| File | What it drives |
|---|---|
| `capabilities/extract/extract.md` | the structured-extraction prompt (`{schema_json}`, `{text}`) |
| `capabilities/extract/repair.md` | the single repair retry (`{malformed}`, `{errors}`) |
| `capabilities/voice/assistant.md` | the voice assistant system prompt, decision-support framing |
| `evals/judge_rubric.md` | the LLM-judge rubric (`{input}`, `{expected}`, `{actual}`) |
| `blueprints/*/…/system.md`, `scribe.md` | per-blueprint system prompts |

## It never clobbers

Files that already exist come back marked `=  (exists; --force to overwrite)` and are
reported under `skipped` in `--json`. Pass `--force` to overwrite:

```bash
ack eject prompts --force
ack eject prompts --json      # {"copied": [...], "skipped": [...], "available": ["prompts"]}
```

## Point your code at the ejected copy

`extract()` and `VoiceLoop` both take an explicit path:

```python
from agenticcarekit.capabilities.extract import extract
extract(provider, IntakeNote, transcript, prompt_path="prompts/capabilities/extract/extract.md")

from agenticcarekit.capabilities.voice import VoiceLoop
VoiceLoop(asr, llm, tts, system_prompt_path="prompts/capabilities/voice/assistant.md")
```

## What else can be ejected

Today: `prompts`, and only `prompts`.

```bash
ack eject nope
# ✗ E401  nothing to eject called 'nope'
#         ejectable things: prompts
```

The registry is a single dict (`cli/project_ops.py::EJECTABLES`); more entries are
additive. Roadmap.

## Ejectability without the command

`ack eject` is the mechanical version of a promise the architecture already keeps. The
ladder, top to bottom — each rung usable on its own:

| Rung | Import |
|---|---|
| Blueprint | your generated `app/` — already yours |
| Packs | `from agenticcarekit.packs.healthcare import PHIRedactor, SyntheticGenerator` |
| Capabilities | `from agenticcarekit.capabilities.extract import extract` |
| Kernel | `from agenticcarekit.kernel.providers import build_ollama_chat, provider_for` |
| Raw client | `OllamaProvider("gemma4:e4b").client` — the `httpx.Client` itself |

Nothing hides the provider. That is the promise that makes depending on this project
reversible.

## Related

- [../architecture.md](../architecture.md) — the full ejectability ladder
- [run-evals.md](run-evals.md) — editing the judge rubric
