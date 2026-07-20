# Dashboard API

The FastAPI backend for the hosted dashboard — Phase 3 of the
[development roadmap](../plans/goals/development-roadmap.md) ("minimal hosted
view"), serving the [project vision](../plans/goals/project-vision.md)'s
requirement of "open one place from my phone, anywhere, anytime." It is a thin
wrapper over `src/analytics.py` (documented in
[analytics calculations and sources](../reference/analytics.md)): every
endpoint calls a function that already exists in the pipeline and converts its
return value to JSON. There is no new financial math in the API layer, so the
dashboard can never diverge from what the CLI reports.

```
 pipeline (src/app.py)          dashboard backend              dashboard frontend
┌──────────────────────┐      ┌──────────────────────┐      ┌──────────────────────┐
│ email / statement /  │      │ FastAPI              │      │ Next.js + shadcn/ui  │
│ yfinance ingestion   │ ───► │ dashboard/api/main.py│ ───► │ dashboard/web/       │
│ writes DuckDB        │ DB   │ :8000, GET data +    │ JSON │ :3000                │
│ Data/*.duckdb        │ read │ guarded POST actions │      │ (server + client)    │
└──────────────────────┘  ▲   └──────────────────────┘      └──────────────────────┘
                          └── actions shell back out to src/app.py
```

## Design constraints

- **Read-only data surface.** Every `GET` endpoint only reads; none of them
  writes the database. The sole exception is the guarded `POST /api/actions/*`
  namespace ([below](#website-initiated-actions)), which does not write
  directly either — it runs the same CLI commands as a subprocess.
  If the DuckDB file does not exist at startup, the process raises and exits
  instead of letting `database.get_shared_connection` silently create an
  empty database.
- **Pipeline-sourced data only.** Prices, positions, and cash come from the
  last pipeline run. The API does not fetch live market data; the one network
  touch is the benchmark history fetch inside `analytics.portfolio_report`
  (yfinance, for alpha/beta/tracking error), which degrades that section to
  unavailable on any failure rather than failing the request.
- **`src/analytics.py` is the single source of truth.** The API converts
  `Decimal`/`date`/dataclass values to JSON types (`to_jsonable`) and adds
  nothing else.

## Endpoints

| Endpoint | Serves | Backing function | Empty/missing behavior |
| --- | --- | --- | --- |
| `GET /health` | Status + database path/existence | `config.DATABASE_PATH` | Always 200; `database_exists: false` when missing |
| `GET /api/portfolio/summary` | Portfolio value, cash, holding count | `analytics.get_portfolio_summary` | Zeros, 200 |
| `GET /api/portfolio/holdings` | All holdings with portfolio weights, sorted by value | `analytics.get_holdings` | `[]`, 200 (weight guard: no division by zero) |
| `GET /api/portfolio/allocation` | Classifier-group and geography breakdown | `analytics.get_group_allocation` | `{}`, 200 |
| `GET /api/portfolio/trend` | Chronological portfolio value series | `analytics.get_historical_portfolio_values` | `[]`, 200 |
| `GET /api/portfolio/report` | Full analytics report (see below) | `analytics.portfolio_report` | 503 with remediation detail when the database file is missing |
| `GET /api/portfolio/classifications` | Per-holding classification detail (group, confidence, reasoning, evidence, review flag, enriched fields) | `analytics.get_classification_details` | `{generated_at: null, count: 0, review_count: 0, classifications: []}`, 200 |
| `GET /api/etfs/overlap` | Share of the ETF sleeve held in underlying names more than one fund reports, plus per-pair overlap | `analytics.get_etf_overlap` | `{available: false, reason: ...}`, 200 |
| `GET /api/stocks/{symbol}/history?range=` | One ticker's daily close series (`range` = `1m`/`3m`/`6m`/`1y`/`3y`/`max`) | `analytics.get_price_history` | 404 for unknown symbol; 422 for invalid range |

## ETF overlap, and why it is a floor

`GET /api/etfs/overlap` answers "how much of my ETF money is buying the same
companies twice". It reads each fund's `top_holdings` from
`portfolio_classifications.fields` and scales every position by that fund's
share of the ETF sleeve, so the three returned shares — `overlapping_weight`
(names at least two funds report), `unique_weight` (names only one fund
reports), `unreported_weight` (the rest of each fund) — always sum to 1.

Two limits are baked into the number and surfaced to the UI as `basis` and
`caveat`:

- Providers publish only each fund's **largest** positions (usually ten), so
  funds can overlap further down their books than either one reports. The
  result is a floor on true overlap, never an upper bound.
- Those records store a **display name and weight but no ticker symbol**, so
  matching is by normalized name (`_normalize_holding_name` strips a leading
  "The" and trailing legal-form tokens, and deliberately keeps share-class
  letters so Class A and Class B stay distinct).

`pairs[].overlap_pct` is an overlap coefficient: for each shared name it takes
the smaller of the two funds' weights and sums them. `available` is false with
a `reason` when fewer than two funds report holdings.

## The report endpoint and the visual map

`GET /api/portfolio/report` recomputes the full analytics report — the same
shape the CLI's `analytics --export` writes to
`exports/analytics/portfolio-analytics.json` — cached against the DuckDB
file's modification time. The data only changes when the pipeline writes the
database, so the report is rebuilt at most once per pipeline run (a few
seconds), and every other request is served from memory. No manual export
step is needed, and the dashboard is never stale relative to the pipeline.
The response wraps the report with freshness metadata:

```json
{ "generated_at": "...", "database_mtime": "...", "report": { ... } }
```

Every planned dashboard visual is a JSON-path lookup into `report`:

| Visuals | Report path |
| --- | --- |
| Allocation pies: group / sector / currency / geography | `allocation.by_group`, `.by_sector`, `.by_currency`, `.by_geography` |
| Look-through sector exposure | `allocation.look_through_sector` |
| Treemap / top holdings | `allocation.by_ticker` + `holdings` (each holding row carries `weight`, its fraction of portfolio value — also used by the ETF table and holding-detail KPI) |
| Concentration gauge / HHI / effective holdings / top-N weights / limit breach | `allocation.concentration` (single-name breach is computed over non-exempt tickers; broad-market Core ETFs are listed in `exempt_tickers`, with `max_flagged_name_ticker`/`_weight` naming the largest non-exempt holding) |
| Target vs actual allocation | `targets` |
| Portfolio value trend | `performance.historical_values` |
| Trend overlays: cumulative net deposits + XEQT / S&P 500 benchmark lines | `performance.trend_overlays` — `points[].net_deposits_cum` and `points[].benchmarks.{XEQT,SP500}` are raw CAD closes aligned to the valuation date grid (forward-filled, `null` before inception); the frontend re-anchors each benchmark to the portfolio value at the first visible point so toggling 3M/1Y/Max re-normalizes. Benchmarks come from `historical_records` rows persisted by `market_data.ensure_benchmark_history` (pipeline-side fetch, not the API) |
| Drawdown chart; Sharpe / Sortino / volatility / total-return tiles | `performance.adjusted_returns.*` |
| Money-weighted return (XIRR) | `performance.money_weighted` |
| Alpha / beta / information ratio vs benchmark | `performance.benchmark` |
| Monthly dividend bars / TTM income / yield gauges / dividend growth | `income.*` |
| Fee breakdown / fee drag | `fees.*` |
| Turnover / holding period | `activity.*` |
| Realized gains | `realized_gains` |
| Data-quality flags dashboard | `data_quality` + `unavailable_metrics` |

Rule for growth: a new visual reads a section of `/api/portfolio/report`
first. A dedicated endpoint is added only when a visual needs parameters or
interactive recomputation, and it must wrap an existing `src/analytics.py`
function. `GET /api/portfolio/classifications` and
`GET /api/stocks/{symbol}/history` are the first two such endpoints:
per-holding classification detail is not in the report at all (the report
only carries aggregate group/geography weights), and the per-ticker price
series is parameterized by symbol and range — exactly the documented
exception. Both wrap read-only analytics functions and touch no network.

`get_classification_details` also normalizes the classifier's `fields` JSON:
`sector_weights` and `top_holdings` are stored double-encoded (a JSON string
inside the fields JSON), so the endpoint parses those nested values into real
objects rather than returning them as strings.

## Frontend (`dashboard/web`)

A Next.js (App Router) + shadcn/ui app consumes the API. Pages are server
components that fetch on the Next server (no CORS); charts, tables, and the
price explorer are client components. The one browser-side fetch is the price
explorer hitting `/api/stocks/{symbol}/history` (covered by the CORS
allowlist). All fetches use `cache: "no-store"` — the API's mtime cache is the
caching layer. Every data page is `force-dynamic` (always reflects the last
pipeline run). See `dashboard/web/README.md` for setup.

Pages and their primary data source:

| Route | Shows | Source |
| --- | --- | --- |
| `/` Overview | Value/book-cost/unrealized/cash KPIs, value trend with net-deposit + benchmark overlay toggles, sector donut, group cards vs target | `report.summary`, `.performance.historical_values`, `.performance.trend_overlays`, `.allocation`, `.targets` (+ classifications for per-group top holding) |
| `/portfolio` | Tabs: Allocation (target-vs-actual, treemap, look-through, currency/geography, concentration), Performance (return/risk tiles, drawdown, benchmark), Costs & Activity (fees, turnover, realized) | `report.allocation`, `.performance`, `.fees`, `.activity`, `.realized_gains` |
| `/stocks` | Sortable stock table + price explorer with benchmark compare | `report.holdings` (stocks) ⋈ `classifications`; `/api/stocks/{symbol}/history` |
| `/etfs` | Blended MER, ETF table, fund overlap, underlying sector mix | `report.holdings` (ETFs) ⋈ `classifications` (`fields.expense_ratio`, `aum`, `sector_weights`); `/api/etfs/overlap` |
| `/income` | Yield KPIs, 36-month dividend bars + rolling average | `report.income` |
| `/data-quality` | Severity summary, pipeline action buttons, flags table, unresolved symbols, provisional holdings, review-needed classifications (each with a Classify action), unavailable metrics | `report.data_quality`, `.unavailable_metrics`, `classifications`, `/api/tickers/pending`, `/health` |
| `/holdings/[symbol]` | Shared stock/ETF detail: KPIs, price chart, classification, facts | `report.holdings` ⋈ `classifications`; `/api/stocks/{symbol}/history` |

The frontend never computes portfolio math — it formats API values and does
only presentation-layer derivations (blended MER, index-to-100 compare,
group→color mapping) in `dashboard/web/src/lib/derive.ts`.

## Website-initiated actions

`POST /api/actions/*` is the only part of the API that changes state. It
exists so the fixes the dashboard already surfaces — a holding the classifier
could not place, a symbol it could not map, data that needs a re-run — can be
applied from the page that reports them. **Every action has a CLI equivalent
and the CLI remains canonical**; these endpoints shell out to it rather than
reimplementing anything.

| Endpoint | Does | CLI equivalent |
| --- | --- | --- |
| `POST /api/actions/classify` | Re-run classification and sync it into DuckDB | `app.py portfolio-classify` + `classification-sync` |
| `POST /api/actions/refresh` | Run the full ingestion pipeline | `app.py pipeline` |
| `POST /api/actions/overrides` | Pin a ticker to a group, then re-classify | edit `ref/manual_overrides_v1_1.yaml`, then the two commands above |
| `POST /api/actions/resolve-ticker` | Map a pending source symbol to a verified symbol | `app.py resolve-tickers` (interactive) |
| `GET /api/tickers/pending` | Source symbols still blocking ingestion | `app.py ticker-map pending` |
| `GET /api/actions` / `GET /api/actions/{job_id}` | Recent jobs / one job's status | — |

### Constraints this surface honors

- **Auth gate first.** `require_action_auth` runs on every action. With
  `DASHBOARD_ACTION_TOKEN` set it requires a matching bearer token (the mode
  to use if the API is ever bound to a non-loopback interface); unset, it
  accepts loopback callers only. The GET surface is unauthenticated as before.
- **Never blocks.** Actions return `202` with a job (`dashboard/api/jobs.py`)
  that the frontend polls. `409` means another job is already running.
- **One writer.** DuckDB permits a single read-write process. A single-worker
  executor plus the one-job-at-a-time rule keeps actions serialized, `_db_lock`
  serializes the API's own database use, and a writer subprocess only starts
  after `database.close_connection()` releases the file. **Run one uvicorn
  worker** — job state is in-process.
- **CORS** `allow_methods` is `["GET", "POST"]`; POST is only routed under
  `/api/actions/*`.
- **No new cache plumbing.** A writer rewrites the DuckDB file, whose mtime
  invalidates the report cache automatically.

### Writing the override YAML

`POST /api/actions/overrides` is the one action that edits a
`Knowledge-Base/ref/*.yaml` reference file. `src/manual_overrides.py` validates
the group against `approved_groups` (rejecting `Cash`/`Needs Review`), requires
a rationale, splices the entry as text so the rest of the hand-curated document
stays byte-identical, re-parses before committing, and replaces the file
atomically. This is an **owner-initiated** edit that happens to arrive over
HTTP — distinct from the CLAUDE.md rule that knowledge-base *agents* never
edit `ref/*.yaml`.

Still unbuilt: `POST /api/actions/evaluate/{ticker}` (the stock-data-prep →
stock-analyst flow), which would follow the same job model.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_PATH` | `Data/PRD_WealthSimple.duckdb` | DuckDB file to serve (via `src/config.py`) |
| `DASHBOARD_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed browser origins |
| `DASHBOARD_ACTION_TOKEN` | unset | Bearer token required for `POST /api/actions/*`; unset means loopback-only |

The frontend sends that token as `NEXT_PUBLIC_ACTION_TOKEN` when it is set.

## Running locally

```powershell
uv run uvicorn main:app --reload --port 8000 --app-dir dashboard/api
```

Dependencies (`fastapi`, `uvicorn[standard]`) are declared in the repo's
`pyproject.toml` and installed by `uv sync`.

## Testing

`tests/test_dashboard_api.py` covers the wrapper layer with all database
access mocked — the analytics functions themselves are tested against real
temporary DuckDB files in `tests/test_analytics.py`, so the API tests assert
serialization, response shapes, empty-portfolio behavior, CORS parsing, the
report cache (mtime keying), and startup fail-fast. Endpoint tests construct
`TestClient(app)` without a context manager so the lifespan database check is
skipped; only the startup tests use `with TestClient(app)` to exercise it.
