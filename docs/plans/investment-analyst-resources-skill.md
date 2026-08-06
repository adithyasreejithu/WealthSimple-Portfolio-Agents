# Investment Analyst Resources — Phase 1a data-layer skill

*Status: approved and implemented — a standalone phase of
`investment-analyst-v2-plan.md`'s Phase 1, built as a deterministic skill
instead of the `investment-data-prep` agent that plan sketches, per
Adithya's direction on 2026-08-04. Intended to be merged into the v2 plan
once the `investment-analyst` (opus) judgment agent is built on top of it.*

## Context

`docs/plans/investment-analyst-v2-plan.md` proposes a second research track
(v2) whose Phase 1 is an `investment-data-prep` **agent** that fetches
yfinance data and persists it. Phase 1's data layer is a **skill** instead —
a deterministic Python script, no agent, no LLM judgment — called
**`investment-analyst-resources`**.

The reason: v1's `stock-data-prep` agent never touches DuckDB. It fires a
live 12-group yfinance pull on every single run and reads the classification
*export file* for position context, throwing the raw response away
afterward (`fetch-stock-research-data` output is explicitly ephemeral).
Meanwhile this repo has spent thirteen schema versions persisting exactly
the data an analyst needs — `historical_records`, `financial_snapshots`,
`earnings_events`, `dividend_events`, `position_snapshots`,
`portfolio_classifications` — and none of it was being read by the research
path. This skill closes that gap: read the database first, refresh only the
ticker and only the domains that are actually overdue, and make one narrow
live call for the things DuckDB structurally cannot hold.

This plan is deliberately standalone so it can be built and merged into the
v2 plan phase by phase, rather than as one big-bang rebuild.

**Confirmed decisions:** DB-first with a live top-up for unpersisted groups ·
per-domain freshness cadences · standalone phase doc, no `research-v2/` tree
yet · read-only connections so parallel agents never lock each other out ·
two-tier output (inline digest + on-disk bundle).

---

## Part 1 — Coverage analysis (what v1's data-prep pulls vs what the DB already has)

### What v1's `stock-data-prep` pulls today

Per `.claude/agents/stock-data-prep.md` and
`.claude/skills/fetch-stock-research-data/references/yfinance-research-contract.md`:

1. `kb-search --ticker` — existence check on `Knowledge-Base/stocks/TICKER.md` only.
2. `fetch-stock-research-data` — one live yfinance call, **12 groups**:
   `overview`, `valuation`, `financials`, `earnings`, `analyst`, `options`,
   `news`, `insider`, `institutional`, `dividends`, `history` (400 days),
   `funds`. Written to `exports/stock-recommendations/<TICKER>-<date>-research.json`,
   never persisted beyond that run.
3. First run only: `annual-financial-context --ticker` — annual statements,
   ephemeral, stdout only.
4. `scoring_worksheet.py` — merges the research JSON +
   `exports/portfolio-classification/portfolio-classification.json` +
   `--thesis-page` into derived metrics, position context, and prior decision.

**No DuckDB reads anywhere in that chain.** Position/weight context reaches
v1 only secondhand, via the classification export.

### What DuckDB can serve for the same 12 groups

