# Investment Analyst Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/investment-analyst.md`](../../../.claude/agents/investment-analyst.md).
Design rationale is in [`plan.md`](plan.md).

## Purpose

The sole LLM judgment stage of the rebuilt Investment Analyst track
(`docs/plans/investment-analyst-rebuild-roadmap.md`, Phase 4). Reads one run's
`investment-worksheet.v1` (built deterministically by
`src/workspace/investment_worksheet.py`, Phase 3) and produces one
`investment-thesis.v1` artifact: a fundamental-attractiveness rating,
valuation stance, thesis direction/confidence, 2-4 evidence-cited key claims,
and upgrade/downgrade/invalidation conditions. It is **not** a portfolio
decision — see `docs/architecture/investment_thesis_schema.md` §1/§2 — and it
is not the legacy `stock-analyst` agent (see "What this agent does not do"
below).

## Runtime Settings

- `model: opus` — the one required judgment stage in the rebuilt workflow;
  every earlier stage (resource collection, worksheet building) is
  deterministic Python. Per `docs/plans/combined-investment-analyst-plan/00-overview.md`
  §4, the strongest model is worth it here because it is the only place
  interpretation happens.
- `tools: ["Bash", "Read", "Write"]` — `Bash` drives three new deterministic
  CLI stages (`run build-worksheet`, `run check-thesis`, `run save-thesis`);
  `Read` opens the worksheet JSON and `analyst-context.md`; `Write` is scoped
  by the guardrails to `tmp/<TICKER>-thesis-draft.json` only — the finished
  artifact is written by `save-thesis`, not directly by the agent.
- **No `skills:` field.** Per `00-overview.md` §3.2/§4.1, worksheet-building
  and thesis-validation are deterministic CLI stages, not Claude skills — the
  only skill type the rebuilt runtime permits is a purpose-built `*-resources`
  data-collection skill (`investment-analyst-resources`, which this agent
  never invokes directly — see Handoffs), and this agent needs neither.

## Inputs

- `calculations/<TICKER>-<stamp>-analyst-context.md` — the compact,
  bullet-point narrative context (primary read).
- `calculations/<TICKER>-<stamp>-worksheet.json` — the full
  `investment-worksheet.v1` document, read for the structured blocks that must
  be copied verbatim (`valuation_methods`, `scenario_inputs`, `unknowns`,
  `evidence_health.domain_status`, `identity`, `source_evidence`).

The agent never reads the raw `market_data_bundle` (`investment-analyst-resources`
output) or the `security-technicals` calculation artifact directly — both are
already normalized into the worksheet.

- The subject's resolved status (`owned`/`wishlist`/`avoid`/`retired`/`unknown`)
  from the `.claude/skills/security-status/` skill — invoked once, early in
  the workflow, before `build-worksheet`. Context for framing the thesis
  only; it never influences `fundamental_rating` or `valuation_stance`. This
  is the **same skill, unchanged**, `investment-portfolio-manager` invokes —
  there is deliberately no separate copy for either agent (see that agent's
  own architecture doc).

## Outputs

One `investment-thesis.v1` JSON, written to
`agent_outputs/<TICKER>-<stamp>-thesis.json` by `run save-thesis` (never
directly by the agent), registered as evidence with
`evidence_type="investment_thesis"`, `source_name="investment_analyst"`,
`collection_method="llm_judgment"`.

## The three CLI stages it drives

| Command | Side effects | Purpose |
|---|---|---|
| `run build-worksheet --run-id --ticker --mode --horizon [--asset-track] [--trigger-type] [--trigger-detail] [--trigger-occurred-at]` | Writes `calculations/*-worksheet.json` + `*-analyst-context.md`, registers both as evidence, appends a `worksheet_built` audit event | Wraps `investment_worksheet.build_analysis_scope` + `build_worksheet_for_run` (Phase 3) |
| `run check-thesis --run-id --path <draft>` | None — pure validation | Wraps `thesis_validation.check_thesis_draft`; the iterate-until-valid loop |
| `run save-thesis --run-id --path <draft> --ticker` | Writes `agent_outputs/*-thesis.json` (only on success), registers `investment_thesis` evidence, appends `analyst_drafted` + `validated` audit events | Wraps `thesis_validation.save_thesis`; the one authoritative finalize step |

## The halt-on-blocking contract

