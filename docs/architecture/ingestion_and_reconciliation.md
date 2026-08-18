# Ingestion, Reconciliation, and Position Engine

This document explains how raw statement/activity/email data becomes the
holdings, cost basis, and portfolio value figures reported by `analytics.py`,
the `analytics` CLI command, and the classify-portfolio skill. It replaces
three previously-independent reimplementations of "what do I currently own"
with one canonical computation. Background: `docs/holdings_reconciliation_2026-07-07.md`
documents the root-cause investigation that motivated this design.

## Ingestion Overview

Three independent intake paths land trade and price data in separate tables;
nothing is merged or deduplicated at ingestion time. Deduplication and
precedence are applied later, at read time, by the reconciliation passes and
`v_trade_events` (see below).

```
Statement PDFs                Activities CSV export          Wealthsimple emails
      |                              |                              |
statement_extractor.py         data_sorter.py                email_extractor.py
(DATE_CODE_PATTERN,            (_read_source_rows ->         (fetch_email_transactions)
 MONEY_AT_END_PATTERN,          raw_activity_exports verbatim
 wrapped-line money fallback,   -> _normalize_row /
 fx_rate extraction)            normalize_activity_type /
      |                         ACTIVITY_TYPE_MAPPING ->
database_command.py::           _publish_activities,          database_command.py::
 upload_statement_transactions   dedup by row_fingerprint/      upload_email_transactions
      |                          duplicate_ordinal)                   |
      v                              |                              v
 transactions                        v                       email_transactions
 (ticker rows) /               activities                    (reconciliation_status
 cash_transactions              (tracked in                    defaults to 'provisional')
 (unresolved-ticker rows)       activity_imports)

                    Price / FX history
                          |
        yfinance_extractor.py / market_data.py
        (sync_market_data for owned tickers;
         ensure_fx_history for config.FX_PAIR_SYMBOL)
                          |
                          v
                 historical_records
        (per-ticker OHLC + the FX pair's daily close,
         under a reserved ticker row: exchange='FX',
         security_type='fx_rate')
```

Key points:

- **Statement PDFs -> `transactions`/`cash_transactions`.** Rows without a
  resolved `ticker_id` go to `cash_transactions`. `MONEY_AT_END_PATTERN`
  requires a trailing `$debit $credit $balance` triple; `merge_wrapped_activity_rows`
  tries that pattern against the dated first line, then falls back to the
  fully merged group text (`_close_activity_group` in `statement_extractor.py`)
  so a description that pushes money onto a wrapped continuation line is not
  silently dropped. `upload_statement_transactions` logs a warning for any
  surviving BUY/SELL row with NULL debit and credit; the reconciliation pass
  below usually heals it without a database edit.
- **Activities CSV export -> `activities`.** `ACTIVITY_TYPE_MAPPING` only
  maps `MoneyMovement`/`Dividend`/`Interest`; `Trade` and `CorporateAction`
  keep their raw subtype. Deduplication is by `row_fingerprint` +
  `duplicate_ordinal`, so re-importing the same export (or an export that
  overlaps a prior one) is idempotent.
