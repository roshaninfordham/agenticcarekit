# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
"""Voice-care intake demo entrypoint.

Run with ``make demo`` (``python -m app.main --offline``, the default):
wires ``MockASR``/``MockTTS`` and a ``MockProvider`` so the whole turn
loop — listen, transcribe, converse, scribe — runs with zero network
calls, against the synthetic transcripts in ``app/fixtures``.

The seam to a real ASR/TTS/model provider is marked clearly below. Swap
it; nothing else in this file needs to change.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agenticcarekit.capabilities.voice import MockASR, MockTTS, VoiceLoop
from agenticcarekit.kernel.providers import MockProvider
from agenticcarekit.kernel.trace import ConsoleSink, JsonlSink, Tracer, bytes_egressed
from rich.console import Console

from app.fixtures.sample_transcripts import SAMPLE_TRANSCRIPTS
from app.scribe import transcribe_to_note

console = Console()
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_offline_stack() -> tuple[VoiceLoop, MockProvider]:
    """The offline seam: mocks in, real providers out.

    To go live, replace ``MockASR``/``MockTTS`` with a real microphone or
    telephony adapter (same ``capabilities.voice`` interface) and
    ``MockProvider`` with ``OllamaProvider``/``CerebrasProvider`` behind a
    ``FallbackChain`` (``agenticcarekit.kernel.providers``). The rest of
    this function, and everything downstream of it, is unchanged.
    """
    provider = MockProvider()
    asr = MockASR()
    tts = MockTTS()
    loop = VoiceLoop(asr=asr, tts=tts, provider=provider)
    return loop, provider


def run_demo() -> None:
    """Run one synthetic intake call end to end, fully offline."""
    tracer = Tracer(sinks=[JsonlSink(".trace/voice-care.jsonl"), ConsoleSink()])
    loop, provider = _build_offline_stack()
    system_prompt = _load_prompt("system.md")

    sample_id, transcript = next(iter(SAMPLE_TRANSCRIPTS.items()))
    console.print(f"[bold]voice-care demo[/bold] — replaying synthetic sample {sample_id!r}\n")

    turn_transcript = loop.run(system_prompt=system_prompt, seed_transcript=transcript)
    note = transcribe_to_note(turn_transcript, provider=provider, tracer=tracer)

    console.print("\n[bold]Structured intake note[/bold] (for clinician review):")
    console.print(note.model_dump())

    egressed = bytes_egressed(tracer.events)
    if egressed == 0:
        console.print("\n[green]0 bytes egressed[/green] — this run stayed on-device/mocked throughout.")
    else:
        console.print(f"\n[yellow]{egressed} bytes egressed[/yellow] to a non-device provider this run.")


def run_eval() -> None:
    """Score the scribe against every bundled synthetic fixture."""
    tracer = Tracer(sinks=[ConsoleSink()])
    _, provider = _build_offline_stack()
    for name, transcript in SAMPLE_TRANSCRIPTS.items():
        note = transcribe_to_note(transcript, provider=provider, tracer=tracer)
        console.print(f"[bold]{name}[/bold]  triage_route={note.triage_route!r}")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="app.main", description="voice-care intake demo")
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="run against mocks only, no network (default, and the only mode this template ships)",
    )
    parser.add_argument("--eval", action="store_true", help="score the scribe against the fixture set")
    args = parser.parse_args()

    if args.eval:
        run_eval()
    else:
        run_demo()


if __name__ == "__main__":
    cli()
