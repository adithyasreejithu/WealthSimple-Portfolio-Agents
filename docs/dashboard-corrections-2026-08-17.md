# Dashboard Corrections — 2026-08-17

Record of what shipped in response to four reported dashboard defects plus a
new wishlist surface. See `docs/plans/wishlist-dashboard-full.md` for the
deferred, richer version of the wishlist page.

## 1. New pipeline data never reached the dashboard

**Root cause:** `dashboard/api/main.py`'s `/api/portfolio/report` cache was
keyed on `Path(db).stat().st_mtime` — the main `.duckdb` file's modification
time. DuckDB commits land in the `.wal` sidecar first and only touch the main
file's mtime at checkpoint, so the cache could serve a stale payload
indefinitely between checkpoints even though new rows were already committed
and queryable. Verified live: the API was serving a report from 22:09:58 while
the `.wal` file carried writes through 22:14:09.

**Fix:**
- `analytics.get_data_watermark` (`src/analytics.py`) — a new read-only query
  returning `GREATEST()` of the freshness columns every writer already
  stamps (`position_snapshots.computed_at`, `portfolio_classifications
  .generated_at`, `historical_records.record_date`, `earnings_events`/
  `dividend_events`/`financial_snapshots.fetched_at`, `security_status
  .declared_at`, `staged_files.published_at`).
- `dashboard/api/main.py`'s report cache now keys on that watermark instead
  of file mtime.
- Every `POST /api/actions/*` job also calls a new `invalidate_report_cache()`
  on completion, as a second guarantee independent of the watermark query.

## 2. Last-updated date not fixed after a refresh

Same root cause as #1 — the freshness badge (`site-header.tsx` →
`freshness-badge.tsx`) reads `generated_at`/`database_mtime` from the same
cached report payload. Fixed by the same change.

## 3. Reloading appeared to lose classifications

Classifications were never lost — they persist to the `portfolio_classifications`
table and are served, uncached, by `GET /api/portfolio/classifications`. The
reported symptom was two data sources disagreeing after a reload:
`allocation.by_group` came from the *cached* `/api/portfolio/report`, while
per-holding `group`/`confidence` came from the *uncached* classifications
endpoint. Fixing #1 removes the split — both now advance together the moment
the watermark moves.

## 4. NVDU: no confirmation asked, and the classification was wrong

Two separate defects, both traced to source and fixed:

**4a — a pre-existing research ticker silently absorbed a real purchase.**
`src/ticker_pipeline.py`'s `resolve_or_enrich_ticker` had a fallback: if a
base symbol matched exactly one existing `tickers` row, that row was reused
regardless of whether its currency contradicted the newly inferred listing
currency — for every source type except `email_currency`. NVDU already had a
USD/NASDAQGM ticker row, created by `investment-analyst` ahead of any
purchase as a research placeholder for the *US* Direxion leveraged fund. When
the user's real purchase (a CAD-listed product) arrived via an unmatched
email trade with no explicit price-currency field, the resolver's fallback
matched the sole existing candidate and silently priced the position as the
wrong, USD-listed security — producing the nonsense +264% unrealized gain
the user flagged.

Fix: the mismatched-currency fallback now only fires for `source_type ==
"statement"` (hard FX-rate evidence). Every other source, including NVDU's
email/no-price-currency case, now falls through to the existing `unresolved`
branch — landing in the pending-ticker queue (`GET /api/tickers/pending`,
the `ResolveTickerForm` UI, or the `resolve-tickers` CLI prompt) for a human
to confirm, instead of guessing. Regression test:
`tests/test_staging.py::test_email_trade_with_weak_currency_evidence_does_not_reuse_mismatched_ticker`.

This is a **prevention fix**, not a retroactive one — the existing NVDU
`tickers` row still points at the wrong security. Correcting it requires
confirming the actual CAD provider symbol with the user before writing a new
mapping (a financial-data guess is not something to commit silently); that
step was intentionally left for the user to do via the now-working
resolve-tickers flow, once the correct symbol is known.

**4b — the "ask me" prompt was never removed.** It still exists
(`ticker_mapping.resolve_pending_interactively`, and the dashboard's
`ResolveTickerForm`) and still works for genuinely unresolved symbols. The gap
was in when a symbol *reached* that prompt, not the prompt itself — fixed by
4a's change.

**4c — the classifier had no rule for leveraged/trading ETFs.** NVDU's
`etf_category` ("Trading--Leveraged Equity") matched no tier in
`Knowledge-Base/ref/classification_rules_v1_1.yaml`, so it fell through to
`fallback_needs_review`. It was the only holding in `Needs Review`. Added a
new rule to `asset_class_and_etf_type_rules` assigning leveraged/inverse
trading ETF categories to `Growth` (medium confidence) — the closest fit per
`Knowledge-Base/ref/policy_v1_1.yaml`'s group definitions (higher-volatility,
satellite, upside-oriented), since `Alternatives` is reserved for non-equity
diversifiers (gold/commodity) and this is still 2x equity exposure.

## Extra bug found and fixed in scope: USD/CAD units mismatch