- **Wealthsimple emails -> `email_transactions`.** Every real trade starts
  `reconciliation_status = 'provisional'`; Interac deposits start
  `not_applicable`. Provisional rows are the most recent activity not yet
  confirmed by a statement or activities export.
  - **Trade-amount parsing and `amount_quality`.** A filled buy's total is
    labeled `Total cost`; a filled sell's is labeled `Total value` or `Total
    proceeds` — `parse_wealthsimple_email` (`email_extractor.py`) recognizes
    all three into the same `total_cost` field (fixing a real incident where
    a sell confirmation's proceeds were silently dropped because only `Total
    cost` was recognized, leaving that sale's cash impact and realized gain
    both at zero). If no such label matches, the amount is derived as
    `abs(quantity) * average_price` (the email's own fill price, not the
    position's book-average cost) rather than left blank. Each row records
    how its `total_cost` was obtained in `amount_quality`: `reported`
    (labeled total found), `derived_from_quantity_and_price` (fallback used),
    or `missing` (neither available). `v_trade_events`/the position
    engine/`analytics.get_realized_gain_summary` all read this marker to
    flag a sale's proceeds and realized gain as an estimate instead of
    presenting a derived figure as a confirmed brokerage amount.
  - **Idempotent replay enrichment.** Re-uploading a message that already
    exists (matched by `source`/`source_message_id`) refreshes its
    `email_transactions` row in place *only* when that row is still
    unmatched and provisional (`reconciliation_status = 'provisional'`, no
    `matched_activity_id`/`matched_transaction_id`) — e.g. a replay after an
    `email_extractor.py` parsing fix picks up a `total_cost` the original
    parse missed. A row a reconciliation pass has already linked to an
    activity/statement is authoritative and is never rewritten; if a replay's
    values disagree with a linked row, the linked values are kept and a
    warning is logged naming the message id, the differing field(s), and
    both values, for manual review (`_enrich_or_skip_email_transaction` in
    `database_command.py`). An enrichment update never counts toward
    `upload_email_transactions`'s inserted-row return value, so it cannot
    inflate `email_checkpoints.email_count`.
  - **Controlled replay:** `python src/app.py pipeline --source email
    --email-date-from YYYY-MM-DD` overrides the stored checkpoint for one run
    without moving it backward — `update_email_checkpoint` takes the
    `GREATEST` of the stored and newly-computed `checked_through_date`, so
    replaying an old date range can enrich existing rows (see above) without
    ever regressing the checkpoint a normal run already advanced past.
- **Price/FX history -> `historical_records`.** `sync_market_data` (per-ticker
  OHLC for owned securities) also calls `ensure_fx_history` (`market_data.py`),
  which creates a `tickers` row for `config.FX_PAIR_SYMBOL` (`USDCAD=X`) on
  first use and incrementally fetches its daily close from the earliest owned
  transaction date forward. This ticker is never a portfolio holding: it has
  no owning transactions, so it never appears in `position_snapshots`,
  `get_market_targets`, or classification output.

**Market-data refresh stage.** `app.py::run_pipeline` calls
`_run_market_data_refresh` once, after `ensure_positions_fresh`/
`complete_batch`, regardless of `--source` -- this is the pipeline's
"everything stored locally gets pulled" step. `sync_market_data` (prices, FX,
benchmark, ticker metadata) runs unconditionally every call, since
`MarketTarget.fetch_ranges` is already incremental. `sync_earnings_dividends`
and `sync_financial_snapshots` are staleness-gated per ticker
(`market_data.stale_earnings_symbols`/`stale_dividend_symbols`/
`stale_financial_snapshot_symbols`, each comparing that domain's own
`fetched_at` column against a `config.py` interval constant) so a routine run
stays fast instead of re-fetching all three heavier domains for every owned
ticker every time; `pipeline --full` bypasses every gate. **Classification is
not part of this stage or of `pipeline` at all** -- it is the standalone
`app.py classify` command (`docs/reference/cli.md`), run explicitly whenever
holdings change.

**Portfolio vs. research scope.** `get_market_targets` (`market_data.py`)
resolves its ticker set from a `LEFT JOIN` against `owned_dates`, kept to
owned-only by default (`include_research=False`) -- `pipeline` and this
stage never pass anything else, so a ticker with zero transactions is never
touched by a routine run, exactly as before this parameter existed. Passing
`include_research=True` additionally admits a ticker with a
`security_status.declared_status` of `wishlist` (`market_data.
RESEARCH_STATUSES`) even though it has no transactions, tagging each
returned `MarketTarget.scope` as `"portfolio"` or `"research"`. Only the
`investment-analyst-resources` skill's on-demand refresh phase passes this
-- see `.claude/skills/investment-analyst-resources/SKILL.md`'s Subject
scope section for why a research ticker needs real, persisted DB history (an
SMA-200 or 365-day relative-strength window cannot be built from a single
live yfinance pull). `avoid`/`retired` declarations are deliberately never
admitted by either scope.

## Reconciliation Passes

Precedence: **activities > transactions (statements) > email_transactions**.
Two idempotent, clear-and-rebuild passes in `database_command.py` apply it by
setting link columns. They run after every import (`app.py::run_pipeline`,
`data_sorter.py::sort_data`), and — as the enforcing backstop — at the start
of every `position_engine.recompute_positions()` run, which covers the
`recompute-positions` CLI and every lazy `ensure_positions_fresh()` rebuild.
The engine never computes from unreconciled links: a database migrated from
an older schema (where every link column is NULL) would otherwise count each
trade once per source, doubling every quantity.

### Pass A — `reconcile_statement_activities()`

Links each statement BUY/SELL row to the activities `Trade` row that reports
the same trade, via `transactions.superseded_by_activity_id`.

- Match: same `ticker_id` + direction, date within `RECON_DATE_WINDOW_DAYS_STMT`
  (5 days — widened from an initial 3 to cover a confirmed real case where a
  statement date and its activities date were 4 days apart), quantity within
  `max(RECON_QTY_ABS_TOL, RECON_QTY_REL_TOL_STMT * activities_qty)` (0.5% relative).
- One-to-one greedy assignment: candidates are sorted (exact-quantity matches
  first, then closest quantity, then closest date, then lowest id) and
  assigned first-come, so repeated identical trades on the same day
  (`duplicate_ordinal` > 1 in `activities`) pair off deterministically instead
  of all matching the same row.
- Every call resets **all** `superseded_by_activity_id` links to NULL before
  rematching, so it is safe to rerun at any time.
- **Unmatched activities rows still feed `v_trade_events` directly** — this is
  what heals a statement row with NULL debit/credit without any manual
  backfill: the covering activities row (which has `net_cash_amount`) simply
  wins.

### Pass B — `reconcile_email_transactions()`

Links each provisional email BUY/SELL row to whichever statement or
activities row reports the same trade, trying activities first (matching
`v_trade_events`'s precedence).

- Tolerance is wider than Pass A (`RECON_QTY_REL_TOL_EMAIL` = 2%, floor
  `RECON_QTY_ABS_TOL`, ±`RECON_DATE_WINDOW_DAYS_EMAIL` = 4 days) because DRIP
  fractional-share quantities can drift a little between the email
  confirmation and the broker's settled record.
- Resolution (`_resolve_email_match` in `database_command.py`): an exact
  match (zero quantity diff, zero date diff) is preferred and resolved
  deterministically by id even if more than one exists; once no exact match
  exists, a single tolerant candidate is accepted, but two or more tolerant
  candidates are left for human review (`reconciliation_status = 'review_required'`)
  rather than guessed.
- On match: `reconciliation_status = 'superseded'` plus exactly one of
  `matched_activity_id` / `matched_transaction_id`.
- Rows this pass previously superseded are reset back to `provisional` and
  rematched if their link target no longer exists (e.g. the statement row
  they pointed at was itself superseded by an activities row since the last
  run) — this keeps the pass correct across reruns as upstream data changes.
  Rows a human marked `review_required`, and `not_applicable` rows (Interac
  transfers), are left untouched.

**A DuckDB constraint to know about:** neither pass declares a `FOREIGN KEY`
on its link column (`transactions.superseded_by_activity_id`,
`email_transactions.matched_transaction_id`/`matched_activity_id`). DuckDB
does not support bulk-`UPDATE`ing a table that is a live foreign-key target
from another table's column, and both passes need to freely rewrite their
parent tables. These are intentionally "soft" links, enforced only by the
passes' own logic, not the schema.

## `v_trade_events`: the Unified Event View

A SQL view (`database.py::_create_trade_events_view`, recreated on every
`initialize_database` call so its logic always ships with code, never with
the database file) that normalizes all three sources into one BUY/SELL/SPLIT
stream with source precedence already applied:

- **activities**: `Trade` rows become BUY/SELL (sign from `activity_subtype`
  or a negative `quantity`); `CorporateAction` rows with a non-NULL quantity
  become SPLIT (signed delta — a subdivision adds shares). Priority 1.
- **statements** (`transactions`): BUY/SELL rows `WHERE superseded_by_activity_id
  IS NULL` — i.e. excluded once Pass A links them to an activities row.
  Priority 2.
- **email** (`email_transactions`): only rows still `reconciliation_status =
  'provisional'` — a matched/superseded email row is already represented by
  whatever it was matched to. Priority 3. `amount_currency` is the row's own
  `price_currency` (falling back to `CAD`), not hardcoded `CAD` — a prior
  version of this view always labeled email amounts `CAD` regardless of the
  security's actual listing currency, which meant the position engine's
  currency-mismatch FX conversion (below) never fired for a USD-priced email
  trade and its raw USD figure was booked as if it were already CAD.

Every branch also emits `amount_quality`: `activities`/`statements` rows are
always `'reported'` (their source never estimates); `email` rows carry
whatever `email_transactions.amount_quality` recorded (see above),
defaulting to `'missing'` if unset.

`STKREORG` statement rows (splits recorded by the broker with no quantity) are
never emitted by the view — they carry no usable data. If a split exists only
in a statement and was never captured by an activities import, the position
engine flags the ticker `split_without_quantity` (see below) instead of
computing a silently wrong share count.

## Position Engine

`src/position_engine.py` is the one place average-cost book value is
computed. It reads `v_trade_events`, walks each ticker's events in
chronological order, and writes two tables:

- **`position_ledger`**: one row per event with running quantity, running
  book value (CAD and market currency), and running realized gain — an audit
  trail also used to reconstruct historical portfolio value
  (`analytics.get_historical_portfolio_values`).
- **`position_snapshots`**: the final per-ticker state — quantity, CAD and
  market-currency book value, realized gain, provisional quantity (the
  portion still sourced from an unmatched email row), and a JSON array of
  data-quality flags. This is what every holdings consumer reads.

### Average-cost algorithm

Per ticker, events are processed in `(event_date, source_priority, source_id)`
order. State: `quantity`, `book_cad`, `book_mkt`, `realized_cad`.

- **BUY** (quantity `q`, CAD cost `c` from the event's `amount_cad`):
  `quantity += q`; `book_cad += c`; `book_mkt += c / fx_rate`. A NULL `c`
  (e.g. an unhealed statement extraction gap) still adds quantity but flags
  `buy_missing_cost` rather than guessing a cost.
- **SELL** (quantity `q`, CAD proceeds `p`): `sold = min(q, quantity)` — an
  oversell (recorded sells exceeding recorded buys) is clamped rather than
  driving quantity negative, flagged `oversell_clamped`. On an oversell, `p`
  is prorated to `p * (sold / q)` before computing realized gain — `p` covers
  the full confirmed `q`, but only `sold` shares are actually leaving this
  ticker's book, so crediting the unprorated `p` would count proceeds for
  shares the position never held as pure gain against zero cost (a real
  incident: `docs/plans/pltr-sale-cash-realized-gain.md`). Cash
  (`analytics.get_cash_summary`, below) is unaffected by this proration — it
  rolls forward the full reported/derived proceeds separately, since the
  brokerage did receive that full amount even though this ticker's book only
  reflects `sold` shares leaving. Realized gain = `p_used - sold * avg_cad`
  where `avg_cad = book_cad / quantity` (average cost is unchanged by a
  sell); `book_cad -= sold * avg_cad`; `book_mkt` follows the same
  average-cost formula in market currency; `quantity -= sold`. A non-`NULL`
  `p` whose `amount_quality` is not `'reported'` (derived from quantity x
  price rather than stated by the source) flags `sell_proceeds_estimated`;
  the BUY case flags `buy_cost_estimated` symmetrically. Both are separate
  from `buy_missing_cost`/`sell_missing_proceeds`, which mean no amount was
  available at all, estimated or otherwise.
- **SPLIT** (signed delta `d`): `quantity += d`; book values are untouched,
  so average cost per share drops automatically, exactly as a real split does.

**Worked example** (the WN/ZEQT case from the original investigation): buy
0.2701 + 0.2777 shares, a `CorporateAction` SPLIT event adds +1.0992 shares
(from the activities export), then a SELL of 1.6645 shares is recorded.
Summing the raw BUY/SELL deltas alone (the old, buggy approach) ignores the
split and makes the position go negative; walking the SPLIT event in its
correct chronological position keeps the running quantity positive
throughout and lands on the broker's true share count.

### FX resolution

CAD-per-unit-of-listing-currency, resolved per event in `_resolve_fx`:

1. The event's own recorded `fx_rate` (statements carry this directly).
2. The configured FX pair's (`config.FX_PAIR_SYMBOL`) historical close on or
   up to 5 days before the event date (`_fx_on_or_before`).
3. The most recent `fx_rate` seen anywhere in `transactions`, flagged `fx_stale`
   since it is not dated to this event.
4. `1.0`, flagged `fx_unavailable`, so valuation never raises.

Current-price valuation (as opposed to historical-event valuation) uses
`position_engine.latest_fx_rate`, the same chain but anchored to the most
recent FX close instead of a specific event date.

### Staleness and self-heal

`position_engine_meta` stores a `ledger_fingerprint` — a hash of row counts,
max ids, and reconciliation-link-column checksums across `transactions`,
`activities`, `email_transactions`, and `historical_records`
(`compute_fingerprint`, `FINGERPRINT_SQL`). Writable callers use
`ensure_positions_fresh(connection)`, which recomputes automatically when the
fingerprint no longer matches; this runs at the end of every pipeline run
(`app.py::run_pipeline`) and inside every `analytics.py` holdings read. The
classify-portfolio skill's read-only connection cannot recompute itself, so
`read_classification_data.py` checks the same fingerprint and raises an
actionable `RuntimeError` naming `uv run python src/app.py recompute-positions`
instead of silently reading stale data. A freshly migrated or rebuilt
database has empty `position_snapshots`, which is trivially stale, so the
very first read anywhere triggers a full compute with no manual step.

### Data-quality flag vocabulary

Stored as a JSON array in `position_snapshots.data_quality_flags` and
surfaced in `analytics.build_data_quality` as `position_engine_<flag>`:

| Flag | Meaning |
| --- | --- |
| `buy_missing_cost` | A BUY event had no cost amount from any source; quantity is correct, book value is understated. |
| `sell_missing_proceeds` | A SELL event had no proceeds amount; realized gain for that sale used cost as a stand-in (zero gain/loss), not a fabricated number. |
| `buy_cost_estimated` | A BUY event's cost came from a non-`'reported'` `amount_quality` (derived from quantity x fill price, typically an email confirmation with no recognized total label) rather than a source-stated figure. |
| `sell_proceeds_estimated` | Same, for a SELL event's proceeds. |
| `oversell_clamped` | Recorded sells exceeded recorded buys; quantity was clamped to zero instead of going negative. Realized-gain proceeds for the sale are prorated to the clamped quantity (see the SELL algorithm above); cash still rolls forward the full reported/derived proceeds. |
| `negative_quantity` | Internal-only marker used while `oversell_clamped` clamps the running total; not expected to appear in a persisted snapshot. |
| `fx_stale` | No FX pair close was available dated to an event; the most recent known `transactions.fx_rate` was used instead. |
| `fx_unavailable` | No FX rate was available at all for a non-CAD event; `1.0` was used as a last resort. |
| `split_without_quantity` | A statement `STKREORG` row has no matching `activities` `CorporateAction` quantity within the reconciliation window, so a known split could not be applied. |

A ticker with `quantity == 0` but a non-empty flag list (e.g. a fully clamped
oversell) is still returned by `analytics._get_net_positions` and surfaced
through `get_excluded_positions`, not silently dropped once it reaches zero.

`analytics.build_data_quality` also emits a handful of flags directly, not
prefixed with `position_engine_`, sourced from other analytics functions
rather than `position_snapshots.data_quality_flags`. Notably
`realized_gain_missing_proceeds`: `get_realized_gain_summary` walks
`v_trade_events` independently of the position engine, and applies the same
break-even treatment as `sell_missing_proceeds` above when a SELL has no
`amount_cad` from any source — imputing proceeds equal to the allocated cost
(zero realized gain for that sale) instead of booking the full cost basis as
a fabricated loss, and reporting the affected ticker/date here so the UI can
badge it as provisional rather than presenting a wrong number silently.
`get_realized_gain_summary` mirrors the position engine's other two
corrections independently, since it recomputes weighted-average cost/gain
itself rather than reading `position_snapshots.realized_gain_cad`: each
emitted event in `realized_gains.events` (the dashboard's realized-gains
table) carries the sale's `amount_quality`, and an oversell's proceeds are
prorated to the clamped (actually-held) quantity before computing gain, for
the same reason described in the SELL algorithm above.

## FX Valuation for Reporting

`analytics.py` values current holdings and historical series in CAD:

- `market_value_cad = quantity * latest_close * fx_today` (`fx_today` from
  `position_engine.latest_fx_rate`; `1` for CAD-listed tickers).
- `unrealized_mkt = market_value_mkt - book_value_mkt` (matches the units a
  broker holdings export reports unrealized P/L in).
- Historical series (`get_historical_portfolio_values`) forward-fills the FX
  pair's close the same way it forward-fills security prices, so the whole
  series stays in one currency.
- Portfolio total = `cash_cad + sum(market_value_cad)`.

`Holding` (in `analytics.py`) keeps `market_value`/`cost_basis` as CAD by
default — this is what allocation weights, concentration, and portfolio
totals sum, so a CAD/USD split no longer mixes nominal values in two
currencies. `market_value_mkt`, `cost_basis_mkt`, and `unrealized_mkt` carry
the same figures in the ticker's own listing currency when that view is
needed instead (e.g. to match a broker export).

## Cash Balance

`analytics.get_cash_summary` layers three sources, each filling a gap the one
before it cannot cover:

1. **Statement anchor**: the latest `statement_balances` row (every statement
   line's trailing balance, regardless of whether it resolved to a ticker).
2. **Activities rollforward**: `activities.net_cash_amount` summed for dates
   strictly after the anchor, since a statement PDF typically lags the
   activities CSV export by weeks.
3. **Provisional email rollforward** (`analytics._provisional_email_cash_events`):
   the CAD-converted, signed (`+` for SELL, `-` for BUY) `total_cost` of every
   still-provisional, ticker-resolved email trade, using the same
   FX-resolution chain as the position engine's `_resolve_fx`. Without this
   layer, a trade confirmed only by email has no cash effect at all until its
   statement or activities row eventually arrives — the position engine
   already reflects the shares moving, but cash silently omits the money,
   which is exactly the bug `docs/plans/pltr-sale-cash-realized-gain.md`
   traces (cash stuck at a stale statement balance while a real, evidenced
   sale sat unreconciled). A row drops out of this layer the moment
   reconciliation links it to an activity/statement (its
   `reconciliation_status` leaves `'provisional'`), so the authoritative
   source's own cash effect (already inside layer 2) takes over without
   double-counting.

`CashSummary` carries the split out separately: `provisional_adjustment` is
this layer's total CAD contribution (zero once every trade reconciles), and
`estimated_adjustment` is the subset of that built from a non-`'reported'`
`amount_quality` (derived or missing) rather than a source-stated figure.
`source` gets an `_with_provisional_email` suffix whenever the adjustment is
nonzero, and the dashboard's Cash KPI (`dashboard/web/src/app/page.tsx`)
appends the adjustment amount to its sub-label so a provisional balance is
never presented as indistinguishable from a fully reconciled one.

`get_historical_portfolio_values` applies the same three-source layering to
its per-date series (a running cumulative sum of `_provisional_email_cash_events`
merged against the value-date grid), so the trend chart and the Cash KPI
never disagree about a confirmed-by-email-but-not-yet-reconciled trade's cash
effect.

## Consumer Map

Three previously-independent reimplementations of "what do I own" now read
one computation:

| Consumer | Reads | Notes |
| --- | --- | --- |
| `analytics._get_net_positions` (feeds `get_holdings`, `get_excluded_positions`, `get_portfolio_summary`, `portfolio_report`) | `position_snapshots` + `historical_records` (latest price) + `latest_fx_rate` | Calls `ensure_positions_fresh` first. |
| `analytics.get_historical_portfolio_values` | `position_ledger` (day-end running quantity per ticker) + `historical_records` + the FX pair series | Replaces the old independent signed-delta walk over `transactions`/`email_transactions`. |
| `analytics.get_realized_gain_summary` | `v_trade_events` | Deduplicated across sources and split-aware, unlike the old statement-only walk. |
| `.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py` | `position_snapshots` + `position_ledger` (first/last buy date, buy/sell counts) | Read-only connection; raises on a stale fingerprint instead of recomputing. |

**Known scope limit:** `analytics.get_turnover_and_holding_period` has not
been migrated off raw `transactions` — it still walks statement-only data for
turnover and average holding period, so a trade only present in the
activities export or email is not yet reflected there. Everything else in
this document has moved to the unified sources.

## Rebuild Runbook

To rebuild from scratch (a fresh database, or after a bulk data change):

1. Import statements, the activities export, and emails in any order —
   `uv run python src/app.py pipeline` (or the individual `statements` / `import-activities`
   / `email` commands) runs both reconciliation passes automatically after
   each publish.
2. `uv run python src/app.py recompute-positions` — not required after step 1 (every
   holdings read self-heals), but useful to force a rebuild and see the
   ticker count it produced.
3. `uv run python src/app.py reconcile-holdings --report <broker-holdings.csv>` —
   compare against a broker holdings export. Exit code 0 means every ticker
   matched within tolerance; a nonzero exit prints a per-ticker mismatch
   table (quantity, CAD book value, market-currency unrealized P/L) plus any
   ticker present on only one side.

### Interpreting a `reconcile-holdings` mismatch

- **`quantity` mismatch**: check `position_snapshots.data_quality_flags` for
  that ticker (`split_without_quantity` means an activities import is
  missing; `oversell_clamped` means a data-entry error somewhere upstream).
- **`book_value_cad` mismatch** with quantity matching: usually
  `buy_missing_cost` on an unhealed statement row, or an FX resolution issue
  (`fx_stale`/`fx_unavailable`) on a non-CAD holding.
- **`unrealized_mkt` mismatch** with quantity and book value both matching:
  most often price staleness — the database's last stored close predates the
  CSV's intraday snapshot. Run `uv run python src/app.py yfinance-sync` to refresh
  prices before concluding it is a real bug.
