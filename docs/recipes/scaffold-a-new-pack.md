# Recipe — scaffold a new pack (or provider, redactor, capability, blueprint)

**Task:** extend agenticcarekit for a domain, a model host, or a project shape it does
not ship.

Five extension points, one command, working examples rather than stubs.

## Command

```bash
ack new pack cardio
ack new redactor masker
ack new provider vllm
ack new capability summarise
ack new blueprint triage
```

All five take `--path` (default `.`) and `--json`.

## What each one writes

```
$ ack new pack cardio
  New pack: cardio
    + packs/cardio/README.md
    + packs/cardio/__init__.py
    + packs/cardio/pack.toml
    + packs/cardio/redactors.py
  Next: add the entry point to pyproject.toml, then: ack doctor

$ ack new redactor masker
    + redactors/masker_redactor.py
  Next: set  redactor in ack.toml, then: ack sync

$ ack new provider vllm
    + providers/vllm_provider.py
  Next: import it and pass it where a Provider is expected

$ ack new capability summarise
    + capabilities/summarise/__init__.py
    + capabilities/summarise/capability.toml
  Next: ack add summarise

$ ack new blueprint triage
    + blueprints/triage/README.md
    + blueprints/triage/blueprint.toml
    + blueprints/triage/templates/README.md.tmpl
    + blueprints/triage/templates/app/main.py.tmpl
  Next: ack init --blueprint-path ./blueprints --blueprint triage
```

That last line matters: `--blueprint-path` (or the `ACK_BLUEPRINT_PATH` environment
variable) makes your blueprint discoverable without installing anything.

## Read the two shipped packs first

`_template/` exists precisely so "pack" is an interface rather than a folder someone
will redesign the first time a second domain shows up.

| | |
|---|---|
| [`packs/_template/`](../../packages/agenticcarekit/packs/_template/) | near-empty: `manifest.toml`, one model, `TemplateRedactor` (`_template.none`, pure passthrough), and a README documenting every manifest key |
| [`packs/healthcare/`](../../packages/agenticcarekit/packs/healthcare/) | full: FHIR-lite models, `PHIRedactor`, seeded synthetic generator, scoring, two eval sets |

A pack provides some or all of: domain models, redactor implementations, synthetic data,
and eval sets. Nothing more is required.

## Discovery

Packs are found through the `agenticcarekit.packs` entry-point group:

```toml
[project.entry-points."agenticcarekit.packs"]
cardio = "mypackage.cardio"
```

Install the package and the pack appears. No central registration, no PR to this repo.
The first-party registry — [`registry.toml`](../../registry.toml) — lists what ships in
the box; a third-party pack does not need to be in it.

Note the two conventions differ slightly today: first-party packs carry
`manifest.toml`, while `ack new pack` scaffolds `pack.toml`. Follow the shipped packs
if you want to match them exactly.

## The protocols you are implementing

```python
class Redactor(Protocol):                       # packs provide these
    name: str
    def redact(self, text: str) -> tuple[str, list[Redaction]]: ...

class Provider(Protocol):                       # and these
    name: str
    def capabilities(self) -> Capabilities: ...
    def generate(self, req: GenerateRequest) -> GenerateResponse: ...
    def stream(self, req: GenerateRequest) -> Iterator[Chunk]: ...
```

Two rules for a provider, both load-bearing:

1. **Declare capabilities honestly.** The policy engine trusts
   `capabilities().egress`. A provider that declares `device` and then opens a socket
   to a third party defeats the boundary and no check at this layer can see it.
   Declaring *less* than you support costs a loud fixable error; declaring more costs a
   silent wrong answer.
2. **Expose the raw client.** Convention is an attribute named `client`. Nothing hides
   the provider.

For a blueprint, `blueprint.toml` is three tables:

```toml
[blueprint]
name = "triage"
description = "..."
track = "..."

[requires]
modalities_in = ["text"]
tool_calling = true
context_tokens = 32768

[defaults]
capabilities = ["agents"]
pack = "healthcare"
```

`[requires]` is what the recommendation engine filters candidate models against — it is
how "this needs audio" becomes E203 at startup instead of a runtime surprise.

Templates: files ending `.tmpl` get `{{var}}` substitution then lose the suffix;
everything else is copied verbatim. Exactly nine variables are substituted —
`project_name`, `blueprint`, `pack`, `model_primary`, `model_fallback`, `egress`,
`redactor`, `capabilities_list`, `ack_version` — and an unknown `{{...}}` is **E501**,
not silence.

## Verifying a third-party plugin

`ack verify-plugin <name>` is **roadmap** — not implemented. Until it exists, run the
shared corpus yourself against an adapter:

```bash
python3 spec/conformance/runner.py <your-adapter-command>
```

The protocol is documented in
[`spec/conformance/README.md`](../../spec/conformance/README.md). Do not vendor the
fixtures — a port that copies them has forked the spec.

## Related

- [`packs/_template/README.md`](../../packages/agenticcarekit/packs/_template/README.md)
- [../architecture.md](../architecture.md) · [`registry.toml`](../../registry.toml)
- [add-phi-redaction.md](add-phi-redaction.md)
