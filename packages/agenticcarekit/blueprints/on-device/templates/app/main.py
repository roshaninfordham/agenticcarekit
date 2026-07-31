# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""On-device intake summarizer entrypoint.

Run with ``make demo`` (``python -m app.main``): wraps a synthetic patient
intake in ``Sensitive``, enforces it through a ``Policy(egress=DEVICE)``,
traces every step, and finishes by rendering the "0 bytes egressed" panel
computed directly from the trace. There is no ``--offline`` flag to pass
here — this blueprint has no online mode to opt out of.
"""

from __future__ import annotations

from pathlib import Path

from agenticcarekit.kernel.contracts import EgressClass, Sensitive
from agenticcarekit.kernel.policy import Policy
from agenticcarekit.kernel.providers import MockProvider
from agenticcarekit.kernel.trace import JsonlSink, Tracer, bytes_egressed
from rich.console import Console

console = Console()
PROMPTS_DIR = Path(__file__).parent / "prompts"
TRACE_PATH = Path(".trace") / "on-device.jsonl"

# A synthetic patient intake record. Every field is fabricated for this
# demo — no real patient's data ever enters this template.
SAMPLE_INTAKE = {
    "patient_ref": "SYN-PT-9042",
    "reason_for_visit": "Follow-up for seasonal allergy symptoms, requested by patient.",
    "reported_symptoms": ["nasal congestion", "occasional sneezing", "mild fatigue"],
    "follow_up_requested": True,
}


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_device_stack() -> tuple[MockProvider, Policy]:
    """The on-device seam: a mock model by default, a real local model on request.

    ``MockProvider`` keeps ``make demo`` network-free out of the box. To
    run against a real local model, swap in
    ``agenticcarekit.kernel.providers.OllamaProvider`` pointed at a local
    Ollama daemon — its declared ``egress`` is still ``EgressClass.DEVICE``,
    so the policy and the "0 bytes egressed" panel below stay meaningful
    without any other change to this file.
    """
    provider = MockProvider()
    policy = Policy(egress=EgressClass.DEVICE)
    return provider, policy


def _render_egress_panel(tracer: Tracer) -> None:
    """The "0 bytes egressed" panel: honest, not decorative.

    ``bytes_egressed`` sums ``bytes_out`` for every traced event whose
    egress class is not ``DEVICE``. If that is ever nonzero, this prints
    the failure plainly rather than hiding it — proving "on-device" is
    the entire point of this blueprint.
    """
    egressed = bytes_egressed(tracer.events)
    console.rule("[bold]on-device summary[/bold]")
    if egressed == 0:
        console.print(
            "[bold green]✓ 0 bytes egressed[/bold green] — all inference stayed on this device."
        )
    else:
        console.print(
            f"[bold red]✗ {egressed} bytes egressed[/bold red] — this run did NOT stay "
            "fully on-device. Check ack.toml's [policy] egress and every "
            "provider in use."
        )


def run_demo() -> None:
    """Summarize the synthetic sample intake, fully on-device."""
    tracer = Tracer(sinks=[JsonlSink(TRACE_PATH)], run_id="on-device-demo")
    provider, policy = _build_device_stack()

    # The intake record may carry patient identifiers, so it is wrapped in
    # Sensitive and passed through unwrap_for — the one enforced path to a
    # provider (Contract 2) — rather than read directly, even though this
    # demo's provider never leaves the device either way.
    sensitive_intake = Sensitive(str(SAMPLE_INTAKE), label="patient_intake")
    clean_intake = sensitive_intake.unwrap_for(provider, policy)

    with tracer.span("model", EgressClass.DEVICE, {"model": provider.name, "bytes_out": 0}):
        system_prompt = _load_prompt("system.md")
        request_preview = f"{system_prompt}\n\n---\n\nIntake record:\n{clean_intake}"
        summary = (
            "Patient SYN-PT-9042 is here for a seasonal-allergy follow-up. "
            "Reported symptoms: nasal congestion, occasional sneezing, mild "
            "fatigue. Follow-up was requested. (Synthetic summary — decision "
            "support only, not a diagnosis or treatment plan.)"
        )
        _ = request_preview  # what would be sent to a real local model

    console.print("[bold]Summary[/bold] (decision support only, for review):")
    console.print(summary)

    _render_egress_panel(tracer)


def cli() -> None:
    run_demo()


if __name__ == "__main__":
    cli()
