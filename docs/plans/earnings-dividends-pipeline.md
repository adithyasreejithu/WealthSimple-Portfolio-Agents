# Earnings and Dividends Pipeline

*Status: **proposed — awaiting approval** (2026-07-21). This plan records the
design for a new company-declared earnings/dividend data pull. It moves to
`docs/plans/implementation/` in as-built form once executed.*

## Context

The pipeline currently pulls security metadata and OHLCV history from
yfinance (`src/yfinance_extractor.py`, orchestrated by `src/market_data.py`'s
`sync_market_data` / the `yfinance-sync` CLI command) for tickers the
portfolio actually owns. It has no equivalent pull for **company-declared
earnings and dividend data** — the earnings calendar (report dates, EPS
estimates/actuals, surprise%) and the dividend schedule (ex-dividend dates,
declared per-share amounts). Two downstream consumers need this once it
exists in the database:

1. A future dashboard view (out of scope here — dashboard work is tracked
   separately; this plan only makes the data available to read via DuckDB).
2. A knowledge-base agent system being designed separately (also out of
   scope here — it just needs to be able to read the tables).

This plan covers only the extraction + storage half: a new extractor module,
new tables, a schema migration, a new CLI command pair, and tests.

### Not the same thing as `cash_transactions`

`Knowledge-Base/dividends/index.md` and `Knowledge-Base/earnings/index.md`
already exist as *hand-curated wiki note* indexes (dividend safety notes,
earnings write-ups) — unrelated to this plan; they are prose pages, not
structured market data, and are out of scope here.

More importantly: `cash_transactions` / `transactions` already record the
user's **actual received dividend cash** from brokerage statements/activity
exports. The new `dividend_events` table is a completely different thing —
the **company's declared dividend schedule** (market data, independent of
whether the user owned the stock on that date). These must not be conflated,
joined implicitly, or merged. `dividend_events` has no relationship to
position/holdings math; it is reference data.

### Readiness assessment: buildable now

- yfinance access, session/cache setup, ticker resolution, and the
  ownership-scoped ticker-list pattern all already exist and are directly
  reusable (`src/yfinance_extractor.py`, `src/market_data.py`).
- The exact yfinance calls needed (`Ticker.calendar`, `Ticker.earnings_dates`,
  `Ticker.dividends`) are already used by this repo today in the archived
  `.claude/skills/_archive/fetch-stock-research-data/scripts/fetch_stock_research_data.py`
  (`_fetch_earnings`, `_fetch_dividends`) — reuse that field knowledge rather
  than inventing new calls.
- `src/database.py`'s migration ladder has a clean, well-established pattern
  (a shared `_create_*_tables` helper called from both `_deploy_schema` and
  the newest migration block) that a new version bump slots into directly.
- No new dependency required.

### yfinance field mapping (verified against the archived script + yfinance's actual data shape)

| Source call | What it returns | Used for |
|---|---|---|
| `Ticker.earnings_dates` | DataFrame indexed by report date; columns `EPS Estimate`, `Reported EPS`, `Surprise(%)`. Yahoo returns a fixed window covering recent past reported quarters **and** upcoming estimated dates (no actual yet for future rows) | `earnings_events`: `report_date`, `eps_estimate`, `eps_actual`, `surprise_pct` |
| `Ticker.calendar` | dict with next `Earnings Date`, `Revenue High/Low/Average`, `Dividend Date`, `Ex-Dividend Date` — only ever describes the **single next** event, never historical rows | Not used in v1 (see decisions below) |
| `Ticker.dividends` | pandas Series indexed by **ex-dividend date**, value = actual per-share cash amount declared/paid historically. This is confirmed, backward-looking data only — no forward-looking "next payment" row | `dividend_events`: `ex_dividend_date`, `declared_amount` |

