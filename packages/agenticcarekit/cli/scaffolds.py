"""``ack new`` — scaffolds for the five extension points (brief §10).

| Point | Interface | Command |
|---|---|---|
| Provider | ``Provider`` protocol | ``ack new provider <name>`` |
| Redactor | ``Redactor`` protocol | ``ack new redactor <name>`` |
| Pack | pack manifest + schemas | ``ack new pack <name>`` |
| Capability | capability manifest | ``ack new capability <name>`` |
| Blueprint | template dir + ``blueprint.toml`` | ``ack new blueprint <name>`` |

Every skeleton is a **working minimal example** with the right protocol
imports — not a stub with ``pass`` in it. An extension point whose
scaffold does not run is a folder, not an interface.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agenticcarekit.kernel.contracts import AckError

__all__ = ["KINDS", "scaffold"]

KINDS = ("blueprint", "capability", "pack", "provider", "redactor")

_SLUG = re.compile(r"[^a-z0-9_-]+")


def _slug(name: str) -> str:
    """Normalise a name to a safe slug.

    Example:
        >>> _slug("My Cool Pack!")
        'my-cool-pack'
    """
    return _SLUG.sub("-", name.strip().lower()).strip("-") or "unnamed"


def _ident(name: str) -> str:
    """Python identifier form of a name.

    Example:
        >>> _ident("my-cool-pack")
        'my_cool_pack'
    """
    return _slug(name).replace("-", "_")


def _write(root: Path, files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for rel in sorted(files):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[rel], encoding="utf-8")
        written.append(rel)
    return written


# ── provider ─────────────────────────────────────────────────────────────


def _provider(name: str) -> dict[str, str]:
    slug, ident = _slug(name), _ident(name)
    return {
        f"providers/{ident}_provider.py": f'''"""A minimal ``Provider`` for {slug}.

Nothing here hides the provider: ``self.client`` is the raw client and is
part of the public surface by convention (Contract 1). Delete this file and
call the client directly and everything still works — that is the point.
"""

from __future__ import annotations

from collections.abc import Iterator

from agenticcarekit.kernel.contracts import (
    Capabilities,
    Chunk,
    EgressClass,
    GenerateRequest,
    GenerateResponse,
    Modality,
    Usage,
)


class {ident.title().replace("_", "")}Provider:
    """Declares what it can do; the runtime negotiates (invariant 2).

    Example:
        >>> p = {ident.title().replace("_", "")}Provider()
        >>> p.capabilities().egress.value
        'device'
        >>> p.generate(GenerateRequest(messages=())).text
        'hello from {slug}'
    """

    name = "{slug}"

    def __init__(self, client: object | None = None) -> None:
        #: The raw client. Never wrapped, never hidden.
        self.client = client

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities_in=frozenset({{Modality.TEXT}}),
            modalities_out=frozenset({{Modality.TEXT}}),
            tool_calling=False,
            streaming=True,
            context_tokens=8192,
            thinking=False,
            egress=EgressClass.DEVICE,
        )

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            text="hello from {slug}",
            usage=Usage(input_tokens=0, output_tokens=4),
            model=req.model or "{slug}",
            raw={{"provider": "{slug}"}},
        )

    def stream(self, req: GenerateRequest) -> Iterator[Chunk]:
        response = self.generate(req)
        yield Chunk(delta=response.text)
        yield Chunk(done=True, response=response)
''',
    }


# ── redactor ─────────────────────────────────────────────────────────────


def _redactor(name: str) -> dict[str, str]:
    slug, ident = _slug(name), _ident(name)
    return {
        f"redactors/{ident}_redactor.py": f'''"""A minimal ``Redactor`` for {slug}.

