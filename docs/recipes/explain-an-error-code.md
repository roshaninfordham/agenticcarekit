# Recipe — explain an error code

**Task:** you got `E203` (or any code) and want the fix, not a search engine.

## Command

```bash
ack explain E203
ack explain E203 --json
```

## Output

```
agenticcarekit 0.1.0 · ack — No telemetry, ever.

  E203  Model does not support a required input modality

    what   a request or blueprint needs an input modality (e.g. audio) this model lacks.
    why    native audio input is available on gemma4:e2b and gemma4:e4b only.

  Fix:
    ack init --model gemma4:e4b-mlx
```

```json
{"command": "explain",
 "data": {"code": "E203",
          "title": "Model does not support a required input modality",
          "what": "a request or blueprint needs an input modality (e.g. audio) this model lacks.",
          "why": "native audio input is available on gemma4:e2b and gemma4:e4b only.",
          "fix": "ack init --model gemma4:e4b-mlx"},
 "envelope_version": 1, "ok": true, "version": "0.1.0", "error": null,
 "elapsed_ms": null}
```

Codes are case-insensitive: `ack explain e203` works.

## Where the answer comes from

[`spec/errors.json`](../../spec/errors.json) — one registry, 34 codes, read by the CLI,
the Python implementation, the TypeScript port, and the MCP surface. Not a doc page that
drifts from the code: a code raised in the implementation but missing from the registry
is a **test failure** (`tests/test_conformance_registry.py`).

## Reading a code without running anything

| Range | Domain | Typical first move |
|---|---|---|
| `E0xx` | bootstrap / environment | `ack doctor` |
| `E1xx` | model / provider / network | `ack doctor`, or `--offline` |
| `E2xx` | capability mismatch | `ack init --model <a model that has it>` |
| `E3xx` | policy and privacy | declare a redactor, or keep `egress = "device"` |
| `E4xx` | project config | `ack sync` |
| `E5xx` | generation / templates | usually a blueprint bug — file it |
| `E6xx` | eval | check the golden set path |

The full table with every `what`/`why`/`fix` is in [../errors.md](../errors.md).

## From Python

```python
from agenticcarekit.kernel.contracts import explain, error_registry

explain("E203")          # -> ErrorEntry(code, title, what, why, fix) or None
len(error_registry())    # -> 34
```

And when you raise your own:

```python
raise AckError(
    "the thing that happened",
    code="E1xx",                  # must already exist in spec/errors.json
    why="why it is refused rather than worked around",
    fix="the literal command to run",
    details={"anything": "structured"},
)
```

`err.render()` produces the canonical CLI shape above; `err.to_dict()` produces the
`--json` / MCP shape.

## Two codes worth knowing before you hit them

**E301 — sensitive value blocked at egress.** The most useful error in the project. It
names the field label, the exact construction call site (`app.py:9`), and the provider:

```
  ✗ E301  Sensitive value blocked at egress boundary
          a Sensitive[T] value was about to reach a public-cloud provider with no redactor declared.

          declare a redactor: [policy] redactor = "healthcare.phi"   # or keep egress = "device"
```

**E502 — tool declared without a mock.** Raised at *import*, not at call time, because
every tool ships a mock and that is what makes `ack demo --offline` real.

## Known drift

`E601`'s fix mentions `ack eval --init`, which is not implemented. Write
`evals/golden.jsonl` by hand — see [run-evals.md](run-evals.md).

## Related

- [../errors.md](../errors.md) — all 34 codes
- [../privacy.md](../privacy.md) — E301, E302, E303 in context
- [drive-from-an-agent.md](drive-from-an-agent.md)