Two fields from the task's proposed schema do not have a reliable yfinance
source and are deliberately scoped out of v1 rather than half-populated (see
Fixed decisions #2 and #3 below): `earnings_events.period`,
`earnings_events.revenue_estimate`/`revenue_actual`, and
`dividend_events.pay_date`/`frequency`. All remain as nullable columns for a
future enhancement to backfill from a richer source; v1 leaves them `NULL`
rather than guessing.

### Confirmed via live test against real held tickers (2026-07-21)

Run against `NVDA`, `MCD`, `T`, `CDZ.TO` (pulled from the actual
`tickers`/`ticker_provider_mappings` tables) using the same `curl_cffi`
session pattern `yfinance_extractor.py` already uses:

- **`Surprise(%)` units resolved** — values observed: `5.54, 5.32, 3.46,
  -3.36, 11.58, -0.90`. These are already percentage points (`5.54` means
  `5.54%`), not a fraction. `surprise_pct DOUBLE` stores the value as
  returned, no conversion needed. This closes the "unverified units" risk
  below.
- **`earnings_dates` row count is 25** for every individual-stock ticker
  tested (not the ~12 estimated earlier from reading the archived script —
  corrected here from live behavior). Exactly one trailing row is null
  (`Reported EPS`/`Surprise(%)` both `NaN`) — the single upcoming,
  not-yet-reported estimate. This is the exact shape Fixed decision #5's
  speculative-row cleanup logic was designed around; confirmed correct.
- **ETFs legitimately return empty `earnings_dates`.** `CDZ.TO` (a held
  dividend ETF) returned an empty/None `earnings_dates` with yfinance
  printing `"No earnings dates found, symbol may be delisted"` to
  stdout/stderr — misleading wording, but no exception is raised and the
  ticker is not actually delisted; ETFs simply don't have earnings events.
  The extractor must treat an empty `earnings_dates` result as a normal,
  expected outcome for fund tickers, not a fetch failure to flag loudly —
  worth a one-line note in `fetch_earnings_events` so this doesn't get
  miscategorized as an error in logs.
- **`dividends` works cleanly for both stocks and ETFs** — `CDZ.TO` returned
  233 rows with no issues; no special-casing needed there.

## Fixed decisions

1. **Schema version bump: 11 → 12.** Two new tables (`earnings_events`,
   `dividend_events`), no changes to existing tables. `config.py`'s
   `DATABASE_SCHEMA_VERSION` becomes `12`.

2. **`earnings_events` scope in v1**: populate `ticker_id`, `report_date`,
   `eps_estimate`, `eps_actual`, `surprise_pct`, `fetched_at` from
   `Ticker.earnings_dates` exactly as the archived script already calls it.
   Leave `period` and `revenue_estimate`/`revenue_actual` `NULL` always —
   `earnings_dates` carries no revenue fields, and `calendar`'s revenue
   figures only ever describe the single next quarter (no historical
   coverage, no `report_date`-keyed history), so merging it in would
   populate at most one row per ticker inconsistently. Columns stay in the
   schema (nullable) for a future dedicated revenue source.

3. **`dividend_events` scope in v1**: populate `ticker_id`,
   `ex_dividend_date`, `declared_amount`, `fetched_at` from `Ticker.dividends`
   only — this is genuinely "declared/paid" data (an ex-date already priced
   in by the market), matching the column's `NOT NULL` semantics honestly.
   Leave `pay_date` and `frequency` `NULL` always in v1: `dividends()` does
   not carry pay date, and `calendar`'s `Dividend Date` only ever covers the
   single next payment (not historical), so partial coverage isn't worth the
   added fetch/match complexity. Do **not** ingest `calendar`'s
   forward-looking, model-estimated next-dividend guess as a row — v1 only
   stores confirmed historical ex-dividend events, keeping `declared_amount`
   an honest "this actually happened" value, not a projection.

4. **Not date-windowed like OHLCV.** Unlike `fetch_security_history`
   (`yfinance.download(start=, end=)`), neither `earnings_dates` nor
   `dividends` takes an incremental date range in this repo's existing call
   sites — each fetch returns yfinance's full available window. So there is
   no `--full`/incremental-start-date concept here; every sync run re-fetches
   each ticker's available earnings/dividend data in full from yfinance.
   "Refresh behavior" is entirely about how that freshly fetched snapshot
   reconciles against what's already stored (decision #5).

5. **Refresh behavior: upsert with speculative-row cleanup for earnings, plain upsert for dividends.**
   - `dividend_events`: `INSERT ... ON CONFLICT (ticker_id, ex_dividend_date) DO UPDATE SET declared_amount = excluded.declared_amount, fetched_at = CURRENT_TIMESTAMP` — matches the existing `historical_records` upsert convention in `database_command.py::upload_security_history`. No deletes needed; v1 never ingests speculative dividend rows, so there's nothing to retire.
   - `earnings_events`: same upsert on `(ticker_id, report_date)`, **plus** before inserting each ticker's freshly fetched batch, delete any *existing, still-unreported* rows for that ticker at future dates that are about to be superseded: `DELETE FROM earnings_events WHERE ticker_id = ? AND report_date >= CURRENT_DATE AND eps_actual IS NULL`, then insert/upsert the fresh batch. This handles the case the task raised explicitly — an estimate that later gets an actual reported value updates the existing row via `ON CONFLICT DO UPDATE` (same PK) rather than duplicating; it also retires an abandoned speculative report date (e.g. yfinance revises its guessed date) instead of leaving an orphaned stale-estimate row forever. Confirmed past-reported rows are never deleted, only upserted.

6. **Ticker scope**: reuse `market_data.get_market_targets` unchanged — the
   same "owned + verified Yahoo provider mapping" resolution already used by
   `yfinance-sync`. No new ticker-scope logic; this directly satisfies the
   "don't pull arbitrary tickers" requirement for free.

7. **Not auto-wired into the ingestion pipeline.** `yfinance-sync`'s OHLCV
   sync auto-runs after an email batch commits
   (`docs/architecture/database_schema.md`, "YFinance Market Data"). Earnings
   and dividend calendars don't need to move in lockstep with portfolio
   ingestion, and adding two more yfinance calls per ticker to every email
   pipeline run is unwarranted cost. v1 ships as a standalone, on-demand CLI
   command only (run before a KB agent pass or dashboard refresh, or on a
   schedule the user sets up separately). Auto-wiring is deliberately
   deferred (see below).

## What will be built

### 1. Schema (`src/database.py`, `src/config.py`)

`config.py`: bump `DATABASE_SCHEMA_VERSION = 11` → `12`.

`database.py`: add `earnings_events`, `dividend_events` to `REQUIRED_TABLES`.
Add a shared helper (mirrors `_create_position_engine_tables` /
`_create_statement_balances_table`, called from both `_deploy_schema` and the
migration):

```python
def _create_earnings_dividends_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the earnings_events/dividend_events market-data tables.

    Shared between `_deploy_schema` (fresh installs) and the v11->v12
    migration so both paths stay in lockstep. These record company-declared
    market data (earnings calendar, dividend schedule), distinct from
    `cash_transactions`/`transactions`, which record the user's own received
    dividend cash from brokerage statements -- never join or conflate them.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_events (
            ticker_id BIGINT NOT NULL,
            report_date DATE NOT NULL,
            period VARCHAR,
            eps_estimate DOUBLE,
            eps_actual DOUBLE,
            revenue_estimate DECIMAL(20, 2),
            revenue_actual DECIMAL(20, 2),
            surprise_pct DOUBLE,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, report_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dividend_events (
            ticker_id BIGINT NOT NULL,
            ex_dividend_date DATE NOT NULL,
            pay_date DATE,
            declared_amount DECIMAL(18, 8) NOT NULL,
            frequency VARCHAR,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, ex_dividend_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
```

- `_deploy_schema`: call `_create_earnings_dividends_tables(connection)`
  right after the existing `_create_statement_balances_table(connection)`
  call (before the `schema_metadata` INSERT).
- **Migration — precise edit required.** The current v10→v11 block in
  `initialize_database` is the *terminal* block: on success it calls
  `_create_trade_events_view(connection)` and `return False` immediately,
  using `DATABASE_SCHEMA_VERSION` (currently `11`) as its target version. To
  extend the ladder to v12, that block must be edited (not just appended
  after) to chain into a new terminal block, exactly mirroring how the
  v9→v10 block was previously edited to chain into v10→v11:
  - In the existing `if row and row[0] == 10:` block: change
    `[DATABASE_SCHEMA_VERSION, SCHEMA_COMPONENT]` to the hardcoded
    `[11, SCHEMA_COMPONENT]`; change the `logger.info` call to hardcode `11`
    instead of `%d`/`DATABASE_SCHEMA_VERSION`; **remove** the
    `_create_trade_events_view(connection)` + `return False` lines that
    currently end that block; **add** `row = (11,)` at the end instead (this
    is exactly the shape of the current `if row and row[0] == 9:` block,
    which chains into `row[0] == 10`).
  - Add a new terminal block immediately after:
    ```python
    if row and row[0] == 11:
        connection.execute("BEGIN TRANSACTION")
        try:
            _create_earnings_dividends_tables(connection)
            connection.execute(
                "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                [DATABASE_SCHEMA_VERSION, SCHEMA_COMPONENT],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            logger.exception("Database migration from version 11 failed")
            raise
        logger.info("Database migrated from schema version 11 to %d", DATABASE_SCHEMA_VERSION)
        _create_trade_events_view(connection)
        return False
    ```
  This keeps every block's own transaction/rollback/logging shape identical
  to the existing ladder and only ever uses `DATABASE_SCHEMA_VERSION` (not a
  hardcoded `12`) in the new terminal block, matching convention.

### 2. New extractor: `src/earnings_dividends_extractor.py`

Mirrors `src/yfinance_extractor.py`'s structure exactly (reuses its private
helpers `_require_yfinance`, `_build_session`, `_create_ticker`,
`_normalize_tickers`/`_normalize_hint` by importing them, rather than
duplicating ticker-resolution logic):