Redactors are what let a ``Sensitive`` value cross a public-cloud boundary
at all (Contract 2). Publish precision and recall against a labelled set
before you trust one — never claim perfection.
"""

from __future__ import annotations

import re

from agenticcarekit.kernel.contracts import Redaction

_PATTERN = re.compile(r"\\b\\d{{3}}-\\d{{2}}-\\d{{4}}\\b")


class {ident.title().replace("_", "")}Redactor:
    """Replaces one obvious identifier shape. Extend the pattern table.

    Example:
        >>> clean, found = {ident.title().replace("_", "")}Redactor().redact("ssn 123-45-6789")
        >>> clean
        'ssn [REDACTED:ssn]'
        >>> found[0].category
        'ssn'
    """

    name = "{slug}"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        found: list[Redaction] = []

        def repl(match: re.Match[str]) -> str:
            found.append(
                Redaction(
                    category="ssn",
                    start=match.start(),
                    end=match.end(),
                    replacement="[REDACTED:ssn]",
                )
            )
            return "[REDACTED:ssn]"

        return _PATTERN.sub(repl, text), found
''',
    }


# ── pack ─────────────────────────────────────────────────────────────────


def _pack(name: str) -> dict[str, str]:
    slug, ident = _slug(name), _ident(name)
    return {
        f"packs/{ident}/pack.toml": f'''[pack]
name = "{slug}"
description = "Domain pack: models, redactors, synthetic data and eval sets."
version = "0.1.0"

[provides]
redactors = ["{slug}.example"]
models = []
eval_sets = []
''',
        f"packs/{ident}/__init__.py": f'''"""The {slug} pack.

Domain is a pack, not the architecture (invariant 8). Everything specific
to {slug} lives here and nowhere else; drop the pack and the kernel is
untouched.

Register in ``pyproject.toml`` so installation is discovery::

    [project.entry-points."agenticcarekit.packs"]
    {slug} = "packs.{ident}"
"""

from __future__ import annotations

from .redactors import ExampleRedactor

__all__ = ["ExampleRedactor", "PACK_NAME"]

PACK_NAME = "{slug}"
''',
        f"packs/{ident}/redactors.py": f'''"""Redactors provided by the {slug} pack."""

from __future__ import annotations

import re

from agenticcarekit.kernel.contracts import Redaction

_EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.]+")


class ExampleRedactor:
    """Redacts email addresses.

    Example:
        >>> ExampleRedactor().redact("write to a@b.com")[0]
        'write to [REDACTED:email]'
    """

    name = "{slug}.example"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        found: list[Redaction] = []

        def repl(match: re.Match[str]) -> str:
            found.append(
                Redaction(
                    category="email",
                    start=match.start(),
                    end=match.end(),
                    replacement="[REDACTED:email]",
                )
            )
            return "[REDACTED:email]"

        return _EMAIL.sub(repl, text), found
''',
        f"packs/{ident}/README.md": f"""# {slug} pack

Decision support only — documentation, navigation, accessibility, triage
routing, education. **Not diagnosis. Not treatment.** Synthetic or public
data only.

| Contents | Where |
|---|---|
| redactors | `redactors.py` |
| manifest | `pack.toml` |

Score every redactor against a labelled set and publish precision and
recall in this file. Do not claim perfection.
""",
    }


# ── capability ───────────────────────────────────────────────────────────


def _capability(name: str) -> dict[str, str]:
    slug, ident = _slug(name), _ident(name)
    return {
        f"capabilities/{ident}/capability.toml": f'''[capability]
name = "{slug}"
description = "What this capability adds to a generated project."
version = "0.1.0"

[requires]
modalities_in = ["text"]
tool_calling = false
context_tokens = 8192
''',
        f"capabilities/{ident}/__init__.py": f'''"""The {slug} capability.

