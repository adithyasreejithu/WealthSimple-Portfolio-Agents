# Report contract

Fixed section order, generated deterministically from
`reconcile_holdings()`/`get_holdings()`/`get_excluded_positions()` output
(`src/holdings_reconciler.py`, `src/analytics.py`) -- never freehand prose:

1. Header -- date generated, report period, data sources.
2. Executive Summary -- compared-ticker count, mismatch count/rate,
   DB-only/report-only tickers.
3. Detailed Mismatch Table -- every ticker present in both the DB and the
   CSV, quantity/book value (CAD)/unrealized P&L (market currency), `✓` for
   fields within tolerance, root-cause tag(s) for fields outside it.
4. Legend -- only for root-cause tags actually observed this run.
5. Excluded Positions -- from `get_excluded_positions()` (negative quantity
   or `oversell_clamped`).
6. Root Cause Analysis -- one subsection per root-cause tag actually
   observed this run; omitted entirely when there are no mismatches.
7. Corrective Actions, Verification Checklist, Appendix -- static reference
   text pointing at `docs/architecture/ingestion_and_reconciliation.md`.

Root-cause tag mapping (`position_engine.py` data-quality flag -> tag):

| Flag | Tag | Label |
| --- | --- | --- |
| `split_without_quantity` | RC1 | Stock split never applied |
| `fx_stale` | RC2b | Currency/FX resolution issue (stale FX rate used) |
| `fx_unavailable` | RC2b | Currency/FX resolution issue (no FX rate available) |
| `buy_missing_cost` | RC2c | Missing cost basis on a buy |
| `sell_missing_proceeds` | RC3 | Missing proceeds on a sell |
| `oversell_clamped` | RC-EXCL | Oversell clamped to zero rather than left negative |
| (mismatch with no matching flag) | RC-UNKNOWN | Unexplained mismatch -- needs investigation |

Determinism: given the same CSV, database, and calendar day, the rendered
report is byte-identical. The only date-derived text is `Date Generated`
and `Report Period`, both set to `date.today()` with no time-of-day
component, so re-running within the same day produces an identical file.

Never add SQL, ticker filters, or a `--database` path outside a value the
caller explicitly passes. Never invent root-cause narrative beyond the flag
mapping above -- an unexplained mismatch is tagged `RC-UNKNOWN`, not guessed.
