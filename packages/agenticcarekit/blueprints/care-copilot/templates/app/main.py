# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Care-copilot admin demo entrypoint.

Run with ``make demo`` (``python -m app.main --offline``, the default):
an ``AgentLoop`` wired with the four tools in ``app/tools`` runs a
synthetic administrative task end to end — every tool call dispatches to
its mock, so nothing touches the network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agenticcarekit.capabilities.agents import AgentLoop
from agenticcarekit.kernel.providers import MockProvider
from agenticcarekit.kernel.trace import ConsoleSink, JsonlSink, Tracer, bytes_egressed
from rich.console import Console

from app.tools import ALL_TOOLS

console = Console()
PROMPTS_DIR = Path(__file__).parent / "prompts"

# A handful of synthetic administrative tasks a front-office user might
# hand this copilot. Every payer, patient reference, and specialist named
# here is fabricated for this demo.
SAMPLE_TASKS = [
    "Check eligibility for an MRI under payer SYN-PAYER-AETNA for patient "
    "SYN-PT-4471, then draft the prior auth if one is required.",
    "Find a cardiology referral slot in the SYN-PAYER-UHC network and "
    "schedule the first available one for patient SYN-PT-2208.",
]


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_offline_stack() -> tuple[AgentLoop, MockProvider]:
    """The offline seam: a mock model, real tools (each with its own mock).

    To go live, replace ``MockProvider`` with ``OllamaProvider`` /
    ``CerebrasProvider`` behind a ``FallbackChain``
    (``agenticcarekit.kernel.providers``). The tools stay exactly as they
    are — each already carries the real-vs-mock seam internally.
    """
    provider = MockProvider()
    loop = AgentLoop(provider=provider, tools=ALL_TOOLS, system_prompt=_load_prompt("system.md"))
    return loop, provider


def run_demo() -> None:
    """Run the copilot against one synthetic administrative task."""
    tracer = Tracer(sinks=[JsonlSink(".trace/care-copilot.jsonl"), ConsoleSink()])
    loop, _ = _build_offline_stack()

    task = SAMPLE_TASKS[0]
    console.print(f"[bold]care-copilot demo[/bold] — task: {task}\n")

    result = loop.run(task, tracer=tracer, offline=True)
    console.print("\n[bold]Result[/bold] (for staff review before anything is submitted or booked):")
    console.print(result)

    egressed = bytes_egressed(tracer.events)
    if egressed == 0:
        console.print("\n[green]0 bytes egressed[/green] — every tool call this run dispatched to its mock.")
    else:
        console.print(f"\n[yellow]{egressed} bytes egressed[/yellow] to a non-device provider this run.")


def run_eval() -> None:
    """Run the copilot against every bundled synthetic task."""
    tracer = Tracer(sinks=[ConsoleSink()])
    loop, _ = _build_offline_stack()
    for i, task in enumerate(SAMPLE_TASKS, start=1):
        console.print(f"[bold]task {i}[/bold]: {task}")
        result = loop.run(task, tracer=tracer, offline=True)
        console.print(result)


def cli() -> None:
    parser = argparse.ArgumentParser(prog="app.main", description="care-copilot admin demo")
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="run against mocks only, no network (default, and the only mode this template ships)",
    )
    parser.add_argument("--eval", action="store_true", help="run every bundled synthetic task")
    args = parser.parse_args()

    if args.eval:
        run_eval()
    else:
        run_demo()


if __name__ == "__main__":
    cli()
