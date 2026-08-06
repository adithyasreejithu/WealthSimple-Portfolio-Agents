# Fix stale position value/weight in investment-analyst-resources, without duplicating portfolio-value math

## Context

Running `investment-analyst-resources` for `NOW` showed `portfolio_context.position_market_value`/`weight_pct` computed from a **July 31 close ($111.23)** even though the same run had just refreshed prices through **August 4 ($118.14)**. Root cause: `db_resources.py` deliberately sources those two fields from `exports/portfolio-classification/portfolio-classification.json` — a snapshot written whenever `classify-portfolio` last ran (manual-trigger only) — instead of computing them live, because computing them via `src/analytics.py` would require DuckDB's exclusive read-write connection (`ensure_positions_fresh` → `get_shared_connection`), which this skill's read-only, parallel-safe `--mode read`/`gate` architecture is built to avoid taking.

Investigation confirmed:
- The **dashboard** has no such problem — `analytics.py::_get_net_positions` computes market value live on every request (latest `historical_records` close × FX × quantity), self-healing `position_snapshots` first via `ensure_positions_fresh`. It never touches the classification JSON.
- The classification JSON has other **legitimate, non-price consumers** that don't need daily freshness: `kb-sync-portfolio` (generates the two KB portfolio pages), `kb-staleness-gate` (owned-ticker list), `kb-update-thesis` (portfolio-fit narrative), and `evaluate-stock-decision`'s worksheet (`role`/`asset_class`/ETF fields, already gated at >7 days stale). The rubric's scored math never reads `weight_pct`/`position_market_value` — confirmed in `rubric.py`/`validate_recommendation.py`. So the export itself should **not** be eliminated.
- The lock-avoidance reasoning that justified reading value/weight from the JSON conflated two things: `ensure_positions_fresh`'s **self-heal** genuinely needs the write connection, but the **query** underneath it (`historical_records` latest-close join + `latest_fx_rate`) is plain read-only SQL. `latest_fx_rate` (`src/position_engine.py:120`) is already connection-agnostic — pure `SELECT`s, no writes.
- Decision made: don't auto-trigger `classify-portfolio` from inside this skill (reintroduces the exact portfolio-wide/write-lock exposure the read-only architecture avoids, just to get a dollar figure). Instead compute value/weight live, read-only, in this skill.
- User's explicit condition on that approach: don't end up with **two independent implementations** of "position quantity × price × FX → market value." The fix must share one implementation between the existing write-path (`analytics.py`) and the new read-only path (`db_resources.py`), not fork it.

## Approach

Extract the row-fetch that `analytics.py::_get_net_positions` currently inlines (`src/analytics.py:128-159`) into a new connection-agnostic function in `src/position_engine.py`, next to the already-shared `latest_fx_rate`. Both the write path and the read-only path call the same function; the only difference is whether `ensure_positions_fresh` ran first.

### 1. `src/position_engine.py` — new shared query

Add `read_live_position_values(connection: Any, ticker_ids: list[int] | None = None) -> list[dict[str, Any]]`, placed near `latest_fx_rate` (`position_engine.py:120`). It runs exactly the `latest_prices` CTE + join currently inlined in `analytics.py:130-159` (same `WHERE s.quantity <> 0 OR s.data_quality_flags IS NOT NULL` filter, same columns), optionally narrowed with `AND s.ticker_id IN (...)` when `ticker_ids` is given, and returns dict rows instead of tuples. No FX, no multiplication — just the stored quantity/book fields joined to the latest ingested close per ticker. Purely read-only; safe on either connection type.

### 2. `src/analytics.py` — refactor, not rewrite

Replace the inline SQL block in `_get_net_positions` (`analytics.py:128-159`) with a call to `position_engine.read_live_position_values(connection)`. Keep everything downstream unchanged (`analytics.py:161-203`): same FX-cache loop via `latest_fx_rate`, same `Holding` construction. This is behavior-preserving — existing `tests/test_analytics.py` (`get_holdings` fixtures, e.g. lines 61, 415, 722-936) must still pass unmodified.

### 3. `db_resources.py` — new read-only live-value functions

