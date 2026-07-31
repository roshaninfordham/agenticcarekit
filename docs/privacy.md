# Privacy

What agenticcarekit enforces, how, and — at least as important — what it does not
claim. A security claim without a stated limit is marketing.

---

## The boundary in one paragraph

Sensitivity is a **type**, not a convention. A value wrapped in `Sensitive[T]` cannot
reach a `public-cloud` provider un-redacted, and the check happens at runtime, on one
code path, with an error that names the exact field and construction call site. A code
comment saying "don't send PHI here" is not a boundary; this is.

```python
from agenticcarekit.kernel.contracts import EgressClass, Sensitive
from agenticcarekit.kernel.policy import Policy
from agenticcarekit.kernel.providers import CerebrasProvider
from agenticcarekit.packs.healthcare import PHIRedactor

policy = Policy(
    egress=EgressClass.PUBLIC_CLOUD,
    redactors={"healthcare.phi": PHIRedactor()},
    default_redactor="healthcare.phi",
)
note = Sensitive("Patient Maria Alvarez, MRN 4482911, DOB 03/14/1979.", label="intake_note")

print(note)                                        # Sensitive(<intake_note>, origin=app.py:11)
print(note.unwrap_for(CerebrasProvider("gemma-4-31b"), policy))
# Patient [NAME-1], MRN [MRN-1], DOB [DATE-1].
```

Drop the `redactors=`/`default_redactor=` arguments and the same call raises:

```
PolicyViolation E301 | field=intake_note | call_site=app.py:11 | provider=cerebras
```

## The three egress classes

Everything is defined over exactly these, and nothing else:

| Class | Meaning |
|---|---|
| `device` | never leaves the machine |
| `trusted-network` | self-hosted, user-controlled |
| `public-cloud` | third-party API |

## The enforcement matrix

| value → provider egress | `device` | `trusted-network` | `public-cloud` |
|---|---|---|---|
| non-sensitive | allow | allow | allow |
| `Sensitive`, no redactor | allow | allow if policy egress ≥ trusted | **raise E301** |
| `Sensitive`, redactor declared | allow (raw) | allow (raw by default) | allow **redacted only** |

Above the table, applying to *all* traffic: any provider whose declared egress class is
broader than the project's `[policy] egress` is refused outright with **E303** — before
the value is revealed, sensitive or not. A project that declared `egress = "device"` did
not agree to send *anything* to a third party.

Two readings of that matrix were ambiguous and are now pinned by conformance fixtures
so the TypeScript port cannot diverge:

- A declared redactor satisfies the boundary **even when it replaces nothing**. The
  condition is "a redactor was declared and ran", not "the text changed" — otherwise a
  clean note is refused while a dirty one passes.
- Redaction applies at `public-cloud` and **not below**. The "raw or redacted per
  policy" cell resolves to raw for `device` and `trusted-network`; set
  `redact_at_or_above=EgressClass.TRUSTED_NETWORK` to opt into stricter behaviour.

## What the boundary guarantees

1. **One enforcement path.** `Sensitive.unwrap_for(provider, policy)` delegates to
   `Policy.unwrap()`, the only code in the toolkit that reveals a wrapped value on its
   way to a provider.
2. **`Sensitive` never reaches `public-cloud` un-redacted.** E301 carries the field
   label, the exact `file.py:123`, and the provider name — and prints the literal fix.
3. **A provider broader than the project limit is refused outright** (E303), checked
   before the value is revealed. A fallback chain that would silently promote a request
   from the local model to a hosted one fails closed.
4. **Non-text sensitive payloads are refused rather than guessed at.** A
   `Sensitive[dict]` headed for public cloud raises E301 explaining that redactors
   operate on text, instead of being `str()`-ed onto the wire.
5. **A misconfigured redactor fails at construction, not at first use** (E302).
6. **The audit trail never contains the data.** Policy events carry
   `{decision, reason, call_site, label, provider}`; redaction events carry
   `{redactor, categories, count}`. Neither carries the value or the removed spans, and
   a test greps every emitted line for the secret.

## Threat model summary

Full document:
[`kernel/policy/THREATMODEL.md`](../packages/agenticcarekit/kernel/policy/THREATMODEL.md).

**The guarantee is against accident, not malice.** Python has no private state.
`Sensitive` uses `__slots__` and a name-mangled slot, so the value is not in a
`__dict__` and does not appear in `vars()` — but `value._Sensitive__value` reads it,
and no Python library can prevent that. That gap is *asserted as a test*, so the claim
cannot quietly rot into a stronger one.

What actually holds the line in a codebase is reviewability. The only sanctioned raw
accessor is named `dangerously_reveal`. `git grep dangerously_reveal` is a **complete**
list of every place raw sensitive data is touched; anything in that output that is not
the policy engine or on-device-by-construction code is a review finding.

### Eight bypasses closed, with tests

All in `tests/test_policy_bypass.py`.

| # | Bypass | Closed by |
|---|---|---|
| 1 | `print(s)`, f-string, `format()`, `%s`, `logging` | masked `__repr__`/`__str__`/`__format__` — label and origin only |
| 2 | `pickle` (every protocol), `copy`, `deepcopy`, `json.dumps` | `__reduce__` raises `TypeError`; JSON has no encoder |
| 3 | Nest in a dict/list/tuple/dataclass, then `str()` that | container `str()` delegates to member `repr()` |
| 4 | Reach a broader provider than the project declared | E303 before the value is revealed; a redactor does not widen the limit |
| 5 | Leak through the audit trail itself | payloads carry decisions and category names only |
| 6 | Front-door misuse: bare value to `unwrap`, non-text payload, redactor returning junk | `TypeError` / E301 instead of a silent allow |
| 7 | Launder sensitivity through `s.map(...)` | `map` returns a `Sensitive`, still enforced |
| 8 | Attribute scraping: `vars(s)`, `s.__dict__`, `s.value` | `__slots__` + name mangling: nothing found by accident |