`build-worksheet` prints `evidence_health.blocking` — `true` when the run's
`critical_gap_policy` is `stop` (the default for `initial_research` and
`earnings_update`) and a required evidence domain's TRACE completeness falls
below `investment-analysis-policy.yml`'s `trace_policy.blocking_threshold`
(50.0%), per `docs/architecture/analysis_scope_schema.md`'s
`critical_gap_policy`. When `true`, the agent does not draft a thesis at all
— it moves the run to the `insufficient_evidence` status (a new,
Phase-4-added terminal-ish state in `src/workspace/state.py`, distinct from
`failed`) and stops. A human must supply the missing evidence and reopen the
run (`insufficient_evidence -> in_progress`) before a thesis can be produced.

## The confidence-cap contract

`thesis_validation.confidence_cap()` (Phase 1, unchanged this phase) caps
`thesis_confidence` to `low` when any required evidence domain is `missing`
in TRACE, or to `medium` when scoped completeness is below the 80% warning
threshold. `save-thesis` enforces this on every save — a draft claiming a
higher confidence than the cap allows is rejected, not silently downgraded,
so the agent must set a realistic value itself and fix it via the
`check-thesis` loop rather than relying on `save-thesis` to correct it.

## Section-state population rules

Every one of the 16 registered section ids (`investment-analysis-policy.yml`'s
`section_registry`) gets exactly one `section_states` entry, taken from the
worksheet's own `request_and_scope`/`section_requirements` buckets:

| Worksheet bucket | `section_states` value | `sections[id]` present? |
|---|---|---|
| `evaluate_sections` | `changed` | Yes — narrative required |
| `preserve_sections` | `not_evaluated` (never `unchanged`) | No |
| `not_applicable_sections` | `not_applicable` | No |

`preserve_sections` maps to `not_evaluated` rather than `unchanged` because no
prior-thesis loader exists yet (Phase 5/7) — nothing has actually been
compared to justify claiming "unchanged".

## What this agent does not do

- Does not score `Knowledge-Base/taxonomy/decision-rubric.yml` (legacy
  benchmark path only, `00-overview.md` §11.5/§14 — never invoked by the
  rebuilt runtime).
- Does not emit a `decision-framework.yml` action (`Buy`/`Sell`/`Hold`/`Trim`/
  `Add`/`Watchlist`/`Avoid`) or any position-sizing/trade field — reserved for
  the not-yet-built Portfolio Manager (Phase 11); hard-rejected anywhere in
  the artifact tree by `analysis_models.find_forbidden_fields()`.
- Does not run a challenger pass (Phase 8) — `challenger` is always
  `{required: false, completed: false, material_objections: []}` this phase.
- Does not load a prior thesis (Phase 5/7) — `prior_thesis` is always
  `{exists: false}`.
- Does not consume Market Researcher output (Phase 10) — market context stays
  an explicit `unknowns` entry from the worksheet.
- Does not write to `Knowledge-Base/` — KB promotion is a separate,
  human-gated, unbuilt component.
- Does not fetch data — if no resource bundle exists for the ticker, it hands
  off to `investment-analyst-resources` rather than fetching itself.

## Guardrails

See the agent definition's Guardrails section for the full list. The two most
load-bearing: never fabricate an evidence citation (cite only the worksheet's
own `evidence_id` plus its `source_evidence` bundle/technicals ids), and never
hand-tune `policy_version`/`artifact_id`/`validation{}` — `save-thesis`
authoritatively overwrites all three regardless of what the draft contains.

## Handoffs

| Label | Action |
| --- | --- |
| No resource bundle for the ticker | Invoke `investment-analyst-resources` directly for the ticker in this run, then retry. |
| Blocking evidence gap | Stop; a human must supply the missing evidence and reopen the run. |

## Code Location

`src/workspace/thesis_validation.py` (`check_worksheet_ref`,
`scope_from_worksheet_ref`, `check_thesis_draft`, `save_thesis`) and
`src/workspace/cli.py` (`build-worksheet`/`check-thesis`/`save-thesis`
subcommands) hold the deterministic logic; `src/workspace/investment_worksheet.py`
(Phase 3) and `src/workspace/analysis_models.py`/`thesis_validation.py`
(Phase 1) are unchanged dependencies. See
[`docs/architecture/investment_thesis_schema.md`](../../architecture/investment_thesis_schema.md)
and
[`docs/architecture/analysis_scope_schema.md`](../../architecture/analysis_scope_schema.md).
