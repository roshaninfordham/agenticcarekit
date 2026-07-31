# Blueprint: voice-care

**Track:** Voice for Care — intake and clinical scribe.

## What it generates

`ack new --blueprint voice-care` (or `ack init`, choosing this blueprint)
scaffolds a project with:

- `app/main.py` — a `VoiceLoop` wired to mock ASR/TTS by default, with a
  clearly marked seam to swap in a real ASR/TTS provider or telephony
  adapter.
- `app/scribe.py` — turns a turn transcript into a structured `IntakeNote`
  (pydantic model) via `agenticcarekit.capabilities.extract.extract`.
- `app/prompts/*.md` — system and scribe prompts, framed as decision
  support.
- `app/tools/` — an example `@tool` with a mandatory mock.
- `app/fixtures/` — synthetic sample intake transcripts.
- `Makefile`, `pyproject.toml`, `README.md` for the generated project.

Requires: text + audio input, tool calling, 32,768-token context — see
`blueprint.toml`.

## Running the demo

Inside a project generated from this blueprint:

```sh
make demo   # python -m app.main --offline — mock ASR/TTS, synthetic transcript, no network
```

## Scope

Decision support only — not diagnosis, not treatment. Synthetic/public
data only. This blueprint (and every project generated from it) produces
documentation, navigation, accessibility, triage *routing*, and patient
education — never a clinical diagnosis or treatment recommendation. All
bundled sample data is synthetic.

## Ejectable

Everything under `templates/` is plain, ejectable Python: this is your
code now. Nothing generated from this blueprint calls back into
agenticcarekit's generator.