- `fetch_earnings_events(tickers) -> pd.DataFrame` — threaded
  (`ThreadPoolExecutor`, same `YFINANCE_MAX_WORKERS` cap) per-ticker fetch of
  `client.earnings_dates`; per-ticker failures logged and skipped (never
  raise), matching `fetch_security_info`'s isolation pattern. Output columns:
  `Ticker`, `ProviderSymbol`, `ReportDate`, `EpsEstimate`, `EpsActual`,
  `SurprisePct`.
- `fetch_dividend_events(tickers) -> pd.DataFrame` — same threading pattern
  over `client.dividends`. Output columns: `Ticker`, `ProviderSymbol`,
  `ExDividendDate`, `DeclaredAmount`.
- `main(argv)` — standalone/delegated CLI entry point mirroring
  `yfinance_extractor.main`: `--tickers` (required), `--cache-dir`,
  `--ignore-proxy`, `--skip-earnings`, `--skip-dividends`; prints frames via
  a reused `_print_frame` helper. Registered in `app.py`'s
  `delegated_commands` dict as `"earnings-dividends": ("earnings_dividends_extractor", "main")`,
  exactly like the existing `"yfinance"` entry — an ad-hoc fetch+print
  command, not a database write.

### 3. Sync orchestration: `src/market_data.py`

