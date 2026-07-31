# Blueprint: on-device

**Track:** On-Device — fully offline.

## What it generates

`ack new --blueprint on-device` (or `ack init`, choosing this blueprint)
scaffolds a project with:

- `app/main.py` — summarizes a synthetic patient intake wrapped in
  `Sensitive`, enforced through a `Policy(egress=DEVICE)`, traced with a
  `Tracer` + `JsonlSink`, and finishing with the **"0 bytes egressed"**
  panel computed from the trace via `bytes_egressed(events)` — an honest
  failure message if it is ever nonzero.
- `app/prompts/*.md` — the summarization prompt, framed as decision
  support.
- `Makefile`, `pyproject.toml`, `README.md` for the generated project.

Requires: text input only, no tool calling, 8,192-token context — see
`blueprint.toml`. This is deliberately the smallest-footprint blueprint,
so `ack init`'s recommendation engine only offers models that genuinely
fit a local, offline deployment.

## Running the demo

Inside a project generated from this blueprint:

```sh
make demo   # python -m app.main — fully offline, no --offline flag needed
```

## Scope

Decision support only — not diagnosis, not treatment. Synthetic/public
data only. This blueprint (and every project generated from it) produces
a plain-language summary for documentation and patient communication —
never a clinical diagnosis or treatment recommendation. The bundled
sample intake is synthetic.

## Ejectable

Everything under `templates/` is plain, ejectable Python: this is your
code now. Nothing generated from this blueprint calls back into
agenticcarekit's generator.
