# Threat model — the privacy boundary

Scope: `agenticcarekit.kernel.policy` (W-B) and the `Sensitive` /
`PolicyContext` contract it implements (`docs/CONTRACTS.md`, Contract 2).

Invariant 1 says *sensitivity is a type, not a convention*. This document
says exactly how far that holds, because a security claim without a stated
limit is marketing.

## What the boundary guarantees

1. **One enforcement path.** `Sensitive.unwrap_for(provider, policy)`
   delegates to `Policy.unwrap()`, which is the only code in the toolkit that
   reveals a wrapped value on its way to a provider. One path means one place
   to audit and one place to change.
2. **`Sensitive` never reaches `public-cloud` un-redacted.** No declared
   redactor for the value → `PolicyViolation` **E301**, carrying the field
   label, the exact construction call site (`file.py:123`), and the provider
   name. The fix (`[policy] redactor = "healthcare.phi"`) is printed with the
   error.
3. **A provider broader than the project limit is refused outright** —
   **E303**, sensitive value or not, checked *before* the value is revealed.
   A fallback chain that silently promotes a request from the local model to
   a hosted one fails closed.
4. **Non-text sensitive payloads are refused rather than guessed at.**
   Redactors operate on text; a `Sensitive[dict]` headed for public cloud
   raises E301 explaining that only `str` is redactable, instead of being
   `str()`-ed onto the wire.
5. **A misconfigured redactor fails at construction, not at first use.**
   Naming a redactor no installed pack provides raises **E302** when the
   `Policy` is built.
6. **The audit trail never contains the data.** Every decision emits a
   `TraceEvent` (`kind="policy"`, payload `decision / reason / call_site /
   label / provider`); redactions emit `kind="redaction"` (payload
   `redactor / categories / count`, `bytes_out=0`). Neither carries the
   wrapped value, and neither carries the spans a redactor removed.

## What it cannot guarantee, in Python

**The guarantee is against accident, not malice.** Python has no private
state. `Sensitive` uses `__slots__` and a name-mangled slot, so the value is
not in a `__dict__` and does not appear in `vars()` — but
`value._Sensitive__value` reads it, and nothing in this or any Python library
can prevent that. `tests/test_policy_bypass.py` asserts that this gap is
real, so the claim cannot quietly rot into a stronger one.

What actually holds the line in a codebase is reviewability: the only
sanctioned raw accessor is named **`dangerously_reveal`**. `git grep
dangerously_reveal` is a *complete* list of every place raw sensitive data is
touched — inside the policy engine (after authorization), and in code that
stays on-device by construction. Anything else in that grep output is a
review finding.

Also out of scope:

- **Declared-capability spoofing.** `Policy` trusts
  `provider.capabilities().egress`, because invariant 2 makes declaration the
  contract. A provider that declares `device` and then opens a socket to a
  third party defeats the boundary, and no check at this layer can observe
  it. Providers are code you install; vet them accordingly. (The sidecar,
  W-K, is the structural answer: policy and redaction live in one process, so
  thin clients in other languages cannot bypass what they never touch.)
- **Post-unwrap taint.** Once `unwrap` returns, the result is an ordinary
  `str`. Logging it, caching it, or forwarding it is outside the boundary.
- **Redaction quality.** The engine verifies a redactor ran and returned
  text; it cannot verify the redactor is any good. Packs publish precision
  and recall for theirs (W-F). Do not read "redacted" as "safe" without
  reading those numbers.
- **Values never wrapped.** PHI that was never put in a `Sensitive` is
  invisible to the engine. Wrapping is a design act; blueprints wrap at the
  ingest boundary so it is not left to each call site.
- **Side channels.** Message length, timing, and token counts still leave the
  process. The boundary is about content.

## Bypasses closed, with tests

All in `tests/test_policy_bypass.py`.

| # | Bypass | Closed by | Test |
|---|---|---|---|
| 1 | Print or interpolate the box: `print(s)`, f-string, `format()`, `%s`, `logging` | masked `__repr__` / `__str__` / `__format__` — shows label + origin only | `test_repr_str_and_format_are_masked`, `test_log_formatting_does_not_leak` |
| 2 | Serialize it out of the process: `pickle`, `copy`, `deepcopy`, `json.dumps` | `__reduce__` raises `TypeError`; JSON has no encoder for it | `test_pickling_is_refused_at_every_protocol`, `test_copying_is_refused`, `test_json_dumps_raises_type_error` |
| 3 | Nest it in a structure and stringify that: `str({"note": s})`, lists, tuples, dataclasses | container `str()` delegates to member `repr()`, which is masked | `test_nesting_in_a_structure_still_masks_on_str` |
| 4 | Reach a broader provider than the project declared (`device` limit, `trusted-network` or `public-cloud` provider) | E303 before the value is revealed | `test_trusted_network_provider_is_refused_by_a_device_only_project`, `test_a_declared_redactor_does_not_widen_the_egress_limit` |
| 5 | Leak through the audit trail itself | trace payloads carry decisions and category names, never values or spans | `test_no_trace_event_ever_carries_the_raw_value` |
| 6 | Walk past the front door: hand `unwrap` a bare `str`; hide data in a non-text payload; trust a redactor that returns junk | `TypeError` instead of a silent allow; E301 for non-`str`; `TypeError` for non-`str` redactor output | `test_unwrap_refuses_a_bare_value_instead_of_silently_allowing_it`, `test_non_text_sensitive_cannot_be_smuggled_past_a_text_redactor`, `test_a_redactor_returning_non_text_is_not_trusted` |
| 7 | Launder sensitivity through a transform (`s.map(...)`) | `map` returns a `Sensitive`, still enforced | `test_map_does_not_launder_sensitivity` |
| 8 | Scrape attributes (`vars(s)`, `s.__dict__`, `s.value`) | `__slots__` + name mangling: nothing found by accident | `test_attribute_scraping_finds_nothing_by_accident` |

Documented gaps are asserted too — `test_gap_name_mangled_internals_are_reachable_by_a_determined_developer`,
`test_gap_an_unwrapped_value_carries_no_taint`, and
`test_gap_a_provider_that_lies_about_its_egress_defeats_the_boundary` fail if
anyone later claims more than the boundary delivers.
