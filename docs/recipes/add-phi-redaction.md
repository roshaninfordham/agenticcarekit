# Recipe — add PHI redaction to code you already have

**Task:** you have a working script that sends clinical text to a model. You want the
PHI boundary without rewriting anything.

You do not need `ack init`, a blueprint, or a generated project. The boundary is one
import and one wrapper.

## Install

```bash
uv pip install git+https://github.com/roshaninfordham/agenticcarekit
```

## The ten lines

```python
from agenticcarekit.kernel.contracts import EgressClass, Sensitive
from agenticcarekit.kernel.policy import Policy
from agenticcarekit.packs.healthcare import PHIRedactor

policy = Policy(
    egress=EgressClass.PUBLIC_CLOUD,                 # what this project is allowed to do
    redactors={"healthcare.phi": PHIRedactor()},     # what it may redact with
    default_redactor="healthcare.phi",
)
note = Sensitive("Patient Maria Alvarez, MRN 4482911, DOB 03/14/1979.", label="intake_note")
prompt = note.unwrap_for(my_provider, policy)        # the only path to the raw value
```

Verified output:

```
Patient [NAME-1], MRN [MRN-1], DOB [DATE-1].
```

`my_provider` is anything satisfying the `Provider` protocol — including your own class
with a `name` and a `capabilities()` that declares its `EgressClass`. Nothing forces you
to use our HTTP clients.

## What it buys you immediately

```python
print(note)          # Sensitive(<intake_note>, origin=app.py:9)   — masked
f"{note}"            # same
json.dumps(note)     # TypeError
pickle.dumps(note)   # TypeError
copy.deepcopy(note)  # TypeError
```

And with no redactor declared, the same `unwrap_for` refuses:

```python
strict = Policy(EgressClass.PUBLIC_CLOUD)
note.unwrap_for(hosted_provider, strict)
# PolicyViolation E301 | field=intake_note | call_site=app.py:9 | provider=cerebras
```

The error names the field, the exact construction site, and the provider — because a
vague policy error is one nobody fixes.

## Keeping data on-device instead

If nothing should leave the machine at all, do not declare a redactor — declare the
limit:

```python
policy = Policy(EgressClass.DEVICE)
```

Now **any** provider declaring `trusted-network` or `public-cloud` is refused with
**E303**, sensitive value or not, checked before the value is revealed. A declared
redactor does not widen that limit.

## Wire the trace while you are here

```python
from agenticcarekit.kernel.trace import Tracer, JsonlSink, bytes_egressed

tracer = Tracer(sinks=[JsonlSink("trace.jsonl")])
policy = Policy(EgressClass.DEVICE, emit=tracer.emit)
...
assert bytes_egressed(tracer.events) == 0
```

Every policy decision emits `kind="policy"` with `{decision, reason, call_site, label,
provider}`; every redaction emits `kind="redaction"` with `{redactor, categories,
count}`. Neither payload ever contains the value or the removed spans.

## Read this before you trust it

`healthcare.phi` is **regex and wordlist de-identification, not certified Safe Harbor
de-identification.** Measured on its own labelled set: **precision 0.9688, recall
0.9394**. Roughly six identifiers in a hundred survive. Names outside the curated
wordlist with no honorific or spoken-context cue are missed; a name-word coincidence
unrelated to any patient is over-redacted.

Score it against *your* data before relying on it:

```python
from agenticcarekit.packs.healthcare import score_phi_redactor
score_phi_redactor(PHIRedactor(), "my_labelled_set.jsonl")
# {"precision": ..., "recall": ..., "per_category": {...}}
```

## Bring your own redactor

The protocol is two members:

```python
class MyRedactor:
    name = "mine.phi"
    def redact(self, text: str) -> tuple[str, list[Redaction]]: ...
```

Scaffold one with `ack new redactor <name>`, or write the class yourself — nothing
registers it but the dict you pass to `Policy`.

## Related

- [../privacy.md](../privacy.md) — the full boundary, threat model, and non-claims
- [THREATMODEL.md](../../packages/agenticcarekit/kernel/policy/THREATMODEL.md)
- [healthcare pack README](../../packages/agenticcarekit/packs/healthcare/README.md)
- [scaffold-a-new-pack.md](scaffold-a-new-pack.md)
