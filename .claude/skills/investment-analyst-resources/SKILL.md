---
name: investment-analyst-resources
description: Build a DB-first investment research resource bundle for a ticker -- read everything already persisted in DuckDB (positions, ledger, prices, financials, earnings, dividends, classification), refresh only the domains that are overdue for that one ticker, top up with a narrow live yfinance pull for the groups DuckDB cannot hold (valuation, analyst ratings, options, news, insider, institutional), pull a dedicated live current-price quote that position value/weight/return calculations prefer over a possibly-stale DB close, and compute deterministic derived metrics (options positioning, price returns, analyst revisions, insider activity) plus a data-completeness trace. Use as the data layer beneath the v2 investment-analyst track's Phase 1, or any time a stock's DB-backed context plus a small live top-up is needed without the cost of a full 12-group yfinance pull.
---

# Investment Analyst Resources

Deterministic, no-judgment data layer. See
`docs/plans/investment-analyst-resources-skill.md` for the full design and
coverage analysis against v1's `fetch-stock-research-data`.

Run `uv run python .claude/skills/investment-analyst-resources/scripts/investment_analyst_resources.py`:

- `--ticker TICKER [TICKER ...]` — one or more pipeline ticker symbols.
- Default (no `--mode`): gate → refresh → read in one process. Refreshes
  only the domains that are actually stale for the given ticker(s), then
  reads the database and tops up live, then prints a **digest** to stdout.
  The bundle JSON is written to a **run workspace** — see below.
- `--mode gate` — read-only freshness verdict per ticker, JSON to stdout, no
  writes, no fetch. Safe to run in parallel with anything.
- `--mode refresh` — the **only** write path: batch-refreshes every due
  domain across the given tickers in one pass. Run this once, sequentially,
  before fanning out parallel `--mode read` calls.
- `--mode read` — read-only: DB read + live top-up + digest + bundle, no
  refresh. Safe to fan out in parallel once a `--mode refresh` pass has run.
- `--no-refresh` / `--force-refresh` / `--no-live` / `--no-quote` /
  `--no-bundle` / `--no-trace` / `--output PATH` / `--trace-log-path PATH` —
  see `references/resource-contract.md`. `--no-quote` is independent of
  `--no-live`: omitting it keeps the live current-price quote even in a
  `--no-live` (DB-only) run, since that's the case it matters most for.

## Run workspace: default-on

Any invocation that produces a bundle (`--mode read` or the default
gate→refresh→read sequence) **attaches to a run workspace automatically** —
the bundle lands in that run's `evidence/`, is registered in its evidence
registry, and the completeness trace is appended to its audit log. **The run
is created if it does not exist**, so no `run create` step is needed first.
`--mode gate`/`--mode refresh` never open one; they produce nothing to
register.

This is deliberately not opt-in. A run only existed before if whoever
composed the command line remembered to pass `--run-id` — a judgment call
that could be skipped on any given invocation, and the old wording here
("omit for the unchanged `exports/` behavior") actively invited skipping it.
An audit trail cannot tolerate that: an absent run looked identical to "no
pull happened." Flipping the default moved the decision into the code, the
same way the completeness trace already runs unless `--no-trace` is passed.

- `--run-id ID` — name the run explicitly rather than letting one be
  generated. Attaches if it exists, creates it under that exact name if not.
  Passing the same ID to several invocations puts them all in one run — the
  fan-out pattern.
- `--no-run` — opt out; the bundle goes to `exports/` as before this became
  the default. The opt-out is itself recorded in the completeness trace
  (`workspace.skip_reason`), so it stays a visible, greppable fact in
  `logs/SkillTrace.jsonl` rather than a silent gap.
- A non-default `--db-path` also skips workspace attachment (the existing
  test/debug convention), so the test suite never populates the real
  `workspace/runs/`.

See `docs/architecture/run_workspace.md`.

## Why three modes

