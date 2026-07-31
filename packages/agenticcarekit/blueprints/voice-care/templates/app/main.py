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
import json
from pathlib import Path

from agenticcarekit.capabilities.voice import MockASR, MockTTS, Transcript, VoiceLoop
from agenticcarekit.kernel.contracts import GenerateResponse
from agenticcarekit.kernel.providers import MockProvider
from agenticcarekit.kernel.trace import ConsoleSink, JsonlSink, Tracer, bytes_egressed
from rich.console import Console

from app.fixtures.sample_transcripts import SAMPLE_TRANSCRIPTS, SyntheticTranscript
from app.scribe import transcribe_to_note

console = Console()
PROMPTS_DIR = Path(__file__).parent / "prompts"


#: What the mock scribe model "extracts" from the sample call. A real
#: model produces this JSON itself; the mock replays it so the demo shows
#: the full extract -> validate -> IntakeNote path with zero network.
#: Synthetic data only — no real patient information.
_CANNED_NOTE = {
    "chief_complaint": "Sore throat and a mild fever since Tuesday.",
    "history_of_present_illness": (
        "Symptoms began Tuesday: sore throat with a low-grade fever "
        "(reported ~100.5) that has been similar each evening, plus a "
        "little cough. Swallowing is uncomfortable but manageable."
    ),
    "reported_symptoms": ["sore throat", "low-grade fever", "cough"],
    "triage_route": "routine-visit",
    "follow_up_needed": True,
    "notes_for_clinician": (
        "Temperature figure is patient-reported, not measured in clinic. "
        "Confirm timing of onset before scheduling."
    ),
}


def _scribe_provider() -> MockProvider:
    """A mock model scripted to return the canned intake-note JSON."""
    return MockProvider([GenerateResponse(text=json.dumps(_CANNED_NOTE))])


def _asr_script(transcript: SyntheticTranscript) -> list[list[Transcript]]:
    """Turn a synthetic transcript's patient lines into a MockASR script,
    one scripted ASR turn (with a partial, then the final) per utterance."""
    script: list[list[Transcript]] = []
    for speaker, text in transcript:
        if speaker != "patient":
            continue
        cut = max(1, len(text) // 3)
        script.append(
            [
                Transcript(text[:cut], False, 0, 800),
                Transcript(text, True, 0, 2400),
            ]
        )
    return script


def _build_offline_stack(transcript: SyntheticTranscript) -> tuple[VoiceLoop, MockProvider]:
    """The offline seam: mocks in, real providers out.

    To go live, replace ``MockASR``/``MockTTS`` with a real microphone or
    telephony adapter (same ``capabilities.voice`` interface) and
    ``MockProvider`` with ``OllamaProvider``/``CerebrasProvider`` behind a
    ``FallbackChain`` (``agenticcarekit.kernel.providers``). The rest of
    this function, and everything downstream of it, is unchanged.
    """
    provider = MockProvider()
    asr = MockASR(_asr_script(transcript))
    tts = MockTTS()
    loop = VoiceLoop(
        asr=asr,
        llm=provider,
        tts=tts,
        system_prompt_path=PROMPTS_DIR / "system.md",
    )
    return loop, provider


def run_demo() -> None:
    """Run one synthetic intake call end to end, fully offline."""
    tracer = Tracer(sinks=[JsonlSink(".trace/voice-care.jsonl"), ConsoleSink()])

    sample_id, transcript = next(iter(SAMPLE_TRANSCRIPTS.items()))
    console.print(f"[bold]voice-care demo[/bold] — replaying synthetic sample {sample_id!r}\n")

    loop, provider = _build_offline_stack(transcript)
    patient_turns = sum(1 for speaker, _ in transcript if speaker == "patient")
    for _ in range(patient_turns):
        result = loop.run_turn(iter([b"\x00"]))
        console.print(f"  [dim]caller:[/dim] {result.transcript}")
        console.print(f"  [dim]assistant:[/dim] {result.reply_text}")

    note = transcribe_to_note(transcript, provider=_scribe_provider(), tracer=tracer)

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
    provider = _scribe_provider()
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