New function `sync_earnings_dividends(db_path, symbols=None, *, earnings_fetcher=fetch_earnings_events, dividends_fetcher=fetch_dividend_events) -> EarningsDividendsSyncResult`, mirroring `sync_market_data`'s shape:
- Calls `initialize_database(db_path)`.
- Resolves scope via `get_market_targets(db_path, symbols)` (reused as-is).
- Fetches earnings + dividends per resolved ticker set (dependency-injected
  fetchers, same pattern `sync_market_data` uses for
  `metadata_fetcher`/`history_fetcher`, so tests never hit the network).
- Writes inside one `BEGIN TRANSACTION` / `COMMIT` / `ROLLBACK` block via the
  new `database_command.py` upload functions (below).
- Returns a small result dataclass (`tickers`, `earnings_rows`,
  `dividend_rows`, `error`, `failed_symbols`), matching `MarketSyncResult`'s
  shape.
- Per-ticker fetch failures are isolated (logged, that ticker's rows simply
  absent) — never abort the whole sync, matching existing
  `ensure_benchmark_history`/`sync_market_data` failure-isolation style.

### 4. Upload functions: `src/database_command.py`

- `upload_dividend_events(data, ticker_ids, db_path) -> int` — straight
  upsert loop over the fetched frame, `INSERT ... ON CONFLICT (ticker_id, ex_dividend_date) DO UPDATE SET declared_amount = excluded.declared_amount, fetched_at = CURRENT_TIMESTAMP`, same validation-and-raise style as `upload_security_history` (reject rows missing `ticker_id`/date/amount rather than silently dropping them).