| v1 group | DuckDB source | Coverage |
|---|---|---|
| `history` | `historical_records` (OHLCV + adjusted close, back to first ownership) | **Better than v1** — full history vs v1's 400-day window |
| `financials` | `financial_snapshots` (revenue, net income, EPS, gross/op margin, D/E, current ratio, FCF, plus `extra` JSON for every unmapped line item) | **Better than v1** — accumulates depth across syncs beyond yfinance's shallow ~5-quarter window |
| `dividends` | `dividend_events` (ex-date, pay date, declared amount, frequency) **plus** dividends actually received in `transactions`/`cash_transactions`/`email_transactions` | **Better than v1** — declared schedule *and* your realized income; v1 has only the former |
| `earnings` | `earnings_events` (report date, period, EPS est/actual, revenue est/actual, surprise %) | **Full**, including the forward calendar |
| `funds` | `etf_details` (fund_family, yield, expense_ratio, aum, nav, `top_holdings`, `sector_weights`) | **Partial** — missing description, `fund_operations` detail, asset classes, bond ratings |
| `overview` | `tickers` (name, exchange, currency, financial_currency, security_type) + `stock_details` (sector, industry) + `portfolio_classifications.fields` | **Partial** — missing `longBusinessSummary`, country, website, employees |
| `valuation` | latest close from `historical_records`; market cap + dividend yield from `portfolio_classifications.fields`; ETF yield/NAV/AUM/expense from `etf_details` | **Mostly missing** — no trailing/forward PE, PEG, P/B, P/S, EV/EBITDA, FCF, ROE/ROA, margins, target price |
| `analyst` | — | **None** |
| `options` | — | **None** |
| `news` | — | **None** |
| `insider` | — | **None** |
| `institutional` | — | **None** |

### So: what still has to be pulled live

The top-up call is `fetch-stock-research-data` restricted to
**`valuation analyst options news insider institutional`**, plus `overview` and
`funds` for their narrative/descriptive fields. That is 6–8 groups instead of
12 — and `financials`, `earnings`, `dividends`, `history` (the four heaviest
payloads) come from disk.

### What DuckDB gives that v1 cannot get at all

None of this exists in a yfinance response:

- **Your actual position** — `position_snapshots`: quantity, average-cost
  book value (CAD and listing currency), unrealized, realized gain,
  provisional quantity, data-quality flags.
- **Full trade history** — `position_ledger`: every buy/sell with cost,
  proceeds, FX rate, and running book value → entry timing, cost-basis
  evolution, holding period.
- **Portfolio weight and concentration** — via the classification export
  (`portfolio-classification.json`'s `fields.current_weight_percent` /
  `position_market_value`), which is already the source v1's own
  `scoring_worksheet.py` reads.
- **Classification with provenance** — `portfolio_classifications`:
  primary group, secondary tags, confidence, reasoning, `evidence_used`,
  `missing_data`, `review_needed`, `generated_at`.
- **Dividend income actually received** for this ticker
  (`transactions`/`email_transactions` DIV rows).

---

## Part 2 — Skill design

### Location and shape

```
.claude/skills/investment-analyst-resources/
  SKILL.md
  references/resource-contract.md        # bundle schema + cadence table + scope boundary
  scripts/
    investment_analyst_resources.py      # CLI, orchestration, digest + bundle
    freshness_gate.py                    # per-domain staleness + scoped refresh dispatch
    db_resources.py                      # the fixed read-only queries
```

Three scripts mirrors `classify-portfolio/scripts/`'s existing split. Per
`CLAUDE.md`, skill-owned Python lives beside the skill, not in `src/`.

### Concurrency model (drives the whole design)

DuckDB allows **either one read-write process or many read-only processes**
on a database file — never both. A writer takes an exclusive file lock, and
every other process, *including read-only ones*, then fails to attach. Two
facts about this repo follow from that:

- `database.get_shared_connection` (`src/database.py`) opens **read-write**
  and caches it process-wide. Every `src/analytics.py` function, and
  `src/market_data.py`'s sync functions, route through it. **Any code that
  calls them takes the exclusive lock and blocks every parallel agent.**
- `read_classification_data.py` already does the right thing —
  `duckdb.connect(path, read_only=True)` — and its contract mandates it.
  That is the precedent this skill follows for its read path.

**A DuckDB view does not help with locking.** A view is stored SQL with no
isolation semantics, and `CREATE OR REPLACE VIEW` is itself a write needing
the exclusive lock. So the fixed queries live as SQL constants in
`db_resources.py`, not as new DB views.

Consequently the skill has **three modes**, and only one of them writes:

