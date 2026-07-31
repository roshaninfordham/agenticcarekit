# Recipe — run the offline demo

**Task:** run a generated project with networking disabled, and prove nothing left the
machine.

## Command

```bash
ack init care --blueprint on-device --yes --offline
cd care
ack demo --offline
```

Verified end to end at `0.1.0` — including through `uvx` on a machine that had never
seen the repo.

## Output

```
  Demo
    blueprint     on-device
    offline       yes
    exit code     0

    python -m app.main
    Summary (decision support only, for review):
    Patient SYN-PT-9042 is here for a seasonal-allergy follow-up. Reported symptoms:
    nasal congestion, occasional sneezing, mild fatigue. Follow-up was requested.
    (Synthetic summary — decision support only, not a diagnosis or treatment plan.)
    ───────────────────────── on-device summary ─────────────────────────
    ✓ 0 bytes egressed — all inference stayed on this device.

  done in 0.10s
```

## What `--offline` actually does

- Sets `ACK_OFFLINE=1` for the subprocess.
- Every `@tool` dispatches to its **mock** instead of its real function. The mock is
  mandatory: `@tool` without one fails at *import* with E502, which is what makes this
  mode real rather than aspirational.
- `provider_for(..., offline=True)` returns a fully-capable `MockProvider` regardless
  of the configured reference.
- No pull, no throughput probe, no registry call.

`ack demo` prefers `make demo`, then falls back to `python app/main.py`. It sets a
non-zero process exit status when the demo fails, and prints the real traceback rather
than swallowing it.

## The zero-egress assertion

The panel is not a claim the toolkit makes about itself — it is computed from the run's
own trace:

```python
from agenticcarekit.kernel.trace import read_jsonl, bytes_egressed, assert_zero_egress

events = read_jsonl("trace.jsonl")
bytes_egressed(events)      # sums bytes_out over events where egress != device
assert_zero_egress(events)  # raises AckError E303, listing the offending spans
```

Every model call, tool call, redaction, and policy decision emits one `TraceEvent`, and
`bytes_out` is the bytes that left the process toward a provider for that event.

## Which blueprints work today

| Blueprint | `ack demo --offline` |
|---|---|
| `on-device` | **works** — the output above |
| `voice-care` | fails: `VoiceLoop.__init__() got an unexpected keyword argument 'provider'` |
| `care-copilot` | fails: `AgentLoop.__init__() got an unexpected keyword argument 'system_prompt'` |

Both failures are template-vs-signature drift, tracked as known issue 2 in the
[README](../../README.md). `ack demo` reports them honestly and exits non-zero.

## If you installed with `uv tool install`

The generated `Makefile` calls `python`. On a machine where only `python3` exists, run
the demo through uv instead:

```bash
uv run --project /path/to/agenticcarekit ack demo --offline
```

## Related

- [drive-from-an-agent.md](drive-from-an-agent.md)
- [../privacy.md](../privacy.md)
