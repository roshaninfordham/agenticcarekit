# Blueprint: care-copilot

**Track:** Agentic Care Copilots — prior auth, referrals, scheduling.

## What it generates

`ack new --blueprint care-copilot` (or `ack init`, choosing this
blueprint) scaffolds a project with:

- `app/main.py` — an `AgentLoop` wired with four tools and a step budget.
- `app/tools/` — `check_eligibility`, `draft_prior_auth`,
  `find_referral_slots`, `schedule_appointment`, each `@tool`-decorated
  with a realistic mandatory mock (synthetic payers, specialists, slots).
- `app/prompts/*.md` — the system prompt, framed as administrative
  decision support.
- `Makefile`, `pyproject.toml`, `README.md` for the generated project.

Requires: text input, tool calling, 65,536-token context — see
`blueprint.toml`.

**Prior auth is drafting only.** `draft_prior_auth` produces paperwork for
a human to review and submit through the organization's real channel —
this blueprint has no submission path, mocked or otherwise.

## Running the demo

Inside a project generated from this blueprint:

```sh
make demo   # python -m app.main --offline — every tool call dispatches to its mock
```

## Scope

Decision support only — not diagnosis, not treatment. Synthetic/public
data only. This blueprint (and every project generated from it) produces
administrative decision support — documentation drafting, navigation,
and scheduling logistics — never a clinical diagnosis or treatment
recommendation. All bundled sample data (payers, specialists, slots) is
synthetic.

## Ejectable

Everything under `templates/` is plain, ejectable Python: this is your
code now. Nothing generated from this blueprint calls back into
agenticcarekit's generator.