- `upload_earnings_events(data, ticker_ids, db_path) -> int` — for each
  ticker present in the batch, first `DELETE FROM earnings_events WHERE ticker_id = ? AND report_date >= CURRENT_DATE AND eps_actual IS NULL`, then upsert every row in that ticker's freshly fetched frame via `ON CONFLICT (ticker_id, report_date) DO UPDATE SET eps_estimate = excluded.eps_estimate, eps_actual = excluded.eps_actual, surprise_pct = excluded.surprise_pct, fetched_at = CURRENT_TIMESTAMP`.

### 5. CLI wiring: `src/app.py`

- Add `"earnings-dividends"` to the existing `delegated_commands` dict (ad-hoc fetch+print, section 2 above).
- Add `_run_earnings_dividends_sync_command(argv)`, mirroring `_run_yfinance_sync_command` exactly: `--database`, `--tickers` (optional scope narrowing), `--skip-earnings`, `--skip-dividends`. Dispatch it from `main()` via `if raw_args and raw_args[0] == "earnings-dividends-sync": return _run_earnings_dividends_sync_command(raw_args[1:])`, placed alongside the other explicit-dispatch commands (`yfinance-sync`, `classification-sync`, etc.) — **not** added to the generic `delegated_commands` set, since (like `yfinance-sync`) it needs custom argument handling beyond simple delegation.
- Add both new subcommands to `_print_root_help`'s `commands.add_parser(...)` catalog.

### 6. Documentation updates (same change, per `CLAUDE.md`'s doc rule)

- `docs/reference/cli.md` — new `## Earnings & Dividends` section (mirrors the existing `## YFinance` + `### Database synchronization` structure): the ad-hoc `earnings-dividends` command and its flags, then the `earnings-dividends-sync` command and its flags/behavior (ticker scope, upsert/refresh semantics, "not auto-triggered by the pipeline" note).
- `docs/architecture/database_schema.md` — new short section (mirrors its existing "## YFinance Market Data" section) describing `earnings_events`/`dividend_events`, the upsert/speculative-cleanup behavior, the explicit non-conflation note with `cash_transactions`/`transactions`, and that it is not part of the automatic post-email sync.

## Tests

Following `CLAUDE.md`'s Testing Guidelines (`unittest`, deterministic
fixtures, network always mocked) and the existing mocking conventions in
`tests/test_yfinance_extractor.py` / `tests/test_market_data.py`
(dependency-injected fetchers, `Mock()` yfinance clients/sessions, `Mock()`
DuckDB connections asserting `BEGIN TRANSACTION`/`COMMIT` call order):

