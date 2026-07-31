# Recipe — initialise a voice intake app

**Task:** scaffold a voice intake / clinical scribe project on an audio-capable model.

## Command

```bash
ack init myapp --blueprint voice-care
```

Interactive: it probes the machine, prints a plan with the reason for every choice,
and asks at most two questions. Non-interactive, for CI or an agent:

```bash
ack init myapp --blueprint voice-care --yes --offline --no-pull --no-git
```

| Flag | Effect |
|---|---|
| `--yes` / `-y` | ask nothing |
| `--offline` | no network: mocks and local only; skips the model pull and the throughput probe |
| `--no-pull` | generate the project but do not download the model |
| `--no-git` | do not run `git init` |
| `--model` / `-m` | pin the model instead of taking the recommendation |
| `--providers` | comma-separated chain, e.g. `ollama,cerebras` |
| `--pack`, `--capabilities`, `--name`, `--blueprint-path` | override the defaults |
| `--why` | print the full ranked table, including every eliminated candidate |
| `--json` | machine-readable |

## What you get

```
    + .cursor/rules/agenticcarekit.mdc
    + .github/copilot-instructions.md
    + .gitignore
    + AGENTS.md
    + CLAUDE.md                     -> AGENTS.md
    + Makefile
    + README.md
    + ack.toml
    + app/__init__.py
    + app/fixtures/sample_transcripts.py
    + app/main.py                   VoiceLoop wired to MockASR / MockTTS
    + app/prompts/scribe.md         prompts are .md files, never string literals
    + app/prompts/system.md
    + app/scribe.py                 IntakeNote model + extract(), transcript wrapped in Sensitive
    + app/tools/__init__.py         an example @tool with its mandatory mock
    + pyproject.toml
```

`ack init` twice with the same inputs produces a **byte-identical** tree — asserted by
sha256 per file, symlinks included.

## The plan screen

```
  Plan
    blueprint     voice-care
    model         gemma4:e4b-mlx          ← -mlx build: native Apple Silicon
                                            acceleration on Apple M5
                                          ← e4b: native audio input, ~4.5B effective
                                            parameters
    providers     ollama                  ← local primary, no hosted fallback: egress
                                            stays on device and nothing leaves the
                                            machine (add one with --providers
                                            ollama,cerebras)
    pack          healthcare
    capabilities  voice, extract
    egress        device                  ← redactor healthcare.phi

  Re-run this exactly:
    ack init --blueprint voice-care --model gemma4:e4b-mlx \
      --providers ollama --pack healthcare --yes
```

The "Re-run this exactly" block is the point — paste it into your README, your CI, or a
message to a teammate.

## Why an audio-capable model is mandatory

`voice-care` declares `modalities_in = ["text", "audio"]`. Native audio input exists on
`gemma4:e2b` and `gemma4:e4b` (and their `-mlx` builds) **only**, so:

```bash
ack init myapp --blueprint voice-care --model gemma4:31b --yes
# ✗ E203  Model does not support a required input modality
#         ack init --model gemma4:e4b-mlx
```

The check runs before any network call. On a machine that cannot hold any audio-capable
model, `init` refuses with E203 naming the binding constraint rather than silently
substituting a text model.

## Caveat

`ack demo --offline` on a generated `voice-care` project currently fails with
`TypeError: VoiceLoop.__init__() got an unexpected keyword argument 'provider'` — the
template and the landed signature disagree (known issue 2 in the
[README](../../README.md)). Scaffolding works; the demo wiring needs one edit in
`app/main.py`. Use `--blueprint on-device` for a demo that runs end to end today.

## Related

- [run-the-offline-demo.md](run-the-offline-demo.md)
- [swap-the-model.md](swap-the-model.md)
- [explain-an-error-code.md](explain-an-error-code.md)
