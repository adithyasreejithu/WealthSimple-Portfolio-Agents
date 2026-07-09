# Holdings Reconciliation Agent -- Design Plan

## Summary

On 2026-07-07, an ad-hoc investigation compared the Wealthsimple broker CSV
export against the pipeline's computed holdings and produced
`docs/holdings_reconciliation_2026-07-07.md`: an executive summary, a
per-ticker mismatch table, root-cause analysis, corrective actions, and a
verification checklist. That investigation directly motivated the rewrite of
`src/position_engine.py` / `src/analytics.py` / `src/database.py` (schema
v10) and the `src/holdings_reconciler.py` regression harness (see
`docs/architecture/ingestion_and_reconciliation.md`). This agent turns that
one-off investigation into a repeatable, deterministic reporting workflow so
the same report format can be regenerated on demand, against any broker CSV
export and the live database, without a human re-deriving it by hand.

## Agent and Skill Structure

```
.claude/
  agents/
    holdings-reconciliation.md
  skills/
    reconcile-holdings-report/
      SKILL.md
      scripts/
        generate_reconciliation_report.py
      references/
        report-contract.md
docs/
  agents/
    holdings-reconciliation/
      plan.md
      architecture.md
```

A single skill is sufficient here (unlike `classify-portfolio`'s three-skill
split): the report generator calls existing, already-tested `src/` functions
(`holdings_reconciler.reconcile_holdings`, `analytics.get_holdings`,
`analytics.get_excluded_positions`) rather than performing its own read-only
database access or external enrichment, so there is no separate "data
access" or "enrichment" skill to isolate.

## Fixed Workflow

1. User (or the `holdings-reconciliation` agent) supplies a broker holdings
   CSV export path.
2. `generate_reconciliation_report.py` calls `reconcile_holdings()`, which
   internally calls `analytics.get_holdings()`, which self-heals a stale
   `position_snapshots` via `position_engine.ensure_positions_fresh()`.
3. Per-ticker `data_quality_flags` (from `position_snapshots`, surfaced on
   `Holding`) are mapped to the root-cause tags used in the original report
   (RC1/RC2b/RC2c/RC3/RC-EXCL), via a fixed dictionary owned by this script.
4. A markdown report is rendered deterministically, section by section, and
   written to `exports/holdings-reconciliation/holdings_reconciliation_<date>.md` (or `--output`).

## Script Contracts and Controls

- CLI accepts only `--report` (required), `--database`, `--output`. No SQL,
  ticker filters, or arbitrary DB path beyond what's explicitly passed.
- All reconciliation math is delegated to `src/holdings_reconciler.py` /
  `src/analytics.py` / `src/position_engine.py` -- this script only maps
  flags to root-cause tags and renders text. It never recomputes tolerances,
  cost basis, or FX rates itself.
- Root-cause narrative is limited to the fixed flag-to-tag mapping in
  `references/report-contract.md`; an unexplained mismatch is tagged
  `RC-UNKNOWN`, never guessed prose.

## Acceptance Criteria

- Running the script twice against the same CSV/database on the same day
  produces byte-identical output.
- A ticker with a `split_without_quantity`/`fx_stale`/`fx_unavailable`/
  `buy_missing_cost`/`sell_missing_proceeds`/`oversell_clamped` flag and a
  mismatch is tagged with the corresponding RC label in the report.
- A database with no mismatches renders a short report with an empty Root
  Cause Analysis section (omitted, not printed empty).
- `tests/test_generate_reconciliation_report.py` covers both the
  mismatch-tagging and no-mismatch paths using the same DuckDB fixture
  pattern as `tests/test_holdings_reconciler.py`.

## Assumptions

- The ground-truth CSV format is the Wealthsimple "holdings report" export
  already parsed by `holdings_reconciler.parse_holdings_csv` (`Symbol`,
  `Quantity`, `Book Value (CAD)`, `Market Unrealized Returns` columns).
- This agent does not persist anything to the database or edit
  reconciliation logic -- it is a read-only reporting layer on top of
  already-committed (or about-to-be-committed) pipeline code.
