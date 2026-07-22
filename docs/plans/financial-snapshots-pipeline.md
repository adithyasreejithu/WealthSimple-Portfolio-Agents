# Financial Snapshots Pipeline

*Status: **implemented, one gap open** (2026-07-21). Core pipeline (schema,
extractor, upload, sync, CLI, docs, tests) is built and verified — see
"Implementation status" below. Decision #9 (ephemeral first-run annual
pull) was added to this plan after the background implementation and is
not yet built. Move to `docs/plans/implementation/` once decision #9 is
closed out.*

## Implementation status (verified 2026-07-21)

Confirmed directly against the real repo files and by running the actual
test suite (not just reading the plan):

- `config.py`: `DATABASE_SCHEMA_VERSION = 13`. ✅
- `src/financial_snapshots_extractor.py` exists, matches the field-mapping
  table below exactly (alias-list lookups, capex-sign-aware FCF fallback,
  `extra` JSON for unmapped line items, ETF-empty-is-not-an-error handling). ✅
- `database_command.py::upload_financial_snapshots`,
  `market_data.py::sync_financial_snapshots`, `app.py`'s
  `financial-snapshots`/`financial-snapshots-sync` commands all present. ✅
- `docs/reference/cli.md` and `docs/architecture/database_schema.md` both
  have their `## Financial Snapshots` sections. ✅
- Rubric (`decision-rubric.yml`, still `v1.3`) and
  `scoring_worksheet.py`/`validate_recommendation.py` untouched — correctly
  matches "Deliberately deferred" below, not a gap. ✅
- **Tests run directly, not assumed**: 23 targeted tests across
  `tests/test_financial_snapshots_extractor.py` (11),
  `tests/test_database_command.py::FinancialSnapshotsUploadTest` (5),
  `tests/test_market_data.py::FinancialSnapshotsSyncTest` (4),
  `tests/test_database.py::DatabaseTest::test_version_twelve_schema_is_upgraded_and_adds_financial_snapshots_table` (1),
  `tests/test_app.py::AppPipelineTest`'s two financial-snapshots tests (2) —
  all passing. (Full-suite `uv run python -m unittest discover -s tests`
  could not be run in this sandbox — no network for `uv sync` — so this is
  a targeted, not exhaustive, confirmation; worth a full local run too.)

**The one thing left out**: decision #9's ephemeral first-run annual-
statement pull (`Ticker.financials`/`balance_sheet`/`cashflow`, handed to
the analyst as one-time context on a ticker's first-ever run, never
persisted) is not implemented anywhere in `financial_snapshots_extractor.py`
or `market_data.py` — confirmed by direct search, no matches. This is
expected, not a bug: decision #9 was added to this plan in conversation
*after* the background implementation already happened, so it was never
in scope for that build. Still open.

## Context