Capabilities compose over the kernel and are ejectable: drop this package
and call the kernel directly (invariant 3).
"""

from __future__ import annotations

from agenticcarekit.kernel.contracts import GenerateRequest, Message, Provider

__all__ = ["run"]


def run(provider: Provider, text: str) -> str:
    """One turn through the capability.

    Example:
        >>> class P:
        ...     name = "mock"
        ...     def capabilities(self): ...
        ...     def generate(self, req):
        ...         from agenticcarekit.kernel.contracts import GenerateResponse
        ...         return GenerateResponse(text=req.messages[0].parts[0].text.upper())
        ...     def stream(self, req): ...
        >>> run(P(), "hi")
        'HI'
    """
    req = GenerateRequest(messages=(Message.text("user", text),))
    return provider.generate(req).text
''',
    }


# ── blueprint ────────────────────────────────────────────────────────────

_BP_APP = '''"""__NAME__ — generated by agenticcarekit {{ack_version}}.

Decision support only: documentation, navigation, accessibility, triage
routing, education. NOT diagnosis. NOT treatment. Synthetic or public data
only.
"""

from __future__ import annotations

PROJECT = "{{project_name}}"
BLUEPRINT = "{{blueprint}}"
PACK = "{{pack}}"
MODEL_PRIMARY = "{{model_primary}}"
CAPABILITIES = {{capabilities_list}}


def main() -> None:
    print(f"{PROJECT}: {BLUEPRINT} on {MODEL_PRIMARY} (egress {{egress}})")


if __name__ == "__main__":
    main()
'''

_BP_README = """# {{project_name}}

Generated from the `__NAME__` blueprint by agenticcarekit {{ack_version}}.

- model: `{{model_primary}}`
- pack: `{{pack}}`
- egress: `{{egress}}`
- capabilities: {{capabilities_list}}

**Decision support only.** Documentation, navigation, accessibility, triage
routing, education. Not diagnosis, not treatment. Synthetic or public data
only.

    ack demo --offline
    ack check
"""


def _blueprint(name: str) -> dict[str, str]:
    slug = _slug(name)
    return {
        f"blueprints/{slug}/blueprint.toml": f'''[blueprint]
name = "{slug}"
description = "Describe what this blueprint generates."
track = "custom"

[requires]
modalities_in = ["text"]
tool_calling = true
context_tokens = 32768

[defaults]
capabilities = []
pack = "healthcare"
''',
        f"blueprints/{slug}/README.md": f"""# {slug} blueprint

**Decision support only** — documentation, navigation, accessibility,
triage routing, education. Not diagnosis, not treatment. Synthetic or
public data only.

`templates/` is the generated tree. Files ending `.tmpl` are rendered by
`{{{{var}}}}` substitution and the suffix is stripped; everything else is
copied verbatim. The renderer substitutes exactly: `project_name`,
`blueprint`, `pack`, `model_primary`, `model_fallback`, `egress`,
`redactor`, `capabilities_list`, `ack_version`. An unknown variable is an
E501 error, not silence.

    ack init --blueprint {slug} --yes
""",
        f"blueprints/{slug}/templates/README.md.tmpl": _BP_README.replace("__NAME__", slug),
        f"blueprints/{slug}/templates/app/main.py.tmpl": _BP_APP.replace("__NAME__", slug),
    }


_BUILDERS = {
    "blueprint": _blueprint,
    "capability": _capability,
    "pack": _pack,
    "provider": _provider,
    "redactor": _redactor,
}


def scaffold(kind: str, name: str, root: Path) -> dict[str, Any]:
    """Write the skeleton for ``kind`` named ``name`` under ``root``."""
    if kind not in _BUILDERS:
        raise AckError(
            f"unknown extension point '{kind}'",
            code="E401",
            why="extension points: " + ", ".join(KINDS),
            fix="ack new provider my-provider",
            details={"kinds": list(KINDS)},
        )
    files = _BUILDERS[kind](name)
    written = _write(root, files)
    return {
        "kind": kind,
        "name": _slug(name),
        "root": str(root),
        "files": written,
        "next": {
            "blueprint": "ack init --blueprint-path ./blueprints --blueprint " + _slug(name),
            "capability": "ack add " + _slug(name),
            "pack": "add the entry point to pyproject.toml, then: ack doctor",
            "provider": "import it and pass it where a Provider is expected",
            "redactor": 'set [policy] redactor in ack.toml, then: ack sync',
        }[kind],
    }