| Mode | Lock | Safe to fan out? | What it does |
|---|---|---|---|
| `gate` | read-only | **Yes** | Per-domain freshness verdict as JSON. No writes, no fetch. |
| `refresh` | **read-write** | **No — run once, sequentially** | The only writer. Accepts many tickers in one call. |
| `read` | read-only | **Yes** | Builds the bundle from DB + live top-up. Never writes to DuckDB. |

This mirrors v1's existing fan-out rule, where `kb-intake` is the single
sequential writer while every prep/analyst invocation runs in parallel
(`docs/architecture/decision_support_flow.md`). For a portfolio-wide run the
orchestration is: one `gate` pass → **one** `refresh` call for all stale
tickers → N parallel `read` calls.

Default single-ticker invocation still does gate → refresh → read in one
process. It calls `database.close_connection()` after the refresh phase,
because a process cannot hold a read-write connection and then open a
read-only one to the same file. On lock contention the skill fails with an
explicit "another process is refreshing the database — retry, or use
`--no-refresh`" message rather than an opaque DuckDB IO error.

### Run flow

```
resolve ticker  →  freshness gate  →  scoped refresh (only stale domains)
                                   →  DB read  →  live top-up  →  digest + bundle
```

**1. Resolve.** Look up `ticker_id`, `security_type`, and the verified Yahoo
`provider_symbol` (`ticker_provider_mappings`, `provider='yahoo'`,
`verification_status='verified'`), all read-only. No fuzzy resolution — same
rule as `fetch-stock-research-data`. Unresolvable → exit non-zero, never
fabricate.

**2. Freshness gate** (`freshness_gate.py`), modeled on
`kb-staleness-gate/scripts/staleness_gate.py` — read-only, prints JSON, and
`--dry-run` computes identically:

| Domain | Timestamp checked | Cadence | Refresh action (ticker-scoped) |
|---|---|---|---|
| Prices | `MAX(historical_records.record_date)` | last completed trading day | `market_data.sync_market_data(symbols=[sym])` |
| Earnings | `MAX(earnings_events.fetched_at)` | 7 days | `sync_earnings_dividends(symbols=[sym], skip_dividends=True)` |
| Dividends | `MAX(dividend_events.fetched_at)` | 30 days | `sync_earnings_dividends(symbols=[sym], skip_earnings=True)` |
| Financials | `MAX(financial_snapshots.fetched_at)` | 30 days | `sync_financial_snapshots(symbols=[sym])` |
| Classification | `portfolio_classifications.generated_at` | 7 days | **report only** — `classify-portfolio` is portfolio-wide |
| Positions | ledger fingerprint vs `position_engine_meta` | any mismatch | **report only** — `recompute-positions` is portfolio-wide |

**Earnings-proximity override.** If `earnings_events` shows a `report_date`
within ±2 days of today, force a refresh of prices + earnings + financials
regardless of cadence, and stamp `earnings_window: true` on the bundle. This
exists because analysis run on an earnings day against pre-earnings data has
already burned us once — it is the concrete gate that prevents a repeat.

Refreshes call `src/market_data.py`'s sync functions **in-process** with
`symbols=[provider_symbol]`; `get_market_targets` does the per-ticker
filtering, so "only that stock" is enforced by existing, tested code rather
than a new subprocess wrapper.

Two boundaries the gate handles explicitly, both discovered in
`get_market_targets`:
- It only returns tickers that are **owned** (have transactions) *and* have a
  verified Yahoo mapping. A watchlist/unowned ticker cannot be synced this
  way — the skill detects that (its own read-only ownership check, not a
  call into `get_market_targets`, which would take the write lock) and falls
  back to a live-only bundle with `refresh_skipped: "not-owned"` rather than
  silently returning empty DB reads.
- `sync_market_data` refreshes the FX pair and benchmark history *before*
  target filtering, so a price refresh touches those two series too. Both
  are failure-isolated and cheap; documented, not fought.