- **New `tests/test_earnings_dividends_extractor.py`** — mock `yf.Ticker` /
  session construction; verify `fetch_earnings_events`/`fetch_dividend_events`
  field mapping and column shape against a synthetic `earnings_dates`
  DataFrame and `dividends` Series; per-ticker exception isolation (one
  ticker raising doesn't drop the others); empty-input handling.
- **`tests/test_market_data.py`** additions — `sync_earnings_dividends`
  tests mirroring the existing `sync_market_data` tests: ticker scope comes
  from `get_market_targets` (patched), injected fetchers, transaction
  begin/commit assertions, per-ticker fetch-failure isolation.
- **`tests/test_database_command.py`** additions — `upload_dividend_events`
  upsert-on-conflict behavior (re-upload same PK updates in place, doesn't
  duplicate); `upload_earnings_events` estimate→actual upsert-in-place
  behavior, and the speculative-row delete-before-insert behavior (seed an
  unreported future row, re-sync without it, assert it's gone; seed a
  reported past row, re-sync without it in the fetch, assert it's
  preserved).
- **`tests/test_database.py`** addition —
  `test_version_eleven_schema_is_upgraded_and_adds_earnings_dividends_tables`,
  mirroring `test_version_ten_schema_is_upgraded_and_adds_statement_balances_table`:
  seed a v11 database, drop the two new tables, force
  `schema_metadata.schema_version = 11`, call `initialize_database`, assert
  version is now `DATABASE_SCHEMA_VERSION`, both tables exist and are
  writable, `is_database_active` is true, and `v_trade_events` is still
  queryable (regression check that the terminal-block edit didn't break the
  view recreation).
- **`tests/test_app.py`** additions — mirror the existing
  `test_yfinance_sync_command_forwards_database_and_tickers` /
  `test_yfinance_sync_full_flag_is_forwarded` style tests for
  `earnings-dividends-sync` argument forwarding, plus a delegation test for
  the ad-hoc `earnings-dividends` command landing in `delegated_commands`.

## Implementation order

1. `config.py` version bump + `database.py` schema/migration changes (section
   1) + its migration test. Get this green in isolation first since it's the
   highest-precision, highest-risk piece touching a live database file.
2. `src/earnings_dividends_extractor.py` + its tests.
3. `database_command.py` upload functions + their tests.
4. `market_data.py::sync_earnings_dividends` + its tests.
5. `app.py` CLI wiring (both commands) + its tests.
6. `docs/reference/cli.md` and `docs/architecture/database_schema.md` updates.

## Verification

1. `uv run python -m unittest discover -s tests` — full suite green,
   including the new migration test.
2. Against a scratch copy of the real database file (never the live one):
   run `earnings-dividends-sync`, confirm `schema_metadata.schema_version`
   reads `12`, both tables populated for owned tickers, re-run confirms
   upsert (row counts stable, `fetched_at` advances, no duplicates).
3. ~~Manually verify the raw units of `earnings_dates`'s `Surprise(%)`
   column against a live ticker before shipping.~~ **Done** — confirmed
   already in percentage-point units (see "Confirmed via live test" above);
   carry this into `docs/architecture/database_schema.md` when written.
4. Confirm `uv run python src/app.py --help` lists both new commands.

## Deliberately deferred

- `earnings_events.period`, `revenue_estimate`, `revenue_actual` — no
  reliable yfinance source at the per-`report_date` granularity this schema
  needs; columns remain nullable for a future dedicated revenue source.
- `dividend_events.pay_date`, `frequency` — not available from
  `Ticker.dividends`; only `calendar`'s single next-payment guess has a pay
  date, not worth partial/inconsistent coverage for v1.
- Ingesting `calendar`'s forward-looking, model-estimated next-dividend
  amount as a `dividend_events` row (kept out to preserve "declared_amount"
  as strictly confirmed data).
- Auto-triggering this sync after the email pipeline (mirroring
  `yfinance-sync`'s auto-run) — v1 ships as an on-demand command only.
- Any dashboard view or knowledge-base agent read path — both are separate,
  already-tracked efforts; this plan only makes the data queryable.
- Deriving dividend `frequency` from historical ex-date cadence — plausible
  future enhancement, not attempted here.

## Risks

- **The migration terminal-block edit is the sharp edge.** Get this wrong
  (e.g. forgetting to remove the old `return False`/hardcode `11` instead of
  reusing `DATABASE_SCHEMA_VERSION` in the wrong spot) and either the ladder
  silently stops at v11 forever, or a fresh v10 database double-creates the
  view/skips a step. The precise before/after diff in section 1 above should
  be followed exactly; the new migration test is the primary guardrail.
- **`earnings_dates`'s window is a yfinance library default, not something
  this pipeline controls** — if Yahoo changes how many rows it returns, the
  speculative-row cleanup logic (delete unreported future rows before
  re-insert) could over- or under-retire rows; scoped per-ticker so a bad
  fetch for one ticker can't corrupt another's data.
- **No incremental fetch window** (decision #4) means every sync re-fetches
  full available history per ticker for every ticker in scope — acceptable
  given yfinance's own response sizes here (a few dozen rows per ticker) but
  worth knowing this doesn't scale the way OHLCV's incremental fetch does.
- ~~Surprise(%) units are unverified against a live call.~~ **Resolved** —
  confirmed as percentage points via live test against real held tickers.
- **ETF tickers legitimately return no earnings data.** Confirmed via
  `CDZ.TO`; the extractor must not treat an empty `earnings_dates` result as
  an error for fund-type tickers.