In `.claude/skills/investment-analyst-resources/scripts/db_resources.py`, add:
- `read_live_market_value(connection, ticker_id) -> dict | None` — calls `position_engine.read_live_position_values(connection, ticker_ids=[ticker_id])`, applies `position_engine.latest_fx_rate(connection, currency)` (imported the same way `DATABASE_PATH`/`REQUIRED_TABLES` already are at the top of this file), returns `{market_value_cad, market_value_mkt, last_price, last_price_date, fx_rate}`. `None` if not currently held.
- `read_live_portfolio_weight(connection, ticker_id) -> dict | None` — calls `read_live_position_values(connection)` for **all** currently-held positions, computes each one's `market_value_cad` the same way (FX-cached per currency, mirroring `analytics.py`'s loop), sums for `total_portfolio_value`, and returns `{weight_pct, position_market_value, total_portfolio_value}` for the requested ticker. Formula matches the dashboard's exactly (`dashboard/api/main.py:164`: `holding.market_value / total_value`, no cash in the denominator) so the two surfaces agree.

### 4. `read_portfolio_context` — split price-live fields from stable ones

Change its signature to `read_portfolio_context(connection, ticker: str, classification_json=DEFAULT_CLASSIFICATION_JSON)`. Keep reading the JSON for `role` (`primary_group`) and `account_type` — stable fields with no daily-freshness need. Replace `weight_pct`/`position_market_value` with the output of the two new live functions. Keep `classification_generated_at` in the result for transparency, but it no longer gates the value/weight numbers' correctness — only the role/tag freshness. Update `read_ticker_bundle` (already holds `connection` in scope) to pass it through.

### 5. Update stale documentation

- `db_resources.py` module docstring (lines 1-16): rewrite the "deliberately not recomputed here" paragraph to describe the new shared-function approach — one query (`position_engine.read_live_position_values`) and one FX helper (`position_engine.latest_fx_rate`), used by both the write path and this read-only path, so there is exactly one implementation of the math.
- `.claude/skills/investment-analyst-resources/SKILL.md` "What it does not do" section (lines 43-47): the "weight and market value come from the already-generated portfolio-classification.json" bullet is no longer accurate — reword to say weight/market value are computed live and read-only via the shared `position_engine` helpers, while role/tags/confidence still come from the classification export.
- `.claude/skills/investment-analyst-resources/references/resource-contract.md`: update any description of `portfolio_context` fields to reflect that only `role`/`account_type`/`classification_generated_at` are export-sourced; `weight_pct`/`position_market_value` are live.
- `.claude/skills/investment-analyst-resources/scripts/freshness_gate.py:159`: narrow the classification-missing gap message from "weight/role unavailable" to "role/tags unavailable" (weight/value no longer depend on the export's presence).
- Do **not** edit `docs/plans/investment-analyst-resources-skill.md` — per `CLAUDE.md`, approved plans are frozen historical record, not updated post-implementation.

### 6. Tests

- `tests/test_investment_analyst_resources.py:247-265` (`test_portfolio_context_missing_file_is_a_gap_not_an_error`, `test_portfolio_context_matches_by_ticker`) need updating for the new `connection` parameter and to assert `weight_pct`/`position_market_value` come from a fixture DB's `position_snapshots`/`historical_records` rows, not the JSON fixture. Add a case proving value/weight are correct even when the classification JSON is missing entirely (only `role`/`account_type` become `None`).
- Add a case in `tests/test_analytics.py` or a focused test for `position_engine.read_live_position_values` confirming it returns identical rows whether called with `ticker_ids=None` (all) or scoped to one ticker.
- Run `uv run python -m unittest discover -s tests` for the full suite.

### 7. Manual verification

Re-run `uv run python .claude/skills/investment-analyst-resources/scripts/investment_analyst_resources.py --ticker NOW` and confirm `portfolio_context.position_market_value`/`weight_pct` now reflect the $118.14 (or whatever is then-latest) close, not the stale $111.23 snapshot — while `role`/`account_type`/`classification_generated_at` still come from the export as before.

## Explicitly out of scope

- `evaluate-stock-decision`'s `scoring_worksheet.py` (`_position_context`, line ~691-723) still reads `weight_pct` from the classification JSON for narrative worksheet context. Not touched here: it doesn't feed the rubric's scored math (confirmed in `rubric.py`), and it already surfaces `classification_stale` explicitly in its printed summary. Worth a follow-up if this data flow needs the same fix.
- No auto-triggering of `classify-portfolio` from any read/refresh path.
- No change to `classify-portfolio`'s manual-only trigger model (no cron/schedule added).
- No change to `exports/portfolio-classification/portfolio-classification.json`'s schema — it keeps `current_weight_percent`/`position_market_value` fields for `kb-sync-portfolio`'s generated KB pages, which are fine as a point-in-time label there.
