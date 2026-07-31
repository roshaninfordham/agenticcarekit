# Recipe — run evals

**Task:** score a project against a committed golden set, offline.

## Command

```bash
ack eval --offline
ack eval --offline --json
```

## First, create the golden set

`ack eval` scores against JSONL files in `evals/` inside the project. There is no
scaffolding command yet — **E601's fix string mentions `ack eval --init`, which is not
implemented** (known drift, see [../errors.md](../errors.md)). Write the file:

```bash
mkdir -p evals
cat > evals/golden.jsonl <<'EOF'
{"id": "c1", "input": "Patient reports nasal congestion and sneezing.", "expected": "seasonal allergy follow-up"}
{"id": "c2", "input": "Needs a refill of her inhaler.", "expected": "medication refill"}
EOF
```

Each line is an `EvalCase`: `id`, `input`, `expected`.

## Output

```
  Eval
    golden set    evals/golden.jsonl
    cases         2
    exact match   0.0%

  done in 0.03s
```

Without `--offline`, the run resolves the project's provider chain from `ack.toml` via
`provider_for`, so it hits your real model.

## The harness, directly

The CLI is a thin wrapper. Everything is importable:

```python
from agenticcarekit.evals import load_golden, run_eval, judge_with_provider, score_table, report_json

cases = load_golden("evals/golden.jsonl")
report = run_eval(cases, my_model_callable)                   # exact match only
report = run_eval(cases, my_model_callable, judge=judge_with_provider(provider, "prompts/judge_rubric.md"))

print(score_table(report))    # a static rich.Table, one row per case, no live updates
report_json(report)           # sorted-key dict: exact_match_rate, judge_score_avg, rows
```

`judge_with_provider` loads its rubric from a **`.md` file**, never a string literal, so
you can change judging behaviour without touching logic. The shipped rubric is
`agenticcarekit/evals/prompts/judge_rubric.md`; `ack eject prompts` copies it into your
project. A judge response that will not parse as a float scores `0.0` rather than
killing the run mid-way.

## Clinical eval sets in the healthcare pack

Two labelled sets ship with the pack and are worth reading before writing your own:

| File | Contents |
|---|---|
| `packs/healthcare/evalsets/phi_labelled.jsonl` | 33 hand-written sentences covering all 18 HIPAA identifier categories, plus tricky negatives *and* deliberate known-limitation cases |
| `packs/healthcare/evalsets/intake_extraction.jsonl` | 10 transcript → structured-summary golden cases |

The PHI set is worth copying as a *method*: it includes cases the redactor gets wrong,
which is why its published precision/recall is 0.9688/0.9394 and not a rigged 1.0.

## Score the redactor rather than the model

```python
from agenticcarekit.packs.healthcare import PHIRedactor, score_phi_redactor

score_phi_redactor(PHIRedactor(), "packs/healthcare/evalsets/phi_labelled.jsonl")
# {"precision": 0.9688, "recall": 0.9394, "per_category": {...}}
```

Entity-level match = category plus overlapping span. `tests/test_packs_healthcare.py`
asserts these numbers reproduce *and* appear verbatim in the pack README, so a
published claim cannot drift from the code.

## The faster loop

For the inner loop, `ack check` is the 30-second gate — lint plus a selftest of the
contract doctests:

```bash
ack check --json
```

```json
{"command": "check", "data": {"budget_seconds": 30, "duration_ms": 72.6, "ok": true,
 "steps": [{"name": "lint", "status": "pass", "duration_ms": 11.9},
           {"name": "selftest", "status": "pass", "doctests_attempted": 24}],
 "within_budget": true}, "envelope_version": 1, "ok": true}
```

Non-zero exit status on failure, so a verification loop can read the shell status.

## Related

- [drive-from-an-agent.md](drive-from-an-agent.md)
- [eject-prompts.md](eject-prompts.md)
- [../errors.md](../errors.md) — E601, E602, E603