DuckDB allows one read-write process or many read-only processes on a
database file, never both. `gate` and `read` open their own
`duckdb.connect(path, read_only=True)` connection; `refresh` is the only
mode that touches `database.get_shared_connection` (via `src/market_data.py`'s
sync functions). For a portfolio-wide run: one `gate` pass to see what's due
→ one `refresh` call for every due ticker/domain → N parallel `read` calls.
Never call `--mode read` while a `--mode refresh` is in flight; it fails with
an actionable retry message rather than a raw DuckDB lock error.

## Sources at a glance

| Data | Live or DB? | Notes |
|---|---|---|
| Positions, ledger | DB | portfolio-wide, not refreshed by this skill |
| Prices | DB | refreshable |
| Financials | DB | quarterly, refreshable |
| Earnings | DB | refreshable |
| Dividends | DB | refreshable |
| Classification (role, account type) | DB (generated file) | portfolio-wide, not refreshed by this skill |
| Stock/ETF details | DB | synced by the core pipeline, not this skill |
| Weight, market value, cost basis, unrealized gain | DB + live price | computed fresh every run |
| Current price quote | Live | every run unless `--no-quote` |
| Overview, valuation, analyst, options, news, insider, institutional, funds | Live | every run unless `--no-live`; never persisted |
| Derived metrics, completeness trace | Computed | pure math, no fetch |

Full cadence rules, refresh commands, and field-level detail are in
[resource-contract.md](references/resource-contract.md).

## What it does not do

- Does not reimplement portfolio math. Position market value and weight are
  computed live and read-only via `position_engine.read_live_position_values`
  + `position_engine.latest_fx_rate` -- the same query and FX helper
  `src/analytics.py`'s write path calls (after its own `ensure_positions_fresh`
  self-heal) -- so there is one implementation of the math shared by both
  paths, not a second one reimplemented here. Cost basis
  (`portfolio_context.cost_basis_cad`) comes from that same query's
  `book_value_cad` column; unrealized gain $/% is the one bit of arithmetic
  this skill does itself (market value minus cost basis). The price fed into
  that math prefers the live quote (below) over the DB's last close for the
  analyzed ticker only; `portfolio_context.price_source` names which was
  used. Only `role`/`account_type` come from the already-generated
  `exports/portfolio-classification/portfolio-classification.json`, since
  those don't need daily freshness. Sector/group allocation, look-through
  exposure, and ETF overlap stay inside `analytics.py`, unused here.
- Does not blend the live quote into `prices`/`historical_records` data --
  that stays purely DB-sourced and DB-labeled. The quote gets its own
  `quote` block instead, so provenance (`db` vs `live` vs `quote`) is always
  explicit.
- Does not fetch `history`, `financials`, `earnings`, or `dividends` live —
  those come from the database, which already has deeper or more complete
  coverage than a single yfinance pull for all four.
- Does not persist the 8 live-only groups to DuckDB, and never will on a
  fixed cadence (deliberate, not deferred) -- most of them return their
  complete current picture on every pull with no "incremental delta" the
  way prices have, and the structured/slow-moving parts of `overview`/
  `funds` are already persisted by the core pipeline's `yfinance-sync` into
  `stock_details`/`etf_details`, which this skill already reads.
- Computes deterministic derived metrics (`derived_metrics.py`: FCF yield,
  options positioning, price returns, analyst revision counts, net insider
  shares) and a completeness trace (`build_trace`, emitted through the shared
  `src/skill_trace.py`) — both are pure math over data already fetched, not
  judgment. It still does not judge, score, or write to `Knowledge-Base/`. It
  has no rubric and forms no opinion — narrative thesis-writing and any
  Buy/Sell-style verdict remain the (deferred) `investment-analyst` agent's
  job. The trace grades only what was *obtainable* for the ticker: a name with
  no options chain is not penalized for lacking options metrics.
- Does not run a portfolio-wide classification or position recompute itself
  -- `classification` and `positions` are report-only domains; a stale
  verdict there names the portfolio-wide command to run instead.

Read [resource-contract.md](references/resource-contract.md) for the bundle
schema, the per-domain cadence table, the live-top-up group list, the
derived-metrics field list, and the completeness-trace format.