### Out of scope, stated plainly

- **Declared-capability spoofing.** `Policy` trusts `provider.capabilities().egress`,
  because invariant 2 makes declaration the contract. A provider that declares `device`
  and then opens a socket to a third party defeats the boundary, and no check at this
  layer can observe it. Providers are code you install; vet them accordingly. The
  sidecar is the structural answer — enforcement in one process that thin clients never
  touch.
- **Post-unwrap taint.** Once `unwrap` returns, the result is an ordinary `str`.
  Logging it, caching it, or forwarding it is outside the boundary.
- **Redaction quality.** The engine verifies a redactor ran and returned text; it
  cannot verify the redactor is any good. Read the pack's numbers.
- **Values never wrapped.** PHI that was never put in a `Sensitive` is invisible to the
  engine. Wrapping is a design act; blueprints wrap at the ingest boundary so it is not
  left to each call site.
- **Side channels.** Message length, timing, and token counts still leave the process.
  The boundary is about content.

---

## What agenticcarekit does NOT claim

Read this section twice if you are considering anything beyond synthetic data.

- **Not HIPAA compliance.** agenticcarekit is a toolkit, not a compliance program.
  It provides no BAA, no administrative or physical safeguards, no access controls, no
  audit-log retention policy, no breach procedure. Nothing here makes an application
  HIPAA-compliant.
- **Not Safe Harbor de-identification.** `healthcare.phi` covers all 18 HIPAA Safe
  Harbor identifier categories as *pattern-matching* rules — regexes plus a curated
  wordlist. There is no clinical NLP model, no manual review, and no legal sign-off.
  Safe Harbor is a legal determination made about a dataset by qualified people, not a
  property a regex library can confer.
- **Not certified against anything.** No SOC 2, no HITRUST, no ISO 27001, no FDA
  clearance, no CE mark. There is no certification body in this picture at all.
- **Not a medical device, and not clinical decision-making.** Decision support only:
  documentation, navigation, accessibility, triage routing, education. **Not diagnosis.
  Not treatment.** Every generated `.py` file carries that line at the top.
- **Not validated on real patient data.** Every number below was measured on the
  bundled synthetic/hand-written labelled set. Performance on your data is unknown
  until you measure it on your data.

### The measured numbers, honestly

From [`packs/healthcare/README.md`](../packages/agenticcarekit/packs/healthcare/README.md):

> Measured on the bundled labelled set (`evalsets/phi_labelled.jsonl`, 33 hand-written
> sentences, entity-level match = category + overlapping span):
> **precision 0.9688, recall 0.9394.**

Deliberately not 1.0/1.0. The labelled set includes cases the redactor gets wrong, so
the number is not rigged:

- **False positive** — a real name-word coincidence unrelated to any patient ("James
  Brown" as a song on the radio) still matches the curated wordlist. Any wordlist-based
  matcher trades this over-redaction for coverage of names lacking a context cue.
- **False negatives** — a name outside the curated wordlist with no honorific or
  spoken-context cue, and a `05-01-2024`-style date the regex set does not recognise.

`NAME` and `DATE` carry the misses. `tests/test_packs_healthcare.py` asserts that
re-running the scorer reproduces those exact four-decimal numbers *and* that they
appear verbatim in the pack README, so the published claim cannot silently drift from
the code.

**Do not read "redacted" as "safe" without reading those numbers.** A 0.9394 recall
means roughly six identifiers in a hundred survive. For synthetic data that is a fine
learning tool. For anything else it is a starting point for your own evaluation, not
an endpoint.

## No telemetry, ever

No analytics. No phone-home update check. No crash reporting. No "anonymous usage
statistics". Stated in the CLI header on every single command:

```
agenticcarekit 0.1.0 · ack — No telemetry, ever.
```

Provider API keys are probed for **presence only** — a boolean. Two tests assert that
no key value reaches a serialized `MachineFacts`, and that `ack doctor` never prints
one.

## Proving zero egress on your own run

The `on-device` blueprint does this at the end of every demo, and you can do it
anywhere:

```python
from agenticcarekit.kernel.trace import bytes_egressed, assert_zero_egress, read_jsonl

events = read_jsonl("trace.jsonl")
assert bytes_egressed(events) == 0        # sums bytes_out where egress != device
assert_zero_egress(events)                # raises AckError E303 naming the offenders
```

The panel is not a claim the toolkit makes about itself. It is an assertion computed
from your run's own trace, and it names the offending spans when it fails.

---

## Related

- [architecture.md](architecture.md) — why the sidecar is the enforcement chokepoint
- [CONTRACTS.md](CONTRACTS.md) — Contract 2, `Sensitive` and `PolicyContext`
- [errors.md](errors.md) — E301, E302, E303 in full
- [THREATMODEL.md](../packages/agenticcarekit/kernel/policy/THREATMODEL.md) — the complete document
- [healthcare pack README](../packages/agenticcarekit/packs/healthcare/README.md) — coverage and limitations
- [recipes/add-phi-redaction.md](recipes/add-phi-redaction.md) — drop the boundary into existing code
