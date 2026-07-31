#!/usr/bin/env python3
"""Conformance adapter for the canonical Python implementation.

Speaks the JSON-lines adapter protocol (``spec/conformance/README.md``) and
maps each area onto the real code — never onto a reimplementation of it.
An adapter that reproduces the logic it is meant to verify proves nothing:

    message-build           -> kernel.providers.builder.build_ollama_chat
    capability-negotiation  -> contracts.Capabilities.missing / providers.models.ensure_supported
    policy                  -> kernel.policy.Policy
    trace-shape             -> contracts.TraceEvent + spec/schemas/trace-event.schema.json
    config                  -> contracts.AckConfig

Areas whose implementation has not landed yet are reported as unsupported by
``--describe``; the harness then counts their cases as skipped rather than
failing them. Nothing here fakes a result to make a suite go green.

Usage::

    python spec/conformance/adapters/python.py --describe
    python spec/conformance/adapters/python.py < cases.jsonl > results.jsonl
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

SPEC_ROOT = Path(__file__).resolve().parents[2]

# ── optional imports ─────────────────────────────────────────────────────
# Each area is independent: the adapter serves whatever has landed. Bare
# `except ImportError` would also hide a typo, so the failure is recorded and
# reported by --describe instead of being swallowed.

_UNAVAILABLE: dict[str, str] = {}

try:
    from agenticcarekit.kernel.contracts import (
        AckConfig,
        AckError,
        AudioPart,
        Capabilities,
        EgressClass,
        GenerateRequest,
        ImagePart,
        Message,
        Modality,
        Redaction,
        Sensitive,
        TextPart,
        ToolCall,
        ToolSpec,
        TraceEvent,
    )
except ImportError as exc:  # pragma: no cover - environment problem, not a case failure
    print(json.dumps({"fatal": f"agenticcarekit.kernel.contracts is not importable: {exc}"}))
    raise SystemExit(2) from None

try:
    from agenticcarekit.kernel.providers.builder import build_ollama_chat
except ImportError as exc:
    _UNAVAILABLE["message-build"] = f"kernel.providers.builder unavailable: {exc}"

try:
    from agenticcarekit.kernel.providers.models import ensure_supported
except ImportError as exc:
    _UNAVAILABLE["capability-negotiation"] = f"kernel.providers.models unavailable: {exc}"

try:
    from agenticcarekit.kernel.policy import Policy
except ImportError as exc:
    _UNAVAILABLE["policy"] = f"kernel.policy unavailable: {exc}"

try:
    from agenticcarekit.kernel.trace.analysis import bytes_egressed
except ImportError as exc:
    _UNAVAILABLE["trace-shape"] = f"kernel.trace.analysis unavailable: {exc}"

try:
    import jsonschema
except ImportError as exc:
    _UNAVAILABLE["trace-shape"] = f"jsonschema unavailable: {exc}"

AREAS = ("message-build", "capability-negotiation", "policy", "trace-shape", "config")


# ── request decoding (the documented JSON encoding of GenerateRequest) ────


def _media(part: dict[str, Any]) -> bytes | str:
    """``data_b64`` passes through as a string; ``data_utf8`` becomes raw bytes.

    The distinction is the point of rule 6: a base64 string must not be
    re-encoded, raw bytes must be.
    """
    if "data_b64" in part:
        return str(part["data_b64"])
    return str(part["data_utf8"]).encode("utf-8")


def _part(part: dict[str, Any]) -> Any:
    kind = part["type"]
    if kind == "text":
        return TextPart(part["text"])
    if kind == "image":
        return ImagePart(_media(part), detail=part.get("detail", "default"))
    if kind == "audio":
        return AudioPart(_media(part), format=part.get("format", "wav"))
    raise ValueError(f"unknown part type {kind!r}")


def _message(msg: dict[str, Any]) -> Message:
    return Message(
        role=msg["role"],
        parts=tuple(_part(p) for p in msg.get("parts", ())),
        thinking=msg.get("thinking"),
        tool_calls=tuple(
            ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {}))
            for tc in msg.get("tool_calls", ())
        ),
        tool_call_id=msg.get("tool_call_id"),
    )


def _tool_spec(decl: dict[str, Any]) -> ToolSpec:
    """A ToolSpec built straight from the fixture declaration.

    Fixtures carry ``{name, description, parameters}`` — the language-neutral
    core of a tool. The wrapping into ``{"type": "function", ...}`` is what
    ``as_function_schema()`` owns and what the fixtures assert.
    """
    return ToolSpec(
        name=decl["name"],
        description=decl.get("description", ""),
        json_schema=decl.get("parameters", {"type": "object", "properties": {}}),
        permissions=frozenset(decl.get("permissions", ())),
        fn=_unused_tool_impl,
        mock=_unused_tool_impl,
    )


def _unused_tool_impl(*_args: Any, **_kw: Any) -> None:
    """Never called: conformance asserts declarations, not tool execution."""
    raise AssertionError("conformance tools are declarations only")


def _request(spec: dict[str, Any], model: str | None = None) -> GenerateRequest:
    return GenerateRequest(
        messages=tuple(_message(m) for m in spec.get("messages", ())),
        model=model,
        tools=tuple(_tool_spec(t) for t in spec.get("tools", ())),
        think=bool(spec.get("think", False)),
        temperature=spec.get("temperature"),
        top_p=spec.get("top_p"),
        top_k=spec.get("top_k"),
        max_tokens=spec.get("max_tokens"),
        stop=tuple(spec.get("stop", ())),
    )


def _capabilities(spec: dict[str, Any]) -> Capabilities:
    return Capabilities(
        modalities_in=frozenset(Modality(m) for m in spec["modalities_in"]),
        modalities_out=frozenset(Modality(m) for m in spec["modalities_out"]),
        tool_calling=bool(spec["tool_calling"]),
        streaming=bool(spec["streaming"]),
        context_tokens=int(spec["context_tokens"]),
        thinking=bool(spec["thinking"]),
        egress=EgressClass(spec["egress"]),
    )


# ── fixture redactors (defined by the spec, not by any pack) ──────────────

_DIGIT_RUN = re.compile(r"\d+")


class PassthroughRedactor:
    """Declared, runs, replaces nothing. Proves that the boundary condition is
    "a redactor was declared and ran", not "the text changed"."""

    name = "passthrough"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        return text, []


class MaskDigitsRedactor:
    """Every ASCII digit becomes ``#``; one Redaction per maximal digit run.

    Deterministic in any language, and enough of a transform that a redacted
    payload is unmistakable in a fixture.
    """

    name = "mask-digits"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        spans = [
            Redaction(
                category="DIGITS",
                start=m.start(),
                end=m.end(),
                replacement="#" * (m.end() - m.start()),
            )
            for m in _DIGIT_RUN.finditer(text)
        ]
        return _DIGIT_RUN.sub(lambda m: "#" * len(m.group()), text), spans


_REDACTORS = {"passthrough": PassthroughRedactor, "mask-digits": MaskDigitsRedactor}


class _StubProvider:
    """The smallest thing satisfying the Provider protocol.

    Policy only ever reads the egress class, so the generation methods exist
    to satisfy the protocol and nothing else.
    """

    def __init__(self, name: str, egress: EgressClass) -> None:
        self.name = name
        self.egress = egress
        self._caps = Capabilities(
            modalities_in=frozenset({Modality.TEXT}),
            modalities_out=frozenset({Modality.TEXT}),
            tool_calling=True,
            streaming=True,
            context_tokens=131_072,
            thinking=True,
            egress=egress,
        )

    def capabilities(self) -> Capabilities:
        return self._caps

    def generate(self, req: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("conformance never generates")

    def stream(self, req: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("conformance never streams")


# ── area handlers ────────────────────────────────────────────────────────


def run_message_build(case_input: dict[str, Any]) -> dict[str, Any]:
    req = _request(case_input.get("request", {}), model=case_input["model"])
    return build_ollama_chat(req, case_input["model"])


def run_capability_negotiation(case_input: dict[str, Any]) -> dict[str, Any]:
    caps = _capabilities(case_input["capabilities"])
    if "request" in case_input:
        model = case_input.get("model", "unknown:model")
        ensure_supported(model, caps, _request(case_input["request"], model=model))
        return {"ok": True}
    reqs = case_input.get("requirements", {})
    return {
        "missing": caps.missing(
            modalities_in=frozenset(Modality(m) for m in reqs.get("modalities_in", ())),
            modalities_out=frozenset(Modality(m) for m in reqs.get("modalities_out", ())),
            tool_calling=bool(reqs.get("tool_calling", False)),
            streaming=bool(reqs.get("streaming", False)),
            context_tokens=int(reqs.get("context_tokens", 0)),
            thinking=bool(reqs.get("thinking", False)),
        )
    }


def _build_policy(spec: dict[str, Any]) -> Any:
    """Construct the policy engine from a fixture's ``policy`` block.

    ``[policy] redactor`` names one redactor out of those a project has
    installed; the fixtures install exactly the one they name, from the two
    spec-defined fixture redactors. No emitter is attached — the harness
    asserts decisions, and W-C owns trace plumbing.
    """
    name = spec.get("redactor")
    return Policy(
        EgressClass(spec.get("egress", "device")),
        redactors={name: _REDACTORS[name]()} if name else {},
        default_redactor=name,
    )


def run_policy(case_input: dict[str, Any]) -> dict[str, Any]:
    policy = _build_policy(case_input.get("policy", {}))
    pspec = case_input["provider"]
    provider = _StubProvider(pspec["name"], EgressClass(pspec["egress"]))
    value = case_input["value"]
    text = value["text"]

    if not value.get("sensitive", True):
        # The E303 ceiling applies to non-sensitive traffic too, so it cannot
        # travel through unwrap(): `check_provider` is the pinned entry point
        # for the value-free half of the boundary.
        check = getattr(policy, "check_provider", None)
        if not callable(check):
            raise AdapterGap("kernel.policy.Policy exposes no check_provider() egress pre-check")
        check(provider)
        return {"text": text}

    revealed = Sensitive(text, label=value.get("label", "sensitive")).unwrap_for(provider, policy)
    return {"text": revealed}


def run_trace_shape(case_input: dict[str, Any]) -> dict[str, Any]:
    if "events" in case_input:
        events = [TraceEvent.from_dict(e) for e in case_input["events"]]
        return {"bytes_egressed": bytes_egressed(events)}

    schema = json.loads(
        (SPEC_ROOT / "schemas" / "trace-event.schema.json").read_text(encoding="utf-8")
    )
    event = case_input["event"]
    try:
        jsonschema.validate(event, schema)
    except jsonschema.ValidationError:
        return {"valid": False}
    # Schema-valid events must also survive the dataclass round trip: a schema
    # that has drifted from the contract is exactly what this suite exists to
    # catch.
    if TraceEvent.from_dict(event).to_dict() != event:
        return {"valid": False}
    return {"valid": True}


def _normalized_config(cfg: AckConfig) -> dict[str, Any]:
    fallback = cfg.model_fallback
    return {
        "blueprint": cfg.blueprint,
        "pack": cfg.pack,
        "model_primary": {
            "provider": cfg.model_primary.provider,
            "model": cfg.model_primary.model,
        },
        "model_fallback": (
            None if fallback is None else {"provider": fallback.provider, "model": fallback.model}
        ),
        "egress": cfg.egress.value,
        "redactor": cfg.redactor,
        "capabilities": list(cfg.capabilities),
        "raw_keys": sorted(cfg.raw),
    }


def _load_toml(text: str) -> AckConfig:
    """Parse through the real entry point (``AckConfig.load``) so the E401/E402
    paths under test are the ones users hit."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ack.toml"
        path.write_text(text, encoding="utf-8")
        return AckConfig.load(path)