**3. DB read** (`db_resources.py`). Opens its **own**
`duckdb.connect(path, read_only=True)` connection — never
`get_shared_connection` — so N of these can run at once. Fixed queries only,
as SQL constants: no arbitrary SQL and no caller-supplied database path, the
boundary `read-portfolio-classification-data` already establishes.

Tables read: `position_snapshots`, `position_ledger`, `historical_records`,
`financial_snapshots`, `earnings_events`, `dividend_events`, `stock_details`,
`etf_details`, `portfolio_classifications`, `tickers`,
`ticker_provider_mappings`, `transactions`.

**This does not reimplement portfolio math.** It reads math that has
*already been computed and stored*: `position_snapshots` holds the position
engine's own output, and `portfolio_classifications` holds the classifier's.
Portfolio-level aggregates that only exist inside `analytics.py`
(group/sector allocation, look-through exposure, ETF overlap, allocation
drift, weight) are **not** recomputed and **not** queried — weight/market
value come from the already-generated
`exports/portfolio-classification/portfolio-classification.json`, a plain
file with no lock, exactly the source v1's `scoring_worksheet.py` uses
today. That keeps the read path both parallel-safe and free of duplicated
math.

**4. Live top-up.** Invoke
`.claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py`
with `--groups valuation analyst options news insider institutional overview funds`.
Import and call it, don't re-implement the yfinance mechanics. `--groups
history financials earnings dividends` are deliberately *never* requested;
the DB serves those. A failed group is recorded as data, never raised.

**5. Emit — two tiers.**

- **Digest — stdout, a few KB, safe to paste onward.** Freshness verdict per
  domain, what was refreshed, DB-vs-live provenance per group, the position
  line (quantity, average cost, unrealized, weight), headline metrics, and
  **aggregates instead of raw series** (e.g. "252 trading days, 2025-08-04 →
  2026-08-04, +38.2%, 52wk 14.10–29.60" rather than 252 rows), plus explicit
  gaps. This is what a calling agent receives inline.
- **Full bundle — on disk**, `exports/investment-analyst-resources/<TICKER>-<date>-resources.json`,
  consumed *programmatically* by the next script, never `Read` by an LLM.

Why not stdout-only: the bundle carries full OHLCV history, the trade
ledger, `financial_snapshots` with `extra` blobs, and live options chains —
v1's narrower equivalent is already "hundreds of KB," which is precisely why
`stock-data-prep` is forbidden from Reading it. And the file isn't an
"export" in the outward sense: `.gitignore` ignores `*` and whitelists only
`src/`, `docs/`, `tests/`, `dashboard/`, `.claude/`, `Knowledge-Base/`, so
`exports/` is local-only and never committed.

`--no-bundle` drops the file; the cost is the next step redoing the pull.

No `research-v2/` tree and no content-hashed evidence store in this phase;
both belong to the v2 merge.

### Bundle schema (top level)

```json
{
  "schema": "investment-analyst-resources.v1",
  "ticker": "PLTR", "provider_symbol": "PLTR", "asset_class": "stock",
  "as_of": "2026-08-04",
  "freshness": {"prices": {}, "earnings": {}, "dividends": {}, "financials": {}, "classification": {}, "positions": {}},
  "refreshed": ["prices", "earnings"],
  "earnings_window": false,
  "db": {"position": {}, "ledger": [], "prices": {}, "financials": [],
         "earnings": [], "dividends": {}, "classification": {}, "portfolio_context": {}},
  "live": {"valuation": {}, "analyst": {}, "options": {}, "news": [],
           "insider": {}, "institutional": {}, "overview": {}, "funds": {}},
  "errors": {}, "gaps": []
}
```

### CLI

```powershell
# Default: gate -> refresh -> read in one process (single-agent use)
uv run python .claude/skills/investment-analyst-resources/scripts/investment_analyst_resources.py --ticker PLTR