The pipeline currently pulls security metadata and OHLCV history from
yfinance (`src/yfinance_extractor.py`, `src/market_data.py`'s
`sync_market_data`/`yfinance-sync`) and, as of the just-shipped
`docs/plans/earnings-dividends-pipeline.md`, company-declared earnings and
dividend calendars (`src/earnings_dividends_extractor.py`,
`src/market_data.py`'s `sync_earnings_dividends`/`earnings-dividends-sync`,
schema v12's `earnings_events`/`dividend_events` tables). It has no
equivalent pull for **per-quarter company financial statement data** —
revenue, net income, EPS, margins, leverage, liquidity, and free cash flow —
even though the decision-support rubric already scores several of these
(`Knowledge-Base/taxonomy/decision-rubric.yml`'s `derived:revenue_growth_yoy`,
`derived:net_income_latest`, `derived:debt_to_equity`, `derived:current_ratio`).
Today those `derived:*` values are computed fresh by
`.claude/skills/_archive/evaluate-stock-decision/scripts/scoring_worksheet.py`
from a single ephemeral yfinance pull each time `stock-data-prep` runs, then
discarded — there is no persisted history, so "year over year" math only ever
has whatever two annual columns yfinance happens to return in that moment,
and nothing survives between analysis runs.

Two downstream consumers motivate persisting this data instead of
recomputing it every time:

1. A future dashboard "financials trend" view. **Out of scope for this
   plan** — already logged in `docs/project/todo.md` Backlog ("Add a
   dashboard 'financials trend' view once a `financial_snapshots` table
   exists in the pipeline database..."), which is itself the reason this
   plan's table is named `financial_snapshots`.
2. Upgrading the decision-support rubric's `derived` financial metrics from
   point-in-time-only to trend-aware. This plan makes that upgrade *possible*
   by persisting quarters going forward, but **editing
   `Knowledge-Base/taxonomy/decision-rubric.yml` or
   `scoring_worksheet.py`/`validate_recommendation.py` to actually consume
   the new table is out of scope here** — the rubric may only be edited
   through the `author-decision-rubric` skill (with its own validation and
   version-bump process, per `CLAUDE.md`), which is a separate, deliberate
   follow-on effort. This plan covers only the extraction + storage half,
   exactly like the earnings/dividends plan did for its two consumers.

### Why persisting matters even though yfinance itself has almost no memory

yfinance's quarterly statement endpoints (`Ticker.quarterly_income_stmt`,
`Ticker.quarterly_balance_sheet`, `Ticker.quarterly_cashflow` — confirmed as
the current, correct yfinance 1.5.1 attribute names, see "yfinance API
surface" below) only ever return a short rolling window of the most recent
trailing quarters, not deep multi-year history — the same "provider returns a
fixed recent window, not the full past" shape already seen and handled for
`Ticker.earnings_dates` in the earnings/dividends plan. This means:

- A single sync run cannot backfill years of quarterly trend data; it can
  only ever see what yfinance currently exposes (recent trailing quarters).
- The *pipeline*, not yfinance, is what accumulates history: each periodic
  `financial-snapshots-sync` run appends whatever new quarter(s) have since
  been reported, and previously-stored quarters are retained even after they
  scroll out of yfinance's own rolling window. True year-over-year comparison
  (a quarter vs. the same quarter four periods back) only becomes reliably
  available after enough sync cycles have accumulated that depth locally —
  this is the entire value proposition of this plan, and should be stated
  plainly in the docs so it isn't mistaken for an immediate backfill.

### Readiness assessment: buildable now, with one live-verification gap

- yfinance access, session/cache setup, ticker resolution, the
  ownership-scoped ticker-list pattern (`get_market_targets`), and the
  upload/sync/CLI conventions all already exist and are directly reusable —
  confirmed by reading the real, current `src/database.py` (schema v12),
  `src/earnings_dividends_extractor.py`, `src/database_command.py`,
  `src/market_data.py`, and `src/app.py` directly via the Read tool.
- The yfinance calls needed are the same "financials" group this repo
  already fetches (ephemerally) via the archived
  `.claude/skills/_archive/fetch-stock-research-data/scripts/fetch_stock_research_data.py`'s
  `_fetch_financials`, which calls `client.income_stmt`,
  `client.quarterly_income_stmt`, `client.balance_sheet`,
  `client.quarterly_balance_sheet`, `client.cashflow`,
  `client.quarterly_cashflow`. Inspecting the actually-installed `yfinance
  1.5.1` package (`.venv/Lib/site-packages/yfinance/ticker.py`) confirms these
  are still the current property names (`quarterly_income_stmt` calls
  `get_income_stmt(pretty=True, freq='quarterly')`, etc.; `financials`/
  `quarterly_financials` are just aliases for the income-statement ones) —
  the "yfinance renames things across versions" risk called out in the task
  is not a problem for these specific attribute names right now.
- **Gap: no live network verification was possible in this planning
  session** (the planning sandbox has no egress to Yahoo's endpoints). The
  earnings/dividends plan was able to live-test row counts, ETF empty-result
  behavior, and units against real held tickers before finalizing; this plan
  could not. Section "Requires live verification before implementation"
  below lists exactly what must be confirmed against real held tickers
  (mirroring `NVDA`, `MCD`, `T`, `CDZ.TO` from the precedent) as the first
  implementation step, before the extractor's field-mapping logic is
  considered final.

### yfinance field mapping (from the archived script + `scoring_worksheet.py`'s existing line-item lookups)

The archived `fetch_stock_research_data.py`'s `_fetch_financials` already
pulls these six DataFrames (annual + quarterly for each of income statement,
balance sheet, cash flow) into the ephemeral research JSON; this plan reuses
only the **quarterly** three (`quarterly_income_stmt`,
`quarterly_balance_sheet`, `quarterly_cashflow`) since the goal is per-quarter
history. Each returns a DataFrame indexed by line-item label with one column
per period-end date (most recent first); `scoring_worksheet.py`'s existing
`_line_item` helper already tolerates label variants across yfinance
versions/industries via alias tuples, e.g. `("Total Revenue", "TotalRevenue",
"Revenue")` — this plan's extractor reuses that exact alias-list-lookup
pattern (not a single hardcoded label) for every named column, since the task
explicitly notes line items vary by industry (e.g. a bank's balance sheet).

| Named column | Statement | Line-item aliases tried in order | Fallback if absent |
|---|---|---|---|
| `revenue` | `quarterly_income_stmt` | `Total Revenue`, `TotalRevenue`, `Revenue` | `NULL` |
| `net_income` | `quarterly_income_stmt` | `Net Income`, `NetIncome`, `Net Income Common Stockholders` | `NULL` |
| `eps` | `quarterly_income_stmt` | `Diluted EPS`, `DilutedEPS` | `Basic EPS`, `BasicEPS` |
| `gross_margin` (computed) | `quarterly_income_stmt` | `Gross Profit`, `GrossProfit` ÷ revenue | compute as `(revenue - Cost Of Revenue) / revenue` if `Gross Profit` absent |
| `operating_margin` (computed) | `quarterly_income_stmt` | `Operating Income`, `OperatingIncome` ÷ revenue | `NULL` if operating income absent |
| `debt_to_equity` (computed) | `quarterly_balance_sheet` | `Total Debt`/`TotalDebt` ÷ (`Stockholders Equity`/`Total Stockholder Equity`/`StockholdersEquity`) | `NULL` if either side missing |
| `current_ratio` (computed) | `quarterly_balance_sheet` | (`Current Assets`/`Total Current Assets`/`CurrentAssets`) ÷ (`Current Liabilities`/`Total Current Liabilities`/`CurrentLiabilities`) | `NULL` if either side missing |
| `free_cash_flow` | `quarterly_cashflow` | `Free Cash Flow`, `FreeCashFlow` (yfinance computes this directly in recent versions) | `Operating Cash Flow`/`OperatingCashFlow`/`Total Cash From Operating Activities` **+** `Capital Expenditure`/`CapitalExpenditure` (capex is already stored as a negative outflow by yfinance, so this is addition, not subtraction — must be verified live, see below) |

This is exactly the named-column list the task specified (revenue, net
income, eps, gross/operating margin, debt/equity, current ratio, free cash
flow) cross-checked against the rubric's actual `evidence_fields`
(`derived:revenue_growth_yoy`, `derived:net_income_latest`,
`derived:debt_to_equity`, `derived:current_ratio`; `derived:fcf_yield`
divides `yfinance:data.valuation.freeCashflow` by market cap — this plan does
not invent a competing FCF concept, it persists the raw dollar figure the
existing gate/dimension math already reads elsewhere).

Everything else either statement returns (total assets, total liabilities,
shares outstanding, R&D, SG&A, dividends paid, industry-specific lines like a
bank's net interest income, etc.) is preserved in the `extra` JSON column
rather than dropped or requiring a future migration — see schema below.

### Confirmed via live test against real held tickers (2026-07-21)

Run against `NVDA`, `MCD`, `T`, `CDZ.TO` (pulled from the actual
`tickers`/`ticker_provider_mappings` tables), plus a bonus check against
`JPM` (not held, but the closest real example of the bank-balance-sheet edge
case called out in the Risks section below) — using the same session
pattern `yfinance_extractor.py` already uses. This closes the "no live
network verification was possible in this planning session" gap from the
Readiness assessment above.

- **Trailing quarter depth is not a flat 4-5 as assumed — it varies by
  statement.** `quarterly_income_stmt` consistently returned **5** periods
  for all three equities. `quarterly_balance_sheet` and `quarterly_cashflow`
  returned **6-7** periods (NVDA: 7/7; MCD: 6/5; T: 6/6) — one or more
  quarters deeper than the income statement. Corrected from the "roughly
  4-5" guess in the Readiness assessment.
- **The three statements' period-end dates do *not* line up exactly — this
  assumption in the "Requires live verification" section below was wrong.**
  For every equity tested, the income statement's date set was a **subset**
  of the balance sheet's and cash flow's date sets (balance sheet/cash flow
  simply carry one or more extra trailing quarters income doesn't). This
  does not change the design: the plan's own extractor spec already builds
  "the union of period-end-date columns across the three statements" and
  leaves whichever named columns a given statement doesn't cover as `NULL`
  for that period — that logic handles a subset relationship exactly as
  well as an exact match. Only the "assumes they do, and outer-joins... or
  drift by a few days" framing in "Requires live verification" needed
  correcting; it isn't a few days' drift, it's a systematic
  income-has-fewer-quarters pattern.
- **ETF tickers (`CDZ.TO`) confirmed to return an empty `DataFrame`, not
  raise**, for all three of `quarterly_income_stmt`/
  `quarterly_balance_sheet`/`quarterly_cashflow`. Decision #6's assumption
  is correct as designed; no exception wrapping beyond the standard
  per-statement isolation is needed for the ETF-empty case itself.
- **Capital Expenditure sign convention confirmed negative-as-outflow.**
  Checked every quarter with all three figures present for NVDA/MCD/T:
  `Operating Cash Flow + Capital Expenditure == Free Cash Flow` exactly
  (e.g. NVDA 2026-04-30: `50344 + (-1757) = 48587`, matching the reported
  `Free Cash Flow` of `48587`). Confirms the plan's assumed fallback formula
  (`free_cash_flow = operating_cash_flow + capital_expenditure`) is
  addition, not subtraction, given capex's negative sign.
- **`Free Cash Flow` is present as a direct row for every equity tested** —
  NVDA, MCD, and T all reported it directly in `quarterly_cashflow`. The
  fallback computation path (`operating_cash_flow + capital_expenditure`
  when the direct row is absent) was **never exercised live** in this
  check — real yfinance data for these tickers always had the direct row.
  This means the fallback path must be exercised via a **synthetic
  fixture** in `tests/test_financial_snapshots_extractor.py` (already
  planned in the Tests section below), since it cannot be observed against
  real current data.
- **Bank balance-sheet edge case confirmed** (bonus check, `JPM`, not a
  held ticker but the clearest available real example): `Total Debt` and
  `Stockholders Equity` are both present (so `debt_to_equity` computes
  normally), but `Current Assets`/`Current Liabilities` are **absent**
  entirely from JPM's `quarterly_balance_sheet` index. `current_ratio` will
  therefore correctly resolve to `NULL` for bank-type tickers, exactly the
  intentional, documented behavior in the Risks section — not a bug to
  guard against further.
- **All named-column line-item aliases in the mapping table above matched
  real row labels exactly** for NVDA/MCD/T (`Total Revenue`, `Net Income`,
  `Diluted EPS`, `Gross Profit`, `Operating Income`, `Total Debt`,
  `Stockholders Equity`, `Current Assets`, `Current Liabilities`,
  `Free Cash Flow`). No alias-fallback label (e.g. `TotalRevenue`,
  `NetIncome`, `Basic EPS`) was exercised live — same as the FCF fallback,
  this means the alias-fallback branches need synthetic-fixture coverage in
  tests, not just the direct-label happy path.

## Fixed decisions

1. **Schema version bump: 12 → 13.** One new table (`financial_snapshots`),
   no changes to existing tables. `config.py`'s `DATABASE_SCHEMA_VERSION`
   becomes `13`.

2. **Hybrid schema: named columns for rubric-relevant fields, one JSON
   column for everything else.** Named, typed columns exist only for the
   fields the rubric's `derived:*` evidence actually scores against today
   (table above) plus `revenue`/`eps`/margins the task explicitly asked to
   have as first-class columns even though the rubric doesn't score EPS
   directly yet. Everything else goes into one `extra JSON` column shaped
   as:
   ```json
   {
     "income_statement": {"Research Development": 123.0, "SG&A": 45.0, ...},
     "balance_sheet": {"Total Assets": 999.0, "Total Liabilities": 500.0, ...},
     "cash_flow": {"Operating Cash Flow": 80.0, "Capital Expenditure": -12.0, ...}
   }
   ```
   containing every line item from the three quarterly statements **not**
   already consumed by a named column, JSON-encoded with the same
   NaN→`null`/Decimal→`float`/Timestamp→ISO-string conversion rules the
   archived `fetch_stock_research_data.py`'s `_json_value` already uses (so a
   bank's balance sheet, which has no "Current Assets"/"Current Liabilities"
   in the usual sense, simply leaves `current_ratio` `NULL` and carries
   whatever line items it does have into `extra` instead of forcing a
   schema-breaking special case). This avoids both a bloated schema (one
   column per possible line item across every industry) and a migration
   every time one more ratio becomes interesting — directly the design goal
   stated in the task.

3. **Update semantics: plain upsert on `(ticker_id, period_end_date)`, no
   delete-before-insert.** Financial statement figures, unlike
   `dividend_events`' immutable historical ex-dates, **can restate** after
   the fact: quarterly comparatives get revised in later filings
   (reclassifications, discontinued-operations restatement, purchase
   accounting adjustments, or occasionally a formal 10-K/A), and Yahoo's own
   third-party data vendor is separately known to correct/reprocess
   historical figures independent of any company action. This plan could not
   run a live before/after diff to catch a real restatement in this planning
   session (no network egress), but the *direction* of the reasoning is
   solid enough to fix now: `period_end_date` is never a forward-looking or
   speculative row the way `earnings_events`' future estimate rows are (a
   snapshot cannot exist before its period has ended and been reported), so
   there is nothing to "retire" the way `upload_earnings_events` retires
   abandoned speculative report dates. The correct and simplest match is
   `dividend_events`'s plain-upsert convention: `INSERT ... ON CONFLICT
   (ticker_id, period_end_date) DO UPDATE SET <every named column and extra>
   = excluded.<column>, fetched_at = now()`. If a quarter is ever restated,
   the next sync's upsert silently overwrites it in place with the corrected
   figures — which is the *desired* data-quality behavior (a persisted
   snapshot should reflect the best-known figures, not the first-ever-seen
   ones), and every prior sync's row is otherwise untouched, so this is
   still "append over time," never a bulk rewrite.

4. **Not date-windowed, like earnings/dividends.** yfinance's quarterly
   statement calls take no incremental start/end parameter in this repo's
   call sites; each fetch returns whatever trailing window yfinance
   currently exposes. There is no `--full`/incremental-start-date concept.
   Every sync run re-fetches each ticker's currently-available quarterly
   window and upserts it against what's already stored — accumulation over
   time (decision #3) is what builds depth, not a backfill parameter.

5. **Ticker scope: reuse `market_data.get_market_targets` unchanged** — same
   "owned + verified Yahoo provider mapping" resolution `yfinance-sync` and
   `earnings-dividends-sync` already use. No new ticker-scope logic.

6. **ETF handling: skip gracefully, not an error.** ETFs generally have no
   traditional income statement/balance sheet/cash flow in the equity sense.
   Mirroring the confirmed `earnings_dates` behavior for `CDZ.TO` (empty
   result, no exception, a misleading provider log line), this plan assumes
   `quarterly_income_stmt`/`quarterly_balance_sheet`/`quarterly_cashflow`
   likewise return an empty DataFrame (not raise) for fund tickers — **this
   specific assumption is unverified in this planning session** and is
   called out explicitly below as the first thing to confirm live before
   the extractor's exception-handling logic is considered final. If it
   instead raises for ETFs, the per-statement fetch must be wrapped the same
   way the archived script's `_safe_map` already isolates each of the six
   statement calls independently, so one raising statement never blocks the
   other two or other tickers.

7. **Not auto-wired into the ingestion pipeline** — mirrors decision #7 of
   the earnings/dividends plan exactly, for the same reason (quarterly
   financial data doesn't need to move in lockstep with every email-ingest
   run; ships as a standalone, on-demand CLI, `financial-snapshots-sync`).

8. **Reporting currency stored as-is, never converted.** Named dollar
   columns (`revenue`, `net_income`, `free_cash_flow`) are stored exactly as
   yfinance reports them, in the ticker's `financial_currency` (already
   tracked on `tickers.financial_currency`) — consistent with how
   `historical_records.close` and `etf_details.aum`/`nav` already store
   as-reported figures with no FX conversion; conversion is a downstream
   concern (`portfolio_metrics.py`), not this table's job.

9. **First-run context gets a separate, ephemeral annual-statement pull —
   not persisted here.** The quarterly window this table accumulates (5-7
   periods, confirmed live — see below) is roughly 1-1.75 years deep on a
   single sync; true multi-year trend only builds up over ~2 real years of
   recurring syncs (decision #4). That's fine for the ongoing dashboard/
   rubric use case, but insufficient for a brand-new ticker's *first*
   analysis pass, which needs a couple years of context to write an
   informed initial thesis. Fix: on a ticker's first-ever run only (no
   existing `stocks/TICKER.md` page — the same existence check the KB
   population system's first-run/incremental design already uses), the
   prep agent additionally fetches `Ticker.financials`/`balance_sheet`/
   `cashflow` (the **annual**, not quarterly, statements — already
   precedented via the archived `fetch_stock_research_data.py`'s
   `_fetch_financials`, which pulls both frequencies), and hands that
   directly to the analyst as one-time narrative context for the initial
   Company Overview/thesis. This is deliberately **not** persisted into
   `financial_snapshots` and does not widen its primary key — it's a
   transient input to the first write, not an ongoing data source. Every
   subsequent run for that ticker uses the quarterly table only, per
   decisions #1-8 above, unaffected by this addition.

## Decision #9 implementation spec — superseded location (2026-07-21)

**Update: the code location described in section 1 below has been
overridden.** Adithya's call: `fetch_annual_financial_context` and its CLI
entry point should live inside a proper skill
(`.claude/skills/bootstrap-stock-research/`), not in
`src/financial_snapshots_extractor.py` — "that's what it should have been
from the start." The `bootstrap-stock-research` skill's `SKILL.md` is
already written (documents the target architecture) but the code has not
actually been moved yet: as of this update,
`fetch_annual_financial_context`/`ANNUAL_CONTEXT_SCHEMA`/
`parse_annual_context_args`/`main_annual_context` are still sitting in
`src/financial_snapshots_extractor.py` exactly as section 1 originally
specified, and the skill's `scripts/` folder is empty. **This is the one
remaining inconsistency to close** — see
`docs/plans/kb-population-agent-rebuild.md`'s Step 0 for the concrete
migration steps. Section 1 below is kept for historical record of the
original reasoning (why it seemed right to share the quarterly file's
helpers directly) but no longer describes where the code should live.

### 1. Fetch function — original spec, now superseded (see update above)

Originally speced to live in the **same file** as the quarterly fetch, not
a new module or a skill `scripts/` directory. Rationale: it reuses six
existing private helpers verbatim (`_num`, `_line_item`,
`_extra_line_items`, `_json_safe`, `_safe_statement`, `_create_ticker`,
`_build_session`) and all eleven label-alias tuples (`REVENUE_LABELS`,
`NET_INCOME_LABELS`, etc.) already defined there. **Superseded**: the
migration to `bootstrap-stock-research` keeps this sharing intact by
importing those helpers from `src/financial_snapshots_extractor.py`
(`sys.path` insertion, same pattern `portfolio-classify` already uses to
import from `classify-portfolio`'s skill scripts) rather than duplicating
them — so the reuse rationale still holds, just via import instead of
same-file placement.

```python
def fetch_annual_financial_context(ticker: str) -> dict[str, Any]:
    """Fetch one ticker's ANNUAL financial statements as ephemeral,
    non-persisted first-run context for the KB prep agent.

    Unlike fetch_financial_snapshots, this is single-ticker (no batching/
    threading — called on-demand for exactly one new ticker at a time, not
    a scheduled multi-ticker sync) and returns a plain JSON-safe dict, not
    a DataFrame, since nothing downstream needs pandas -- the caller is an
    agent reading stdout JSON, not a database writer.
    """
```

Signature deliberately takes one `ticker: str`, not `Iterable[str]` —
matches the one-ticker-at-a-time calling pattern (see section 3).

Internals mirror `_fetch_one_financial_snapshots` almost exactly, with two
differences: (a) statement attributes are `financials`, `balance_sheet`,
`cashflow` (annual — no `quarterly_` prefix; these are the confirmed
correct yfinance 1.5.1 attribute names, already cited above and precedented
by the archived `fetch_stock_research_data.py`'s `_fetch_financials`), and
(b) the return shape is a dict, not a `pd.DataFrame` row list. Same
per-statement exception isolation via `_safe_statement`, same "all three
statements empty is normal for ETFs/funds, not an error" handling as the
quarterly path.

Output shape (`annual-financial-context.v1`):

```json
{
  "schema": "annual-financial-context.v1",
  "ticker": "AAPL",
  "generated_at": "2026-07-21T14:32:00Z",
  "period_count": 4,
  "periods": [
    {
      "period_end_date": "2022-09-24",
      "revenue": 394328000000.0,
      "net_income": 99803000000.0,
      "eps": 6.11,
      "gross_margin": 0.4331,
      "operating_margin": 0.3029,
      "debt_to_equity": 1.7961,
      "current_ratio": 0.8794,
      "free_cash_flow": 111443000000.0,
      "extra": {
        "income_statement": {"...": "..."},
        "balance_sheet": {"...": "..."},
        "cash_flow": {"...": "..."}
      }
    }
  ],
  "note": null
}
```

- `periods` sorted **ascending** by `period_end_date` (oldest first) —
  matches `fetch_financial_snapshots`'s existing sort direction, so the
  analyst reads both annual and quarterly data in the same chronological
  order with no extra convention to learn.
- Named fields are identical to `FINANCIAL_SNAPSHOT_COLUMNS`'s fields
  (minus `Ticker`/`ProviderSymbol`, which are hoisted to the top level
  instead of repeated per period) — same names, same units, same
  computation rules (gross/operating margin, debt-to-equity, current
  ratio, FCF fallback with the capex-sign convention already implemented).
  This is deliberate: the analyst should not have to learn a second set of
  field semantics for annual vs. quarterly data.
- Zero-period case (ETF/fund, or a fetch failure): `"periods": []`,
  `"period_count": 0`, `"note"` set to a short human-readable reason
  (`"No annual statements returned (expected for ETFs/funds)."` or
  `"Fetch failed; see logs."`) — never raises, never omits the `schema`/
  `ticker` keys, so the caller can always parse the envelope even on the
  empty path.

### 2. CLI wiring — `src/app.py` (three call sites, matching the existing
`financial-snapshots` fetch-only command's pattern exactly)

a. `_print_root_help`, alongside the existing `financial-snapshots`
   parser entry:
   ```python
   commands.add_parser(
       "annual-financial-context",
       help="Fetch one ticker's annual financial statements as ephemeral "
            "first-run KB context (not persisted).",
   )
   ```
b. `main()`'s delegated-command set (currently `{"statements", "email",
   "yfinance", "earnings-dividends", "financial-snapshots", "ticker-map",
   "import-activities", "portfolio-classify"}`): add
   `"annual-financial-context"`.
c. `_run_delegated_command`'s `delegated_commands` dict: add
   `"annual-financial-context": ("financial_snapshots_extractor",
   "main_annual_context")`.

### 3. New entry point — `financial_snapshots_extractor.py`

A second `parse_args`/`main` pair, distinct from the existing quarterly
ones (different arguments, different output format — JSON, not
`_print_frame`'s human-readable table):

```python
def parse_annual_context_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch one ticker's annual financial statements as "
                    "ephemeral, non-persisted first-run KB context."
    )
    parser.add_argument(
        "--ticker", required=True,
        help="Single provider (Yahoo) ticker symbol, e.g. AAPL or SHOP.TO.",
    )
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
    parser.add_argument("--ignore-proxy", action="store_true")
    return parser.parse_args(argv)


def main_annual_context(argv: list[str] | None = None) -> int:
    """CLI entry point invoked by the KB prep agent on a ticker's first-ever run."""
    args = parse_annual_context_args(argv)
    if args.ignore_proxy:
        clear_proxy_environment()
    configure_yfinance_cache(args.cache_dir)

    context = fetch_annual_financial_context(args.ticker)
    print(json.dumps(context, indent=2))
    return 0
```

`--ticker` (singular) deliberately breaks from the existing `--tickers
nargs="+"` convention used by every other command in this file — this is
the one command in the whole CLI that only ever operates on exactly one
ticker, so the singular flag makes that constraint visible at the call
site rather than silently accepting (and ignoring) a list.

Two new imports this file doesn't currently have: `json` (for
`json.dumps`) and `datetime`/`timezone` from the standard `datetime`
module (for the `generated_at` ISO timestamp) — easy to miss since
neither is used anywhere else in `financial_snapshots_extractor.py` today.

This satisfies decision #12's "one versioned-JSON stdout output per
invocation" rule directly: one call, one `schema`-tagged JSON document,
nothing split across multiple thin calls.

### 4. Where this fits in the (not-yet-built) prep agent

Documented here so the prep agent's eventual system prompt/skill can be
written directly from this spec without re-deriving it:

1. Orchestrator (or the prep agent itself) checks whether
   `Knowledge-Base/stocks/TICKER.md` exists — a plain file-existence
   check, no new tooling needed.
2. If it does **not** exist (first-ever run for this ticker): prep agent
   runs `uv run python src/app.py annual-financial-context --ticker
   <TICKER>` in addition to its normal quarterly/earnings/dividends reads,
   captures the JSON from stdout, and passes it through to the analyst
   step as extra context alongside the regular worksheet.
3. If it **does** exist (incremental run): this command is not called at
   all — the analyst uses only the persisted `financial_snapshots`/
   `earnings_events`/`dividend_events` tables, per decisions #1-8.
4. The analyst uses this context **only** for the initial Company
   Overview / Original Thesis narrative on a brand-new page — it is never
   referenced again after the first write, is not stored in
   `Knowledge-Base/` in any form, and does not feed the decision-rubric's
   scored dimensions (those remain sourced from `financial_snapshots` per
   the rubric's `sources:` registry, unchanged by this addition).
5. Consistent with decision #12 (agents never read result JSONs directly
   off disk): the wrapper script prints to stdout, the agent invoking it
   captures that output directly — no intermediate file.

### 5. Still requires live verification before implementation

Unlike the quarterly statements (fully live-verified against `NVDA`,
`MCD`, `T`, `CDZ.TO`, `JPM` — see "Confirmed via live test" above), the
**annual** properties (`Ticker.financials`, `Ticker.balance_sheet`,
`Ticker.cashflow`) have never actually been called live in this project —
their attribute names are confirmed correct by reading the installed
yfinance 1.5.1 source and by precedent in the archived
`fetch_stock_research_data.py`, but their *behavior* is not yet confirmed.
Specifically unverified:

- Actual period depth returned (assumed ~4-6 years based on the archived
  script's usage, per the earlier "Confirmed via live test" section, but
  never directly measured for `.financials`/`.balance_sheet`/`.cashflow`
  specifically, only for the `quarterly_*` variants).
- Whether `CDZ.TO` (or another owned ETF) returns an empty DataFrame for
  these three annual properties the same way it does for the quarterly
  ones — assumed by analogy, not confirmed.
- Whether the same line-item label aliases (`Total Revenue`, `Net Income`,
  etc.) apply identically to annual statements, or whether annual
  statements use different/additional labels not covered by the existing
  alias tuples.
- Whether the three annual statements' period-end dates line up with each
  other the same imperfect way the quarterly ones do (income statement a
  subset of balance sheet/cash flow), or differently.

**Recommended verification script**: extend
`verify_financial_snapshots_data.py` (already in the outputs folder) with
a second check block calling `ticker.financials`, `ticker.balance_sheet`,
`ticker.cashflow` (no `quarterly_` prefix) for the same `NVDA MCD T
CDZ.TO` sample, printing shape/index/columns exactly like the existing
`describe()` helper does — same script, same tickers, same output format,
just the other four (annual) properties instead of the three quarterly
ones. This should run before writing `fetch_annual_financial_context`,
not after, matching how the quarterly build was sequenced.

### 6. Tests

- **`tests/test_financial_snapshots_extractor.py`** additions, mirroring
  the existing `FinancialSnapshotsExtractorTest` cases but against
  `fetch_annual_financial_context`: named-column mapping, alias fallback,
  FCF computed fallback, extra-captures-unmapped-fields, per-statement
  exception isolation, empty-ticker/ETF case returns `{"periods": [],
  "period_count": 0, "note": "..."}` without raising, `parse_annual_context_args`
  requires `--ticker`, `main_annual_context` prints valid JSON matching
  the `annual-financial-context.v1` schema (assert via `json.loads` on
  captured stdout, not string matching).
- **`tests/test_app.py`** addition — mirror the existing
  `financial-snapshots` delegation test
  (`"financial-snapshots": "financial_snapshots_extractor.main"` at line
  381 and the command-list membership check at line 414): add
  `"annual-financial-context": "financial_snapshots_extractor.main_annual_context"`
  and add `"annual-financial-context"` to the command-list test.

### 7. Docs

- **`docs/reference/cli.md`** — new `## Annual Financial Context`
  subsection under the existing `## Financial Snapshots` section,
  documenting the command, its single-ticker constraint, its JSON output
  schema, and that it is a KB-agent tool, not a pipeline/dashboard data
  source (no `-sync` counterpart, nothing written to DuckDB).
- **`docs/architecture/database_schema.md`** — **not touched**. Nothing
  here is persisted; there is no schema change. Explicitly noting this so
  it isn't mistaken for an oversight during review.

### 8. Deliberately out of scope for this spec

- Any change to `financial_snapshots`'s schema or primary key (see
  "Deliberately deferred" below — already ruled out).
- Any change to the decision-rubric or `scoring_worksheet.py` — this
  context is narrative-only, never a scored input.
- Building the prep/analyst/orchestrator/writer agent files themselves —
  section 4 above specifies the contract those agents will call, but
  writing the agents is separate, tracked work per
  `kb-intake-rebuild-scope.md`.

## Requires live verification before implementation

~~Unlike the earnings/dividends plan, this one could not run a live check
against real held tickers during planning (no network egress in this
session).~~ **Done** — a live verification pass against `NVDA`, `MCD`, `T`,
`CDZ.TO` (plus a bonus `JPM` bank check) has since been run; see "Confirmed
via live test" above for full results. Status of each open question below:

- ~~How many trailing quarters `quarterly_income_stmt`/
  `quarterly_balance_sheet`/`quarterly_cashflow` actually return today~~
  **Resolved** — income statement returns 5, balance sheet/cash flow return
  6-7 (not a flat 4-5); see "Confirmed via live test."
- ~~Whether the three statements' period-end-date columns line up exactly
  per ticker~~ **Resolved, assumption corrected** — they do not line up
  exactly; income's date set is a systematic subset of balance
  sheet/cash flow's (not a few days' drift). The union/outer-join design in
  section 2 below already handles this correctly with no change needed.
- ~~Whether ETF tickers return an empty DataFrame or raise~~ **Resolved,
  confirmed empty, no exception** — decision #6 stands as designed.
- ~~The exact sign convention of `Capital Expenditure`~~ **Resolved,
  confirmed negative-as-outflow** — `free_cash_flow = operating_cash_flow +
  capital_expenditure` is the correct formula, verified against real
  figures for NVDA/MCD/T.
- ~~Whether `Free Cash Flow` is present as a direct row~~ **Resolved,
  present directly for every equity tested** — meaning the fallback
  computation path was never exercised live and must be covered by a
  synthetic fixture in tests instead (see "Confirmed via live test" and the
  Tests section).

## What will be built

### 1. Schema (`src/database.py`, `src/config.py`)

`config.py`: bump `DATABASE_SCHEMA_VERSION = 12` → `13`.

`database.py`: add `financial_snapshots` to `REQUIRED_TABLES`. Add a shared
helper (mirrors `_create_earnings_dividends_tables`, called from both
`_deploy_schema` and the migration):

```python
def _create_financial_snapshots_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the financial_snapshots table (per-quarter statement data).

    Shared between `_deploy_schema` (fresh installs) and the v12->v13
    migration so both paths stay in lockstep. Hybrid schema: named columns
    for the fields decision-rubric.yml's derived:* metrics actually score
    against today, plus one JSON `extra` column for every other line item
    yfinance's quarterly income statement/balance sheet/cash flow return, so
    a new ratio never requires its own migration. Figures are stored exactly
    as yfinance reports them in the ticker's financial_currency -- no FX
    conversion here (see analytics/portfolio_metrics for that).

    Unlike dividend_events/earnings_events, a row here can legitimately be
    overwritten in place on re-sync: quarterly figures can restate after the
    fact (reclassifications, discontinued-operations restatement, vendor
    data corrections), so ON CONFLICT DO UPDATE is the correct and desired
    behavior, not a bug -- a period_end_date is never a forward-looking or
    speculative row (a snapshot cannot exist before its period has ended and
    been reported), so there is nothing to retire the way
    upload_earnings_events retires abandoned speculative report dates.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_snapshots (
            ticker_id BIGINT NOT NULL,
            period_end_date DATE NOT NULL,
            revenue DECIMAL(24, 2),
            net_income DECIMAL(24, 2),
            eps DOUBLE,
            gross_margin DOUBLE,
            operating_margin DOUBLE,
            debt_to_equity DOUBLE,
            current_ratio DOUBLE,
            free_cash_flow DECIMAL(24, 2),
            extra JSON,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, period_end_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
```

- `_deploy_schema`: call `_create_financial_snapshots_table(connection)`
  right after the existing `_create_earnings_dividends_tables(connection)`
  call (before the `schema_metadata` INSERT).
- **Migration — precise edit, same pattern used for v10→v11→v12.** The
  current v11→v12 block in `initialize_database` is the terminal block: on
  success it calls `_create_trade_events_view(connection)` and `return
  False`, using `DATABASE_SCHEMA_VERSION` (currently `12`) as its target. To
  extend the ladder to v13:
  - In the existing `if row and row[0] == 11:` block: change
    `[DATABASE_SCHEMA_VERSION, SCHEMA_COMPONENT]` to hardcoded
    `[12, SCHEMA_COMPONENT]`; change the `logger.info` call to hardcode `12`
    instead of `%d`/`DATABASE_SCHEMA_VERSION`; **remove** the
    `_create_trade_events_view(connection)` + `return False` lines; **add**
    `row = (12,)` at the end instead.
  - Add a new terminal block immediately after:
    ```python
    if row and row[0] == 12:
        connection.execute("BEGIN TRANSACTION")
        try:
            _create_financial_snapshots_table(connection)
            connection.execute(
                "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                [DATABASE_SCHEMA_VERSION, SCHEMA_COMPONENT],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            logger.exception("Database migration from version 12 failed")
            raise
        logger.info("Database migrated from schema version 12 to %d", DATABASE_SCHEMA_VERSION)
        _create_trade_events_view(connection)
        return False
    ```
  This keeps every block's transaction/rollback/logging shape identical to
  the existing ladder and only ever uses `DATABASE_SCHEMA_VERSION` (not a
  hardcoded `13`) in the new terminal block.

### 2. New extractor: `src/financial_snapshots_extractor.py`

Mirrors `src/earnings_dividends_extractor.py`'s structure exactly, importing
the same reusable helpers from `yfinance_extractor.py`
(`_require_yfinance`, `_build_session`, `_create_ticker`, `_empty_frame`,
`_normalize_tickers`, `_print_frame`, `clear_proxy_environment`,
`configure_yfinance_cache`):

- `fetch_financial_snapshots(tickers) -> pd.DataFrame` — threaded
  (`ThreadPoolExecutor`, `YFINANCE_MAX_WORKERS` cap) per-ticker fetch of
  `client.quarterly_income_stmt`, `client.quarterly_balance_sheet`,
  `client.quarterly_cashflow`; per-ticker and per-statement failures
  isolated and logged, never raised (mirrors the archived script's
  `_safe_map` pattern of isolating each of the three calls independently).
  For each ticker, build the union of period-end-date columns across the
  three statements, and for each period-end date produce one row:
  `Ticker`, `ProviderSymbol`, `PeriodEndDate`, `Revenue`, `NetIncome`, `Eps`,
  `GrossMargin`, `OperatingMargin`, `DebtToEquity`, `CurrentRatio`,
  `FreeCashFlow`, `Extra` (a plain dict, JSON-encoded at upload time, not
  extractor time — mirrors how `upload_dividend_events`/
  `upload_earnings_events` do their own type conversion, keeping the
  extractor's output frame plain Python/pandas types). An ETF/fund ticker
  whose three statements are all empty produces zero rows for that ticker
  and is logged at info level as an expected outcome, not an error (pending
  live confirmation, see above).
- `main(argv)` — standalone/delegated CLI entry point mirroring
  `earnings_dividends_extractor.main`: `--tickers` (required), `--cache-dir`,
  `--ignore-proxy`; prints the frame via the reused `_print_frame` helper.
  Registered in `app.py`'s `delegated_commands` dict as
  `"financial-snapshots": ("financial_snapshots_extractor", "main")`.

### 3. Sync orchestration: `src/market_data.py`

New function `sync_financial_snapshots(db_path, symbols=None, *,
snapshots_fetcher=fetch_financial_snapshots) -> FinancialSnapshotsSyncResult`,
mirroring `sync_earnings_dividends`'s shape:
- Calls `initialize_database(db_path)`.
- Resolves scope via `get_market_targets(db_path, symbols)` (reused as-is).
- Fetches snapshots per resolved ticker set via the dependency-injected
  fetcher (tests never hit the network).
- Writes inside one `BEGIN TRANSACTION`/`COMMIT`/`ROLLBACK` block via the new
  `database_command.py` upload function.
- Returns `FinancialSnapshotsSyncResult(tickers, snapshot_rows, error,
  failed_symbols)`, matching `EarningsDividendsSyncResult`'s shape.
- Per-ticker fetch failures are isolated (logged, that ticker's rows simply
  absent), never abort the whole sync.

### 4. Upload function: `src/database_command.py`

- `upload_financial_snapshots(data, ticker_ids, db_path) -> int` — plain
  upsert loop over the fetched frame:
  ```sql
  INSERT INTO financial_snapshots (
      ticker_id, period_end_date, revenue, net_income, eps, gross_margin,
      operating_margin, debt_to_equity, current_ratio, free_cash_flow, extra
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT (ticker_id, period_end_date) DO UPDATE SET
      revenue = excluded.revenue,
      net_income = excluded.net_income,
      eps = excluded.eps,
      gross_margin = excluded.gross_margin,
      operating_margin = excluded.operating_margin,
      debt_to_equity = excluded.debt_to_equity,
      current_ratio = excluded.current_ratio,
      free_cash_flow = excluded.free_cash_flow,
      extra = excluded.extra,
      fetched_at = now()
  ```
  Same validation-and-raise style as `upload_dividend_events` (reject rows
  missing `ticker_id`/`period_end_date` rather than silently dropping them;
  every other named column is legitimately nullable per decision #2). `extra`
  is serialized via the existing `_json` helper.

### 5. CLI wiring: `src/app.py`

- Add `"financial-snapshots"` to `delegated_commands` (ad-hoc fetch+print).
- Add `_run_financial_snapshots_sync_command(argv)`, mirroring
  `_run_earnings_dividends_sync_command`: `--database`, `--tickers`
  (optional scope narrowing). Dispatch from `main()` via
  `if raw_args and raw_args[0] == "financial-snapshots-sync": return
  _run_financial_snapshots_sync_command(raw_args[1:])`, placed alongside the
  other explicit-dispatch commands — not the generic `delegated_commands`
  set, since it needs custom argument handling.
- Add both new subcommands to `_print_root_help`'s command catalog.
- Add `from market_data import sync_financial_snapshots` to the existing
  `market_data` import line alongside `sync_earnings_dividends`,
  `sync_market_data`.

### 6. Documentation updates (same change, per `CLAUDE.md`'s doc rule)

- `docs/reference/cli.md` — new `## Financial Snapshots` section mirroring
  the existing `## Earnings & Dividends` structure: the ad-hoc
  `financial-snapshots` command and its flags, then
  `financial-snapshots-sync` and its flags/behavior (ticker scope,
  plain-upsert/restatement-overwrite semantics, ETF empty-result note,
  "not auto-triggered by the pipeline" note).
- `docs/architecture/database_schema.md` — new section (mirrors "## Earnings
  & Dividends Calendars") describing `financial_snapshots`: the hybrid
  named-columns-plus-`extra`-JSON design and why, the plain-upsert/
  restatement reasoning (contrasted explicitly with `dividend_events`'
  immutable ex-dates and `earnings_events`' speculative-row cleanup), that
  it only accumulates true trend depth over repeated syncs (yfinance's own
  window is shallow), the as-reported-currency/no-FX-conversion note, and a
  short forward-reference noting that a future rubric change (via
  `author-decision-rubric`) may consume this table for trend-aware
  `derived:*` scoring — explicitly out of scope for this change.

## Tests

Following `CLAUDE.md`'s Testing Guidelines (`unittest`, deterministic
fixtures, network always mocked) and mirroring the real, current test files
for the earnings/dividends feature:

- **New `tests/test_financial_snapshots_extractor.py`** (mirrors
  `tests/test_earnings_dividends_extractor.py`) — a `FakeTicker`/
  `FakeYFinance` pair exposing `quarterly_income_stmt`/
  `quarterly_balance_sheet`/`quarterly_cashflow` as synthetic DataFrames;
  verify: named-column mapping and its label-alias fallback logic (e.g. a
  fixture using `NetIncome` instead of `Net Income` still maps); computed
  fields (`gross_margin`, `operating_margin`, `debt_to_equity`,
  `current_ratio`, and both the direct-row and computed-fallback paths for
  `free_cash_flow`, including a fixture proving the capex sign convention
  once confirmed live); `extra` capturing every unmapped line item and
  nothing else; a period present in only one of the three statements still
  produces a row with the other named columns `NULL`; an all-empty-statement
  ticker (ETF stand-in) produces zero rows without error; per-ticker
  exception isolation (one ticker/one statement raising doesn't drop
  others); empty-input handling.
- **`tests/test_market_data.py`** additions — `sync_financial_snapshots`
  tests mirroring the existing `sync_earnings_dividends` tests: ticker scope
  from `get_market_targets` (patched), injected fetcher, transaction
  begin/commit assertions, per-ticker fetch-failure isolation, write-failure
  rollback.
- **`tests/test_database_command.py`** additions —
  `upload_financial_snapshots` upsert-on-conflict behavior: re-upload the
  same `(ticker_id, period_end_date)` with *different* figures updates the
  row in place (simulating a restatement) rather than duplicating or
  erroring; rows missing `ticker_id`/`period_end_date` are rejected;
  `extra` round-trips through DuckDB's `JSON` type correctly.
- **`tests/test_database.py`** addition —
  `test_version_twelve_schema_is_upgraded_and_adds_financial_snapshots_table`,
  mirroring `test_version_eleven_schema_is_upgraded_and_adds_earnings_dividends_tables`:
  seed a v12 database, drop `financial_snapshots`, force
  `schema_metadata.schema_version = 12`, call `initialize_database`, assert
  version is now `DATABASE_SCHEMA_VERSION`, the table exists and is
  writable, `is_database_active` is true, and `v_trade_events` is still
  queryable (regression check on the terminal-block edit).
- **`tests/test_app.py`** additions — mirror the existing
  `earnings-dividends`/`earnings-dividends-sync` argument-forwarding and
  delegation tests for `financial-snapshots`/`financial-snapshots-sync`.

## Implementation order

1. ~~Live-verify the open questions~~ **Done** — see "Confirmed via live test."
2. ~~`config.py` version bump + `database.py` schema/migration + its test~~
   **Done** — verified `DATABASE_SCHEMA_VERSION = 13`, migration test passes.
3. ~~`src/financial_snapshots_extractor.py` + its tests~~ **Done** — 11 tests
   passing.
4. ~~`database_command.py::upload_financial_snapshots` + its tests~~ **Done**
   — 5 tests passing, including the restatement-overwrite case.
5. ~~`market_data.py::sync_financial_snapshots` + its tests~~ **Done** — 4
   tests passing.
6. ~~`app.py` CLI wiring (both commands) + its tests~~ **Done** — 2 tests
   passing.
7. ~~`docs/reference/cli.md` and `docs/architecture/database_schema.md`
   updates~~ **Done** — both sections present.
8. **Not yet done**: decision #9's ephemeral first-run annual-statement
   pull. This is the one remaining implementation step.

## Verification

1. `uv run python -m unittest discover -s tests` — full suite green,
   including the new migration test.
2. Against a scratch copy of the real database file (never the live one):
   run `financial-snapshots-sync`, confirm `schema_metadata.schema_version`
   reads `13`, the table is populated for owned equity tickers and empty
   (not errored) for any owned ETF, re-run confirms upsert behavior (row
   counts stable for unchanged quarters, `fetched_at` advances, no
   duplicates).
3. Manually inspect one real ticker's `extra` JSON blob to confirm it
   contains the expected leftover line items and none of the named-column
   fields duplicated.
4. Confirm `uv run python src/app.py --help` lists both new commands.

## Deliberately deferred

- Any dashboard view — already tracked separately in `docs/project/todo.md`
  Backlog against this exact table name.
- Any change to `Knowledge-Base/taxonomy/decision-rubric.yml`,
  `scoring_worksheet.py`, or `validate_recommendation.py` to make the
  `derived:*` financial metrics actually read from `financial_snapshots`
  instead of a fresh per-run yfinance pull — this plan only makes the data
  persistently queryable; wiring the rubric to consume it is a separate
  change through `author-decision-rubric`.
- **Persisted** annual (as opposed to quarterly) statement periodicity.
  Only quarterly periods are persisted in `financial_snapshots` — see
  decision #9 for the separate, ephemeral, first-run-only annual pull,
  which is in scope but intentionally not stored in this table. If
  *persisted* annual snapshots are wanted later, the primary key would need
  to widen from `(ticker_id, period_end_date)` to `(ticker_id,
  period_end_date, period_type)`, since a company's fiscal-year Q4
  quarter-end date can coincide with its annual period-end date — not
  attempted here to avoid guessing at an unneeded design now.
- Auto-triggering this sync after the email pipeline — ships as an
  on-demand command only, same as earnings/dividends.
- Deriving a human-readable fiscal-quarter label (e.g. "Q1 2026") from
  `period_end_date` — plausible future read-side convenience, not stored
  here to avoid getting a company's non-calendar fiscal year wrong.

## Risks

- **The migration terminal-block edit is the sharp edge**, exactly as in the
  earnings/dividends plan — get the v11→v12 block's edit wrong and the
  ladder either silently stops at v12 forever or a fresh v11 database
  double-creates the view/skips a step. The new migration test is the
  primary guardrail.
- ~~This plan's field-mapping/exception-handling design was not live-tested
  against real yfinance responses~~ **Resolved** — a live verification pass
  (`NVDA`, `MCD`, `T`, `CDZ.TO`, plus a bonus `JPM` bank check) has since
  confirmed quarterly window depth, ETF empty-vs-raise behavior, capex sign
  convention, and corrected the statement period-end-date alignment
  assumption (subset, not exact match — no design change needed). See
  "Confirmed via live test" above. The one residual gap: the FCF fallback
  path and alias-fallback label branches were not exercised live (real data
  always hit the primary label/direct-row path), so those branches rely on
  synthetic test fixtures rather than live confirmation — normal and
  expected for a rarely-hit fallback branch.
- **yfinance's shallow rolling window means this table only slowly builds
  real trend depth** — a single sync does not backfill years of history;
  this must be communicated clearly in the docs so it isn't mistaken for an
  instant deep-history import.
- **Restatement-driven overwrite is a deliberate, not accidental, design
  choice** — if it later turns out yfinance's quarterly data never actually
  restates in practice, the plain-upsert design is still correct (it simply
  never triggers the update path in practice), so getting this wrong in
  either direction has low downside; the risk is solely in future readers
  assuming every stored row is immutable when it is not.
- **Industry variance in named-column coverage** (a bank's balance sheet
  lacks a conventional current-ratio split) means `debt_to_equity`/
  `current_ratio`/margins will legitimately be `NULL` for some sectors —
  this is intentional per the hybrid design, not a bug, but should be
  called out in the docs so a future consumer doesn't treat `NULL` as a
  fetch failure.