def run_config(case_input: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_toml(case_input["toml"])
    if case_input.get("mode") != "fixpoint":
        return _normalized_config(cfg)

    once = cfg.to_toml()
    round_tripped = _load_toml(once)
    twice = round_tripped.to_toml()
    again = _load_toml(twice)
    fixpoint = once == twice and _normalized_config(round_tripped) == _normalized_config(again)
    return {"fixpoint": fixpoint, "normalized": _normalized_config(round_tripped)}


HANDLERS = {
    "message-build": run_message_build,
    "capability-negotiation": run_capability_negotiation,
    "policy": run_policy,
    "trace-shape": run_trace_shape,
    "config": run_config,
}


class AdapterGap(RuntimeError):
    """The implementation is missing a surface the suite needs to exercise.

    Reported as an ``EADAPTER`` error so the case fails with the missing name
    in the diff — never as a skip, which would hide the gap.
    """


# ── protocol loop ────────────────────────────────────────────────────────


def handle(case: dict[str, Any]) -> dict[str, Any]:
    """One case in, one result out."""
    case_id = case.get("id", "<unknown>")
    area = case.get("area")
    if area in _UNAVAILABLE:
        return {"id": case_id, "unsupported": _UNAVAILABLE[area]}
    handler = HANDLERS.get(str(area))
    if handler is None:
        return {"id": case_id, "unsupported": f"unknown area {area!r}"}
    try:
        return {"id": case_id, "output": handler(case["input"])}
    except AckError as exc:
        return {"id": case_id, "error": {"code": exc.code, "message": exc.message}}
    except AdapterGap as exc:
        return {"id": case_id, "error": {"code": "EADAPTER", "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001 - an unexpected raise is a real result
        return {
            "id": case_id,
            "error": {
                "code": "EUNCAUGHT",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=6),
            },
        }


def main(argv: list[str]) -> int:
    if "--describe" in argv:
        print(
            json.dumps(
                {
                    "name": "python",
                    "language": "python",
                    "areas": [a for a in AREAS if a not in _UNAVAILABLE],
                    "unavailable": _UNAVAILABLE,
                },
                sort_keys=True,
            )
        )
        return 0
    for line in sys.stdin:
        if not line.strip():
            continue
        print(json.dumps(handle(json.loads(line)), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
