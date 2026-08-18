# PLTR Sale Proceeds, Cash, and Realized-Gain Repair

## Summary

The live incident is a provisional PLTR email sale dated `2026-08-05`, not an
`oversell_clamped` event: the parser recorded `1.0` share but no proceeds,
leaving `0.7449` shares, cash at `$0.31`, and PLTR realized gain at `$0.00`.
Fix email sale parsing, derive missing proceeds when necessary, include
provisional trades in cash, and replay the existing email without forcing PLTR
closed beyond source evidence.

## Implementation Changes

- Parse filled sale labels `Total value`, `Total proceeds`, `Total cost`, and
  trade-only `Amount` into the existing trade-amount field. If absent, derive
  gross proceeds as `abs(quantity) × average_price`, where `average_price` is
  the email's own per-share fill price -- distinct from `_apply_sell`'s
  `avg_cad`/`avg_mkt`, which are the position's weighted-average book cost.
  Name the derived-proceeds variable to make this unambiguous at the call
  site (e.g. `fill_price`, not `avg_price`), since reusing the position's
  average would silently reproduce the $0 realized-gain bug in a new form.
- Update `v_trade_events` to expose the correct `price_currency` for email
  amounts plus an amount-quality marker: `reported`,
  `derived_from_quantity_and_price`, or `missing`.
- Convert email amounts to CAD using the transaction-date FX resolver. Treat
  derived amounts and market-FX conversions as estimates until an activities
  or statement row supersedes the email.
- Keep cash and realized-gain math separate:
  - Cash receives the full reported or derived sale proceeds.
  - Realized gain equals CAD proceeds minus the pre-sale weighted-average CAD
    cost of the sold shares.
  - For an oversell, prorate proceeds to the clamped quantity for realized
    gain, but retain the full proceeds in cash and report the unmatched
    quantity and proceeds as a data-quality issue.
- Roll current and historical cash forward with resolved, unreconciled email
  BUY/SELL events after the latest statement anchor. BUY values are negative
  and SELL values positive. Exclude superseded emails so authoritative
  activities automatically replace estimates without double-counting.
- Extend `CashSummary` with defaulted `provisional_adjustment` and
  `estimated_adjustment` fields. Add matching fields to
  `/api/portfolio/summary` and the report TypeScript types; label the Cash KPI
  when provisional or estimated email activity is included.
- Extend realized-gain output with provisional and estimated ticker markers.
  Show this status beside PLTR in the realized-gains table and replace
  `sell_missing_proceeds` with an estimate-quality flag when calculation
  succeeds.
- Preserve quantity integrity: apply every sale confirmation exactly, retain
  the existing clamp guard, and never zero PLTR through a manual override or
  residual tolerance.

## Existing PLTR Repair and CLI

- Make email upload idempotently refresh an existing `email_transaction` when
  the same message ID is reparsed with newly available quantity, price, total,
  currency, or date. Preserve its database identity and reconciliation links;
  unchanged messages remain no-ops.
- Permit in-place enrichment only for unmatched provisional email rows
  (`matched_activity_id IS NULL` and `reconciliation_status = 'provisional'`).
  Never modify an email row already linked to an activity or statement. If
  replayed values conflict with a linked row, retain the authoritative
  reconciliation result and emit a warning naming the message ID, the
  differing field(s), and both the stored and newly-parsed values for manual
  review.
- Add `pipeline --source email --email-date-from YYYY-MM-DD` to override the
  checkpoint for a controlled replay. Replays must never move the stored
  checkpoint backward or increase its new-message count for update-only rows.
- After deployment, stop the dashboard API to release DuckDB, back up the
  production database, then replay from `2026-08-05`, reconcile, and recompute
  positions.
- If replayed sale confirmations total `1.7449` shares, PLTR closes naturally.
  If evidence still totals only `1.0`, retain and prominently report the
  unexplained `0.7449` balance rather than fabricating a full exit.
- Update the ingestion/reconciliation architecture and CLI references with the
  provisional cash rules, formulas, estimate lifecycle, replay option, and
  oversell treatment.

## Test Plan

- Email parsing: sale variants with total value/proceeds, USD and CAD
  currencies, and quantity-times-average fallback.
- Upload/replay: an existing incomplete message is enriched in place;
  unchanged replay is a no-op; checkpoints remain monotonic.
- Position and realized gain: weighted-average cost, reported and derived
  proceeds, FX conversion, missing inputs, partial sales, and oversells with
  prorated realized proceeds.
- Cash: statement anchor plus activities plus provisional email sells/buys;
  reconciliation replacement without double-counting; current and historical
  balances agree.
- PLTR-shaped integration fixtures:
  - `1.7449` held and `1.0` sold leaves `0.7449`.
  - Source-backed sales totaling `1.7449` close the position.
  - Cash receives full proceeds and PLTR realized gain is no longer zero.
- API/frontend: serialization of provisional/estimated fields, Cash KPI
  labeling, realized-gain status, and data-quality messages.
- Shut down the live API before running targeted and full suites. The inspected
  baseline was 151 targeted tests with 150 passing; the sole failure was an
  existing correlation endpoint test attempting to open the production DuckDB
  while API PID `44696` held it.

## Acceptance Criteria

- PLTR cash includes the August 5 sale's provisional CAD proceeds instead of
  remaining `$0.31`.
- PLTR realized gain uses sale proceeds minus weighted-average cost and is
  visibly marked provisional or estimated as applicable.
- `sell_missing_proceeds` disappears when total value or
  quantity-times-average can supply proceeds.
- Later activities or statement ingestion replaces the email estimate exactly
  once.
- PLTR disappears from holdings only when source-backed sold quantity accounts
  for the complete position.
- No manual cash adjustment or hard-coded PLTR correction is introduced.
