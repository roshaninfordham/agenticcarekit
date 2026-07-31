"""W-G · the recommendation engine against the machine-profile corpus.

Brief §7.4: fixture-driven, with an asserted recommendation **and** asserted
reason strings. "Reasons are part of the contract — a correct
recommendation with a wrong explanation is a failed test."

The corpus lives in ``tests/fixtures_cli/machines/`` as recorded
``MachineFacts`` JSON — the exact shape ``ack doctor --json`` emits, so a
new profile is a copy-paste from a real machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agenticcarekit.cli.blueprints import load_blueprint
from agenticcarekit.cli.detect.probes import facts_from_file
from agenticcarekit.cli.recommend import (
    CATALOG,
    HARD_FILTERS,
    SOFT_SCORES,
    Requirements,
    audio_capable_tags,
    explain_ranking,
    rank,
    recommend,
    validate_choice,
)
from agenticcarekit.cli.recommend.rules import RuleContext
from agenticcarekit.kernel.contracts import AckError, CapabilityMismatch

FIXTURES = Path(__file__).parent / "fixtures_cli"
MACHINES = FIXTURES / "machines"
BLUEPRINTS = FIXTURES / "blueprints"


def _spec(name: str):
    return load_blueprint(BLUEPRINTS / name)


def _recommend(machine: str, blueprint: str):
    spec = _spec(blueprint)
    return recommend(
        facts_from_file(MACHINES / f"{machine}.json"),
        spec.requires,
        pack="healthcare",
        capabilities=list(spec.default_capabilities),
        default_redactor="healthcare.phi",
    )


# ── the corpus ───────────────────────────────────────────────────────────
#
# (machine, blueprint, winning model, one reason asserted verbatim)

CORPUS: list[tuple[str, str, str, str]] = [
    # 16 GB Intel Mac — mlx is eliminated on x86_64, e4b fits 9.6 GB of 16.
    (
        "mac-intel-16gb", "test-voice", "gemma4:e4b",
        "e4b: native audio input, ~4.5B effective parameters",
    ),
    (
        "mac-intel-16gb", "test-notes", "gemma4:12b",
        "12b: 256K context in 7.6 GB — the best size-to-quality trade in the family",
    ),
    # 96 GB M4 Max — everything fits, so platform fit and quality decide.
    (
        "mac-m4-max-96gb", "test-voice", "gemma4:e4b-mlx",
        "-mlx build: native Apple Silicon acceleration on Apple M4 Max",
    ),
    (
        "mac-m4-max-96gb", "test-notes", "gemma4:31b",
        "31b: the dense flagship — highest quality tier in the family",
    ),
    # 8 GB Windows laptop — nothing local fits; hosted primary.
    (
        "windows-8gb", "test-notes", "gemma-4-31b",
        "hosted primary: 8 GB of RAM cannot hold any local Gemma 4 variant, "
        "so the model runs off-device",
    ),
    # 24 GB RTX 4090 — 31b (20 GB) squeezes under 90% of 24 GB VRAM.
    (
        "linux-rtx4090-24gb", "test-voice", "gemma4:e4b",
        "128K context against the 32K required",
    ),
    (
        "linux-rtx4090-24gb", "test-notes", "gemma4:31b",
        "31b: the dense flagship — highest quality tier in the family",
    ),
    # Headless server, 256 GB and a fat pipe.
    (
        "linux-headless-server", "test-voice", "gemma4:e4b",
        "handles audio, image, text input — headroom over the audio, text required",
    ),
    (
        "linux-headless-server", "test-notes", "gemma4:31b",
        "20 GB fits comfortably in 256 GB of RAM",
    ),
    # e4b-mlx already pulled — zero download cost dominates everything.
    (
        "mac-m3-e4b-pulled", "test-voice", "gemma4:e4b-mlx",
        "already pulled: no download, ready now",
    ),
    # ...but the notes blueprint needs 256K context, which e4b cannot do,
    # and 12b would take 11 minutes to pull: the fallback rule fires.
    (
        "mac-m3-e4b-pulled", "test-notes", "gemma-4-31b",
        "hosted primary: pulling gemma4:12b would take ~11 min on this connection — "
        "serving from cerebras now and pulling it in the background",
    ),
    # No Ollama installed — a penalty and an install line, not an elimination.
    (
        "linux-no-ollama", "test-voice", "gemma4:e4b",
        "ollama is not installed yet — install it first: brew install ollama",
    ),
    (
        "linux-no-ollama", "test-notes", "gemma4:26b",
        "26b: mixture-of-experts with ~3.8B active parameters at 256K context",
    ),
    # 2 Mbps wifi — no hosted model has audio, so the reason states the cost.
    (
        "mac-slow-wifi-2mbps", "test-voice", "gemma4:e2b-mlx",
        "download ETA ~480 min for 7.2 GB at 2 Mbps",
    ),
    (
        "mac-slow-wifi-2mbps", "test-notes", "gemma-4-31b",
        "hosted primary: pulling gemma4:12b would take ~507 min on this connection — "
        "serving from cerebras now and pulling it in the background",
    ),
    # 4 GB free disk — RAM is fine, the pull is not.
    (
        "linux-tiny-disk", "test-notes", "gemma-4-31b",
        "gemma-4-31b: hosted — data leaves the machine, and agenticcarekit prefers on-device",
    ),
    # 8 GB M1 Air.
    (
        "mac-m1-8gb", "test-notes", "gemma-4-31b",
        "hosted primary: 8 GB of RAM cannot hold any local Gemma 4 variant, "
        "so the model runs off-device",
    ),
    # 12 GB VRAM — 26b/31b exceed 90% of VRAM, 12b does not.
    (
        "linux-rtx3060-12gb", "test-voice", "gemma4:e4b",
        "e4b: native audio input, ~4.5B effective parameters",
    ),
    (
        "linux-rtx3060-12gb", "test-notes", "gemma4:12b",
        "7.6 GB fits comfortably in 32 GB of RAM",
    ),
    # WSL dev box.
    (
        "wsl-docker-dev", "test-voice", "gemma4:e4b",
        "9.6 GB fits comfortably in 32 GB of RAM",
    ),
    (
        "wsl-docker-dev", "test-notes", "gemma4:12b",
        "256K context against the 195K required",
    ),
    # 24 GB M2 Air — 26b/31b do not fit 60% of 24 GB.
    (
        "mac-m2-air-24gb", "test-voice", "gemma4:e4b-mlx",
        "-mlx build: native Apple Silicon acceleration on Apple M2",
    ),
    (
        "mac-m2-air-24gb", "test-notes", "gemma4:12b",
        "12b: 256K context in 7.6 GB — the best size-to-quality trade in the family",
    ),
    # Air-gapped: throughput unknown, so ETA contributes nothing at all.
    (
        "linux-airgapped-12b-pulled", "test-voice", "gemma4:e4b",
        "e4b: native audio input, ~4.5B effective parameters",
    ),
    (
        "linux-airgapped-12b-pulled", "test-notes", "gemma4:12b",
        "already pulled: no download, ready now",
    ),
    # 45 Mbps laptop with a CEREBRAS key present.
    (
        "linux-laptop-key-present", "test-voice", "gemma4:e2b",
        "download ETA ~21 min for 7.2 GB at 45 Mbps",
    ),
    (
        "linux-laptop-key-present", "test-notes", "gemma-4-31b",
        "hosted primary: pulling gemma4:12b would take ~23 min on this connection — "
        "serving from cerebras now and pulling it in the background",
    ),
]


@pytest.mark.parametrize(("machine", "blueprint", "model", "reason"), CORPUS)
def test_corpus_recommendation_and_reason(
    machine: str, blueprint: str, model: str, reason: str
) -> None:
    rec = _recommend(machine, blueprint)
    assert rec.model == model, f"{machine}/{blueprint}: got {rec.model}, reasons={rec.reasons}"
    assert reason in rec.reasons, f"{machine}/{blueprint} reasons were:\n  " + "\n  ".join(
        rec.reasons
    )


def test_corpus_is_large_enough() -> None:
    """Brief §7.4 asks for ~30 profiles; this is the floor, not the target."""
    profiles = sorted(MACHINES.glob("*.json"))
    assert len(profiles) >= 12
    assert len(CORPUS) >= 24


@pytest.mark.parametrize("machine", ["mac-m1-8gb", "windows-8gb", "linux-tiny-disk"])
def test_audio_blueprint_impossible_machines_raise_e203(machine: str) -> None:
    """No audio-capable model can be installed and no hosted model has audio.

    The refusal is the product: a silent downgrade to a text-only model
    would be invariant 2 violated.
    """
    with pytest.raises(AckError) as excinfo:
        _recommend(machine, "test-voice")
    err = excinfo.value
    assert err.code == "E203"
    assert err.details["binding_filter"] in ("ram", "disk")
    assert "no model fits the test-voice blueprint" in err.message


def test_binding_constraint_names_the_fixable_resource() -> None:
    with pytest.raises(AckError) as excinfo:
        _recommend("linux-tiny-disk", "test-voice")
    assert excinfo.value.details["binding_filter"] == "disk"
    assert "only 4 GB is available" in excinfo.value.why


# ── rule table ───────────────────────────────────────────────────────────


def test_rule_tables_are_declarative_and_named() -> None:
    """Every rule is an auditable object with a name and a reason template."""
    assert [f.name for f in HARD_FILTERS] == [
        "modalities",
        "context",
        "tool_calling",
        "mlx_platform",
        "ram",
        "vram",
        "disk",
    ]
    assert {s.name for s in SOFT_SCORES} == {
        "already_pulled",
        "quality_tier",
        "platform_fit",
        "download_eta",
        "capability_headroom",
        "context_headroom",
        "stay_on_device",
        "ollama_present",
        "ram_fit",
    }
    for rule in (*HARD_FILTERS, *SOFT_SCORES):
        assert rule.reason_template.strip()


def test_every_reason_template_renders_for_every_catalog_entry() -> None:
    """An unrenderable reason is a broken contract, not a cosmetic bug."""
    facts = facts_from_file(MACHINES / "mac-m4-max-96gb.json")
    reqs = Requirements(blueprint="test-voice", modalities_in=frozenset({"text", "audio"}))
    for entry in CATALOG.values():
        ctx = RuleContext(entry, facts, reqs)
        for rule in (*HARD_FILTERS, *SOFT_SCORES):
            rendered = ctx.render(rule.reason_template)
            assert "{" not in rendered and "}" not in rendered


def test_catalog_matches_brief_section_2() -> None:
    """Ground truth, verbatim. Do not 'fix' these numbers without the brief."""
    assert CATALOG["gemma4:e2b"].size_gb == 7.2
    assert CATALOG["gemma4:e4b"].size_gb == 9.6
    assert CATALOG["gemma4:12b"].size_gb == 7.6
    assert CATALOG["gemma4:26b"].size_gb == 18.0
    assert CATALOG["gemma4:31b"].size_gb == 20.0
    assert CATALOG["gemma4:e4b"].context_tokens == 131_072
    assert CATALOG["gemma4:31b"].context_tokens == 262_144
    assert audio_capable_tags() == [
        "gemma4:e2b",
        "gemma4:e2b-mlx",
        "gemma4:e4b",
        "gemma4:e4b-mlx",
    ]
    assert CATALOG["gemma4:cloud"].size_gb is None
    assert all(e.tool_calling for e in CATALOG.values())


def test_hosted_primary_raises_egress_and_requires_a_redactor() -> None:
    rec = _recommend("windows-8gb", "test-notes")
    assert rec.egress == "public-cloud"
    assert rec.redactor == "healthcare.phi"
    assert "healthcare.phi redactor is required" in rec.provider_reason


def test_local_primary_keeps_egress_on_device() -> None:
    rec = _recommend("mac-m4-max-96gb", "test-voice")
    assert rec.egress == "device"
    assert rec.providers == ["ollama"]
    assert rec.fallback_ref is None
    assert "nothing leaves" in rec.provider_reason


def test_background_pull_is_recommended_with_the_hosted_primary() -> None:
    rec = _recommend("mac-m3-e4b-pulled", "test-notes")
    assert rec.background_pull == "gemma4:12b"
    assert rec.fallback_ref == "ollama:gemma4:12b"
    assert "background" in rec.reasons[0]


def test_explain_ranking_covers_survivors_and_losers() -> None:
    rec = _recommend("mac-m4-max-96gb", "test-voice")
    data = explain_ranking(rec)
    assert data["chosen"] == "ollama:gemma4:e4b-mlx"
    assert data["ranked"][0]["rank"] == 1
    assert data["ranked"][0]["score_breakdown"]
    eliminated = {row["tag"]: row for row in data["eliminated"]}
    assert eliminated["gemma4:31b"]["eliminated_by"] == "modalities"
    assert (
        "gemma4:31b: no audio input — the test-voice blueprint requires it"
        in eliminated["gemma4:31b"]["reasons"]
    )
    assert len(data["ranked"]) + len(data["eliminated"]) == len(CATALOG)


def test_rank_is_stable_and_total() -> None:
    facts = facts_from_file(MACHINES / "linux-headless-server.json")
    reqs = Requirements(blueprint="test-notes", modalities_in=frozenset({"text"}))
    first = rank(facts, reqs)
    second = rank(facts, reqs)
    assert [c.tag for c in first] == [c.tag for c in second]
    assert len(first) == len(CATALOG)


# ── explicit --model ─────────────────────────────────────────────────────


def test_forced_model_that_lacks_audio_raises_e203_with_candidates() -> None:
    facts = facts_from_file(MACHINES / "mac-m4-max-96gb.json")
    reqs = _spec("test-voice").requires
    with pytest.raises(CapabilityMismatch) as excinfo:
        validate_choice("gemma4:31b", facts, reqs)
    err = excinfo.value
    assert err.code == "E203"
    assert err.candidates == audio_capable_tags()
    assert err.message == "gemma4:31b does not support audio input"


def test_forced_model_that_does_not_fit_ram_warns_rather_than_refusing() -> None:
    """The user named the model; a fit problem is a warning, not a veto.

    (There is no registered error code for "insufficient RAM" — raising an
    unregistered code would be a test failure, so this degrades honestly.)
    """
    facts = facts_from_file(MACHINES / "mac-m1-8gb.json")
    reqs = Requirements(blueprint="test-notes", modalities_in=frozenset({"text"}))
    warnings = validate_choice("gemma4:31b", facts, reqs)
    assert warnings == [
        "gemma4:31b: 20 GB does not fit the 4.8 GB of usable RAM (60% of 8 GB)"
    ]


def test_forced_unknown_model_lists_the_known_tags() -> None:
    facts = facts_from_file(MACHINES / "mac-m4-max-96gb.json")
    with pytest.raises(AckError) as excinfo:
        validate_choice("llama9:1t", facts, Requirements())
    assert excinfo.value.code == "E401"
    assert "gemma4:e4b" in excinfo.value.details["known_tags"]


def test_forced_model_is_reported_as_forced_with_its_reason() -> None:
    facts = facts_from_file(MACHINES / "mac-m4-max-96gb.json")
    rec = recommend(
        facts,
        _spec("test-voice").requires,
        pack="healthcare",
        force_model="gemma4:e2b",
    )
    assert rec.forced is True
    assert rec.model == "gemma4:e2b"
    assert rec.reasons[0] == "chosen explicitly with --model gemma4:e2b"
