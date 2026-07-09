# Holdings Reconciliation Agent

This document is the human-readable companion to the Claude Code agent
defined at
[`.claude/agents/holdings-reconciliation.md`](../../../.claude/agents/holdings-reconciliation.md).
The original design rationale is preserved in [`plan.md`](plan.md).

## Purpose

The agent generates a holdings-reconciliation markdown report with one
supported request: reconcile computed holdings against a broker CSV export.
It must stay narrow, deterministic, and read-only, and it runs the scripted
workflow only -- it never edits reconciliation logic or the database.

## Runtime Settings

- `model: haiku` -- a fast, low-cost model is sufficient because the agent's
  only job is to invoke one fixed script and report the resulting path.
- `tools: ["Bash"]` -- least privilege. The agent needs only to run the
  single report-generation command; it has no Read/Write/Edit access.

## Skill Dependencies

The agent's front matter declares only its direct dependency:

```yaml
skills:
  - reconcile-holdings-report
```

| Skill | Role | Used by |
|---|---|---|
| `reconcile-holdings-report` | Public workflow; renders the deterministic markdown report | `holdings-reconciliation` agent |

Unlike `portfolio-classifier`, there is no separate read-only-data-access or
enrichment skill: `reconcile-holdings-report`'s script calls already-tested
`src/` functions (`holdings_reconciler.reconcile_holdings`,
`analytics.get_holdings`, `analytics.get_excluded_positions`) directly rather
than performing its own database access, so there is nothing further to
isolate into a sibling skill.

## Workflow

1. The `holdings-reconciliation` agent is the public entrypoint.
2. It calls the `reconcile-holdings-report` skill workflow, invoking only
   `python .claude/skills/reconcile-holdings-report/scripts/generate_reconciliation_report.py --report <csv>`.
3. That script calls `src/holdings_reconciler.py::reconcile_holdings()` for
   the per-ticker mismatch comparison, and `src/analytics.py`'s
   `get_holdings()`/`get_excluded_positions()` for `data_quality_flags`
   (sourced from `src/position_engine.py`'s `position_snapshots` output).
4. It maps flags to root-cause tags via a fixed dictionary and renders
   markdown deterministically, section by section (see
   `references/report-contract.md`).
5. It writes the report to `exports/holdings-reconciliation/holdings_reconciliation_<date>.md` (or
   `--output`) and prints the path.

## Guardrails

- Do not accept arbitrary SQL, unrestricted database paths, or ad hoc Python
  execution.
- Do not edit `src/position_engine.py`, `src/analytics.py`, or
  `src/holdings_reconciler.py` reconciliation logic from this agent.
- Do not fabricate root-cause narrative beyond the flag-to-tag mapping in
  `references/report-contract.md` -- an unexplained mismatch is tagged
  `RC-UNKNOWN`, not guessed.
- Do not persist anything to the database; this workflow is fully read-only
  and only ever produces a markdown file.

## Code Location

`.claude/skills/reconcile-holdings-report/scripts/generate_reconciliation_report.py`
owns all report-rendering logic (the flag-to-root-cause mapping, section
templates, markdown assembly) -- it lives beside the skill that owns it, not
in `src/`, per `CLAUDE.md`'s convention that skill-exclusive code stays out
of the general pipeline. It imports `src/config.py::DATABASE_PATH`,
`src/analytics.py`, and `src/holdings_reconciler.py` directly (adding `src/`
to `sys.path` itself, matching the pattern used by
`.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py`)
since those are shared, pipeline-wide modules, not skill-exclusive logic.

No new `src/app.py` subcommand was added for this workflow: unlike
`reconcile-holdings` (a general regression-testing CLI command already
documented in `docs/reference/cli.md`), the rich markdown report is a
reporting concern specific to this agent/skill, so it stays entirely inside
the skill's own `scripts/` directory.
