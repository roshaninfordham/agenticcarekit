# `_template` pack

A near-empty pack that exists for one reason: to prove a domain is a
pack, not the architecture (brief invariant 8). If `agenticcarekit`
shipped only `healthcare`, "pack" would just be a folder someone
redesigns the moment a second domain shows up. This one is that second
domain — deliberately trivial, so the seam it proves is unambiguous.

## How to make a pack

1. **Copy this directory** to `packages/agenticcarekit/packs/<yourname>/`.
2. **Write `manifest.toml`.** Minimal shape:

   ```toml
   [pack]
   name = "<yourname>"          # matches the directory name
   description = "..."
   version = "0.1.0"

   [provides]
   redactors = ["<yourname>.<purpose>"]   # Redactor.name values you export, or []
   models = ["YourModel", ...]            # Pydantic model class names you export, or []
   eval_sets = ["evalsets/your_set.jsonl"]  # paths relative to the manifest, or []
   ```

   All three `[provides]` lists may be empty — the manifest just has to
   be truthful about what the pack module actually exports.

3. **Write your domain models** (`models.py`) as Pydantic models. See
   `agenticcarekit.packs.healthcare.models` for a fully worked example
   (FHIR-lite clinical resources, frozen + strict). This template ships
   exactly one placeholder, `Example`.

4. **Write a redactor, if your domain has sensitive text** (`redactor.py`
   or similar). Implement the `agenticcarekit.kernel.contracts.Redactor`
   protocol: a `name` attribute and `redact(text) -> (clean_text,
   list[Redaction])`. This template's `TemplateRedactor` (`name =
   "_template.none"`) is a passthrough that redacts nothing — replace the
   body, not the shape. See `agenticcarekit.packs.healthcare.phi` for a
   real implementation and the honesty conventions expected of one
   (document what it catches, what it misses, and publish measured
   precision/recall against a labelled set — don't claim perfection).

5. **Export everything from `__init__.py`** — models, redactors, any
   generator or scoring functions. This is the pack's public surface;
   anything not exported here doesn't exist as far as the rest of the
   toolkit is concerned.

6. **Ship eval sets, if relevant** (`evalsets/*.jsonl`, schema `{"id",
   "input", "expected", "tags"}`) and a scoring function if there's
   something domain-specific to score (e.g.
   `healthcare.scoring.score_phi_redactor`).

7. **Write tests** in `tests/test_packs_<yourname>.py` — never inside the
   pack directory itself (this repo's convention: tests live in the
   top-level `tests/` folder, flat, named per workstream/area).

## How discovery works

Packs are discovered via the `agenticcarekit.packs` entry-point group in
`pyproject.toml`:

```toml
[project.entry-points."agenticcarekit.packs"]
healthcare = "agenticcarekit.packs.healthcare"
```

Installing a package that declares this entry point makes the pack
discoverable without any central registration file — `ack` (or any
runtime code) enumerates `importlib.metadata.entry_points(group=
"agenticcarekit.packs")` and imports whatever each entry resolves to.
A third-party pack ships the same way: its own `pyproject.toml` declares
the entry point under its own distribution name, and `manifest.toml`
inside the pack directory is read *after* import, as descriptive
metadata — not as the mechanism that makes the pack findable.

## What "near-empty" proves

- A pack with **zero domain models**, **a no-op redactor**, and
  **zero eval sets** still satisfies every extension point the
  architecture defines. Nothing about the kernel, the policy engine, or
  the CLI special-cases `healthcare`.
- `ack eject pack` (or dropping the pack entirely) must leave the rest of
  a generated project working — packs are ejectable, not load-bearing.