While tracing NVDU's numbers, found that
`.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py`
computed `position_market_value = quantity * close` where `close` is in the
security's **listing currency**, then compared it against `cost_basis =
book_value_cad`, which is always **CAD**. Every non-CAD holding's
`position_market_value`, `current_weight_percent`, and
`unrealized_gain_loss_percent` were wrong by roughly the USD/CAD rate (AAPL's
gain %, for example, was inflated ~1.4x before the fix).

Fix: convert the latest close to CAD via `position_engine.latest_fx_rate`
(the same CAD-per-unit lookup the write-path valuation already uses) before
pricing the position. Verified against the live production database: AAPL's
corrected `position_market_value` (214.6544818931368...) now matches
`analytics.get_holdings`'s CAD-denominated `Holding.market_value` bit-for-bit.
Regression test:
`tests/test_classification_workflow.py::test_usd_holding_market_value_and_gain_are_fx_converted_to_cad`.

## New feature: Wishlist page

`GET /api/wishlist` (`analytics.get_wishlist_overview`) and a new `/wishlist`
dashboard page. Scope, per the user: **status + classification + agent
verdict** (richer version — narratives, order guidance, scenarios — deferred
to `docs/plans/wishlist-dashboard-full.md`).

For each declared-wishlist ticker not currently held:
- **Status** — `security_status` (`declared_status`, `rationale`,
  `declared_at`, `declared_by`).
- **Classification** — `portfolio_classifications` (`primary_group`,
  `confidence`).
- **Agent verdict** — the most recent `investment-thesis.v1` artifact's
  `conclusion` block (`fundamental_rating`, `valuation_stance`,
  `thesis_direction`, `thesis_confidence`, `analysis_horizon`) and the most
  recent decision-proposal artifact's `proposed_action`, `confidence`,
  `summary`, and failing `policy_checks`, found by globbing
  `workspace/runs/*/agent_outputs/TICKER-*-thesis.json` and
  `workspace/runs/*/final/TICKER-*-decision.json` (filenames embed an
  ISO-8601 timestamp, so a lexicographic sort is also chronological).

**Vocabulary guard:** a not-owned subject's decision must use
`WishlistAction` (Buy/Watch/Wait/Pass), never the owned-holding
`PortfolioAction` vocabulary (Buy/Hold/Trim/Sell/Add). Because one production
artifact was found violating this (an older MP decision proposing "Hold"), the
overview flags `action_vocabulary_mismatch` rather than trusting the field,
and the frontend renders a visible warning instead of silently showing an
invalid action.

Currently 5 declared wishlist names: BSX, MP, SMCI, UBER, WST.

## Files changed

| Area | Files |
| --- | --- |
| Report cache / watermark | `dashboard/api/main.py`, `src/analytics.py` |
| Lock diagnostics | `src/database.py` |
| Ticker resolution fix | `src/ticker_pipeline.py` |
| Classifier rule | `Knowledge-Base/ref/classification_rules_v1_1.yaml` |
| FX units fix | `.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py` |
| Wishlist API | `src/analytics.py`, `dashboard/api/main.py` |
| Wishlist frontend | `dashboard/web/src/app/wishlist/page.tsx`, `src/lib/api.ts`, `src/lib/types.ts`, `src/components/app-sidebar.tsx` |
| Docs | `docs/architecture/dashboard_api.md`, `docs/reference/cli.md`, this file, `docs/plans/wishlist-dashboard-full.md` |
| Tests | `tests/test_staging.py`, `tests/test_analytics.py`, `tests/test_dashboard_api.py`, `tests/test_classification_workflow.py` |

## Verification performed

- Full backend suite: `uv run python -m unittest discover -s tests` — 1281
  tests, all passing.
- Frontend: `npx tsc --noEmit`, `npx eslint`, `npx next build` — all clean;
  `/wishlist` registered as a dynamic route.
- Live end-to-end: ran the dashboard API against the production database,
  confirmed `GET /api/wishlist` returns all 5 tickers with status,
  classification, and verdict; confirmed `GET /api/portfolio/report`'s
  `generated_at` now advances independently of `database_mtime` (proving the
  cache no longer keys on file mtime).
- Confirmed the classifier fix directly: a record with `etf_category:
  "Trading--Leveraged Equity"` now classifies as `Growth`/`medium` instead of
  `Needs Review`.

## Known follow-ups, not done here

- **NVDU's existing wrong ticker mapping** still needs a human-confirmed CAD
  provider symbol before it can be corrected in the production database (see
  4a above) — the code fix only prevents the bug from recurring for future
  purchases.
- **`investment-analyst-resources`'s `--register-wishlist` flag writes to
  `security_status`** (via `set_security_status`) even though the
  `security-status` skill's own documentation states no agent has write
  access to that table. Found while researching the wishlist feature; not
  fixed here — needs the user to decide whether the write path or the
  documentation is wrong.
- **Full wishlist page** (narratives, order guidance, scenarios, KB status
  integration) — see `docs/plans/wishlist-dashboard-full.md`.