# Fan-out orchestration: one gate, one writer, N parallel readers
... --mode gate    --ticker PLTR NVDA T          # read-only, parallel-safe
... --mode refresh --ticker PLTR NVDA            # THE writer: once, sequentially
... --mode read    --ticker PLTR                 # read-only, parallel-safe
```

Flags: `--no-refresh` (read as-is, flag staleness) · `--force-refresh`
(ignore cadences) · `--no-live` (DB-only bundle) · `--no-bundle` (digest
only, no file) · `--output PATH`.

---

## Files

**New**
- `docs/plans/investment-analyst-resources-skill.md` — this plan
- `.claude/skills/investment-analyst-resources/SKILL.md`
- `.claude/skills/investment-analyst-resources/references/resource-contract.md`
- `.claude/skills/investment-analyst-resources/scripts/{investment_analyst_resources,freshness_gate,db_resources}.py`
- `tests/test_investment_analyst_resources.py`

**Read/reused, not modified** — `src/` needs no changes at all
- `src/market_data.py` — `get_market_targets`, `sync_market_data`,
  `sync_earnings_dividends`, `sync_financial_snapshots` (`refresh` mode only)
- `src/database.py` — `close_connection` (between write and read phases),
  schema/table definitions
- `exports/portfolio-classification/portfolio-classification.json` — source
  of portfolio-level weight/market-value, lock-free
- `.claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py`
- `.claude/skills/kb-staleness-gate/scripts/staleness_gate.py` — gate pattern
- `.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py` — read-only connection pattern

**Not touched:** `docs/reference/cli.md` (this adds no `src/app.py`
command), `Knowledge-Base/`, v1's agents and skills, `src/analytics.py`
(not called on the parallel-safe read path).

---

## Verification

1. `uv run python -m unittest tests.test_investment_analyst_resources` — gate
   math (each cadence boundary, earnings-proximity override, unowned-ticker
   fallback), bundle assembly, and DB-read shaping against fixtures with
   injected sync/fetch callables. No live network in tests.
2. `uv run python -m unittest discover -s tests` — nothing regressed.
3. `--mode gate --ticker PLTR` on the real DB → per-domain freshness verdict,
   confirm it writes nothing.
4. Live run on **PLTR** (has v1 history, the intended side-by-side ticker):
   first run refreshes stale domains; immediate re-run reports everything
   fresh, performs **zero** syncs.
5. Live run on an **ETF** (e.g. `VFV.TO`) — confirm empty equity groups and
   absent `financial_snapshots` are reported as expected-empty, not failures.
6. Live run on an **unowned/watchlist** ticker — confirm the `not-owned`
   fallback path yields a live-only bundle instead of erroring.
7. **Concurrency proof.** Launch 3+ simultaneous `--mode read` processes on
   different tickers and confirm all succeed with no lock error. Start a
   `--mode refresh` and, while it holds the write lock, confirm a concurrent
   `--mode read` fails with the skill's explicit retry message.
8. Digest stays small — a stdout ceiling is asserted in the tests so a
   future change can't quietly reintroduce raw series into inline output.
9. Compare the bundle against a v1 `fetch-stock-research-data` run for the
   same ticker to confirm the coverage matrix above holds in practice.

## Deferred (next phases, merged into the v2 plan)

- The `investment-analyst` (opus) agent that consumes this bundle, its
  thesis artifact contract, and `validate_thesis.py` — v2 plan §7.4/§7.6.
- `research-v2/` evidence store with `retrieved_at` + content hash — §7.3.
- Materializing the fixed queries as real DuckDB views (a schema migration
  in `src/database.py`) — worth doing for contract stability once the query
  set settles, but it buys tidiness, not parallelism.
- New DuckDB tables for valuation/analyst/options/insider/institutional so
  future runs serve them from disk too — worth doing once this skill shows
  which live groups are actually load-bearing.
