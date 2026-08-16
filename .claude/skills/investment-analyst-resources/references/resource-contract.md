# Investment Analyst Resources — resource contract

## Per-domain cadence

| Domain | Freshness source | Cadence | Refreshable by this skill? |
|---|---|---|---|
| `prices` | `MAX(historical_records.record_date)` | last completed (weekday) trading day | yes -- `market_data.sync_market_data` |
| `earnings` | `MAX(earnings_events.fetched_at)` | 7 days | yes -- `sync_earnings_dividends(skip_dividends=True)` |
| `dividends` | `MAX(dividend_events.fetched_at)` | 30 days | yes -- `sync_earnings_dividends(skip_earnings=True)` |
| `financials` | `MAX(financial_snapshots.fetched_at)` | 30 days | yes -- `sync_financial_snapshots` |
| `classification` | `portfolio_classifications.generated_at` | 7 days | no -- portfolio-wide; run `classify-portfolio` |
| `positions` | ledger fingerprint vs `position_engine_meta` | any mismatch | no -- portfolio-wide; run `python src/app.py recompute-positions` |

The `classification` cadence above governs only `role`/`account_type` inside
`portfolio_context` -- those come from
`exports/portfolio-classification/portfolio-classification.json` and don't
need daily freshness. `weight_pct`/`position_market_value` are **not** on
this cadence: they're computed live and read-only on every run from
`position_snapshots` + a price via `position_engine.read_live_position_values`/
`latest_fx_rate` (the same helpers `src/analytics.py`'s write path uses) --
preferring the live quote (see below) over the latest `historical_records`
close when one is available, so `portfolio_context.price_source` tells you
which was actually used.

**Earnings-proximity override:** if any `earnings_events` row for the ticker
has a `report_date` within 2 days of today, `prices`, `earnings`, and
`financials` are forced stale regardless of cadence, and the bundle carries
`"earnings_window": true`. This exists so analysis run on or near an
earnings date never scores against pre-earnings data.

A ticker is refreshable when it is **owned** (rows in `transactions` /
`email_transactions` / `activities`) or declared a **research** candidate
(`security_status.declared_status` in `market_data.RESEARCH_STATUSES` --
currently just `wishlist`). Anything else -- unregistered, or declared
`avoid`/`retired` -- cannot be refreshed through this skill: the gate reports
`can_refresh: false` and a gap naming the fix, and the read phase falls back
to a live-only bundle. See **Subject scope** below.

## Subject scope (`subject`, in digest + bundle)

Every digest and bundle carries a `subject` block naming where the ticker
sits on the portfolio/research axis, computed by `db_resources.
resolve_subject`:

```json
{"registered": true, "owned": false, "declared_status": "wishlist",
 "rationale": "AI server infra research", "declared_at": "2026-08-14T...",
 "status": "wishlist", "scope": "research"}
```

`status` follows **owned > declared > unknown** precedence, matching
`analytics.resolve_security_status` (the write-path/CLI authority this
read-only skill never calls directly -- see `db_resources.
read_security_status`'s docstring for why). `scope` is `portfolio` when
owned, `research` otherwise -- the same axis `market_data.get_market_targets`
keys its `include_research` widening on.

| `subject.status` | Meaning | Refreshable? |
|---|---|---|
| `owned` | held now or in the past (any transaction) | yes |
| `wishlist` | declared a research candidate via `database status` | yes |
| `avoid`, `retired` | a deliberate declaration, not a research candidate | no |
| `unknown` | no `tickers` row, or a row with no declaration | no |

An `unknown` subject is not a silent dead end: the gate's message names the
exact fix --
`uv run python src/app.py database status --ticker TICKER --set wishlist`.
Pass `--register-wishlist` (optionally with `--wishlist-rationale TEXT`) to
have this skill declare it automatically instead of requiring that manual
step first -- it is the **one write this skill performs outside
`refresh_domains`**, gated the same way: only during the write/refresh phase
(`--mode refresh` or the default sequence), never `--mode gate`/`--mode
read`. It does not touch a ticker already declared `avoid`/`retired` --
that is a considered decision this flag must not silently override.

Once a subject is `wishlist`, `market_data.get_market_targets(...,
include_research=True)` admits it and this skill's refresh phase persists
real `historical_records`/`earnings_events`/`dividend_events`/
`financial_snapshots`/`stock_details`/`etf_details` rows for it, exactly
like an owned ticker -- enough for `security-technicals`' SMA-200/beta/
relative-strength to work on a name you've never bought. `pipeline` and the
standalone `yfinance-sync`/`earnings-dividends-sync`/
`financial-snapshots-sync` commands never pass `include_research`, so a
declared-wishlist ticker is refreshed **only on demand** through this skill,
never by the routine pipeline run.

`position`, `ledger_summary`, `portfolio_context`, and `classification` stay
portfolio-only concepts: for a research or unknown subject they are `null`
in the digest and graded `not_applicable` in the trace, never a gap.

## Live quote (`quote`, in digest + bundle)

A dedicated, lightweight current-price pull via yfinance's `fast_info`
(`_fetch_latest_quote` in `investment_analyst_resources.py`) -- distinct
from and cheaper than the `valuation` live group's full `get_info()` call,
and **independent of `--no-live`** (a `--no-live` run still gets an
accurate current price; only `--no-quote` skips this specific pull).

`{"price": float, "as_of": datetime, "source": "fast_info", "previous_close",
"day_high", "day_low", "year_high", "year_low"}`, or `None` on a failed
fetch, or `{"skipped": True}` under `--no-quote`.

This exists because, before it, the skill had two disagreeing notions of
"current price": `derived_metrics._spot()` (options math) already used a
semi-live `valuation.currentPrice`, while position value/weight/return
figures used whatever `historical_records` last had synced -- up to a full
trading day stale. The quote is now the single source of truth threaded
through all four:

- **Position value / portfolio weight** (`db_resources.read_live_market_value`/
  `read_live_portfolio_weight`/`read_portfolio_context`): the quote replaces
  the DB close for the *analyzed ticker only*; every other position in the
  portfolio-weight denominator still uses its own DB close. `portfolio_context`
  gains `"price_source": "live_quote" | "db_close"` so it's always clear
  which was used. Falls back to the pre-existing DB-only behavior exactly
  (same numbers) when the quote is absent (`--no-quote` or a failed fetch).
- **Return windows** (`derived_metrics.return_30d/90d/365d`): the quote
  becomes the series' "today" point when it's at least as recent as the
  last DB row, instead of stopping at that row.
- **Options spot** (`derived_metrics.atm_iv_near/far`, `iv_skew`): prefers
  the quote over `valuation.currentPrice`.

`read_price_history`/`prices` is deliberately **not** touched -- it stays
purely DB-sourced and DB-labeled, keeping the `db` vs `live` vs `quote`
provenance split clean rather than blending an unpersisted live number into
a DB-labeled block.

## Book cost and unrealized gain (`portfolio_context`)

`position.book_value_cad` (cost basis) and `portfolio_context.position_market_value`
(current worth) are different numbers -- they only match at the moment of
purchase -- and used to be reported in different digest sections with no
gain/loss figure connecting them. `read_live_position_values` (shared with
`analytics.py`'s write path) already returns `book_value_cad` per position;
`read_live_market_value` now carries it through instead of discarding it,
and computes the gap:

- `portfolio_context.cost_basis_cad` -- `position_snapshots.book_value_cad`
  (what you paid, CAD, unaffected by price movement)
- `portfolio_context.unrealized_gain_cad` -- `position_market_value - cost_basis_cad`
- `portfolio_context.unrealized_gain_pct` -- the same, as a percentage of cost basis

All three follow `price_source` -- computed against the live quote when one
was fetched, the last DB close otherwise. `position.book_value_mkt` (cost
basis in the security's own listing currency) is a **different, pre-existing
field, not a market-value figure** despite the `_mkt` suffix -- `_mkt` there
means "listing currency," inherited unchanged from the `position_snapshots`
DB column name.

## Live top-up groups

Requested from `fetch-stock-research-data` when `--no-live` is not set:
`overview`, `valuation`, `analyst`, `options`, `news`, `insider`,
`institutional`, `funds`. Never requested: `history`, `financials`,
`earnings`, `dividends` -- the database serves those. See
`docs/plans/investment-analyst-resources-skill.md` Part 1 for the full
group-by-group coverage comparison against v1's 12-group pull.

## Digest (stdout)

Aggregates only, never raw series -- this is what a calling agent reads
inline. Keys: `schema`, `ticker`, `subject` (see Subject scope, above),
`asset_class`, `as_of`, `earnings_window`,
`freshness` (per-domain stale/last), `refreshed` (domains actually
refreshed this run and their result), `position`, `ledger_summary`,
`portfolio_context` (role/account_type from the export; weight, market
value, cost basis, and unrealized gain $/% all computed live, preferring
`quote` -- see `price_source`), `prices` (count,
date range, latest close, period return, 52-week range -- no `rows`, always
DB-only, never blended with `quote`), `financials` (period count + latest
period only), `earnings` (event count + next/last report), `dividends`
(declared count + received total), `classification`, `stock_details`,
`etf_details` (without `top_holdings`/`sector_weights`), `live` (per-group
ok/empty/failed status only), `derived` (see below), `quote` (see Live
quote, above), `trace` (see Completeness trace, below), `gaps`.

## Derived metrics (`derived`, in both digest and bundle)

Deterministic math over data this skill already has in hand -- no fetch, no
judgment. Ported from `evaluate-stock-decision/scripts/scoring_worksheet.py`'s
`DERIVED_METRICS` (not imported -- see
`docs/plans/investment-analyst-resources-skill.md`'s follow-up plan for why
the two research tracks stay independent), computed by
`scripts/derived_metrics.py`.

**Live-sourced** (`compute_live_metrics`, reads the `options`/`analyst`/
`insider`/`valuation` live groups -- identical shape to v1 since both call
the same `fetch_stock_research_data` code):

- `fcf_yield` -- `valuation.freeCashflow / valuation.marketCap`
- `ev_to_ebitda`, `ev_to_revenue`, `roe` -- pass-through of the provider's
  own `enterpriseToEbitda` / `enterpriseToRevenue` / `returnOnEquity`
  (Phase 2 -- already fetched by `fetch-stock-research-data`'s `valuation`
  group, just never surfaced here before; not recomputed, including when
  negative)
- `peg_ratio` -- `valuation.trailingPegRatio` when present (realized
  trailing growth), else `valuation.pegRatio` (Phase 2)
- `put_call_oi_ratio`, `put_call_volume_ratio` -- nearest-expiry chain, puts / calls
- `atm_iv_near`, `atm_iv_far` -- average call/put implied vol at the strike nearest spot, nearest and farthest fetched expiry
- `iv_skew` -- OTM put IV (~7% below spot) minus OTM call IV (~7% above spot); positive means downside protection is bid up
- `max_oi_call_strike`, `max_oi_put_strike` -- strike with the largest open interest, nearest expiry
- `upgrades_90d`, `downgrades_90d`, `net_revisions_365d` -- analyst grade-action counts from `upgrades_downgrades`
- `net_insider_shares` -- "Net Shares Purchased (Sold)" from `insider.purchases`

**DB-sourced** (`compute_db_metrics`, reads `db_bundle["financials"]`/
`["prices"]["rows"]`) -- **not** the same metric as v1's annual-cadence
version, hence the distinct names:

- `debt_to_equity`, `current_ratio` -- surfaced from the latest
  `financial_snapshots` row (**quarterly**, not v1's annual figure)
- `net_debt_to_ebitda` -- `Net Debt / EBITDA` read from the latest quarter's
  `extra` line items (Phase 2). Deliberately **not** gross Debt/EBITDA:
  yfinance's `Total Debt` label is consumed by `_debt_to_equity`'s
  extraction and so is dropped before `extra` is built (see
  `financial_snapshots_extractor.py`'s `_BALANCE_CONSUMED_LABELS`); `Net
  Debt` is never consumed and is the leverage figure this table can
  actually support. `None` when EBITDA is missing or non-positive.
- `roic` -- NOPAT / Invested Capital, both read from the latest quarter's
  `extra` line items (`EBIT`, `Tax Provision`, `Pretax Income`, `Invested
  Capital` -- the last is yfinance's own computed figure, not re-derived
  from debt+equity-cash, since raw Total Debt/Stockholders Equity are not
  recoverable from this table). Tax rate is not clamped to `[0, 1]`; an
  unusual quarter can legitimately fall outside that range and clamping
  would be a judgment call this metric does not make. `None` on a
  loss-quarter Pretax Income or missing Invested Capital. (Phase 2)
- `revenue_growth_yoy` -- same-quarter-prior-year revenue growth from
  `financial_snapshots` (quarterly cadence, not v1's annual-over-annual)
- `net_income_latest_quarter` -- latest quarter's net income (v1's
  `net_income_latest` is the latest *annual* figure -- different name on
  purpose so nothing conflates the two)
- `return_30d`, `return_90d`, `return_365d` -- from `historical_records`,
  same algorithm as v1's `_price_return` against the DB series instead of a
  live pull. Each window is `None`, not a repeat of a shorter window's
  value, when history does not reach back that far (fixed in Phase 2 -- see
  docs/plans/implementation/phase-2/design-decisions.md, Decision 3).

Every metric is `None`, never raised, when its inputs are missing (no
options chain, no analyst coverage, insufficient price history, etc.) --
this is a normal, expected outcome for many tickers, not a data failure.

Per-security **technicals** (moving averages, drawdown, volatility, relative
strength vs `XEQT.TO`, beta/alpha) are a separate module,
`src/security_technicals.py`, not part of this skill's `derived_metrics.py`
-- see docs/plans/implementation/phase-2/design-decisions.md for why. It
operates on the same `db_bundle["prices"]["rows"]` shape but is not yet
wired into this skill's digest/bundle output.

## Completeness trace (`trace`, in digest + bundle, plus a log line)

Not analytical signal -- observability on whether this run's data pull was
any good, so degradation is visible over time. Built by `build_trace` in
`investment_analyst_resources.py` from the digest that was just built (no
separate fetch, no shadow copy of the data), then written through the shared
`src/skill_trace.py` writer this skill and `market-analyst-resources` both
use. See `docs/architecture/usage_tracking.md`.

Every field is classified **ok / missing / not-applicable**, and only
`ok + missing` forms the completeness denominator. Not-applicable is decided
per field group against *this run's* data, not just asset class:

| Group | Applicable when |
|---|---|
| `position`, `ledger_summary`, `portfolio_context`, `classification` | the ticker is owned (portfolio-only concepts) |
| `prices` | the ticker is owned or a declared research candidate (refreshable) |
| `financials`, `earnings`, `stock_details` | refreshable and not an ETF |
| `derived.financials` | not an ETF and financials data is present |
| `etf_details` | refreshable and not a stock |
| `derived.options` (7 fields) | the `options` live group returned a chain |
| `derived.analyst` (3 fields) | the `analyst` live group returned coverage |
| `derived.insider` (1 field) | the `insider` live group returned filings |
| `derived.valuation` (`fcf_yield`) | not an ETF and `valuation` came back |
| `derived.prices` (`return_30d/90d/365d`) | the DB series spans that window |
| `live.*` | `--no-live` not set; `funds` is never applicable to a stock |
| `quote` | `--no-quote` not set |

This is why a TSX name with no options chain and no analyst coverage now
reports `pct=100.0` on a run that obtained everything obtainable, instead of
81.4% with eleven phantom gaps.

`trace` shape: `{skill, subject, kind, completeness_pct, fields_ok,
fields_missing, fields_graded, fields_not_applicable, domains_ok,
domains_partial, domains_failed, domains_not_applicable, missing: [...],
not_applicable: [...], domains: {name: {ok, missing, not_applicable}}}`.

Written to `logs/SkillTrace.txt` (one scannable line, groups collapsed to
counts) and `logs/SkillTrace.jsonl` (full field names), both git-ignored:

```
2026-08-05 21:25:04 | TRACE | skill=investment-analyst-resources | subject=L | kind=stock | pct=100.0 | ok=48 | graded=48 | failed=0 | missing=- | n_a=derived.options[7],derived.analyst[3],derived.insider[1]
```

With `--run-id`, the trace is additionally appended to that run's
`audit_log.jsonl` as a `trace_recorded` event. `--no-trace` skips the digest
key and every write. `--trace-log-path` overrides the log path (testing only);
the `.jsonl` sidecar follows it.

## Run workspace (default-on)

Any invocation that produces a bundle -- `--mode read`, or the default
gate→refresh→read sequence -- **attaches to a run workspace automatically**,
via the shared `workspace.run.ensure_run`. This is deliberate, not
incidental: before it, a run only existed if the command line happened to
carry `--run-id`, a judgment call that could be skipped on any given
invocation. An audit trail cannot tolerate that -- an absent run looked
identical to "no pull happened." `--mode gate` and `--mode refresh` never
attach; neither produces a bundle to register.

`_resolve_run` in `investment_analyst_resources.py` decides `run_dir`/
`run_id`/`workspace_status`/`skip_reason` for the invocation, in this order:

| Condition | Outcome |
|---|---|
| `--mode gate` / `--mode refresh` | skipped -- no bundle to register |
| `--no-run` | skipped -- explicit opt-out |
| `--db-path` overridden (testing convention) | skipped -- so the test suite never populates the real `workspace/runs/` |
| otherwise | `ensure_run(args.run_id, ...)` -- created or attached |

Skips are not silent: `workspace_status="skipped"` and a `skip_reason` are
threaded into the completeness trace (`skill_trace.WorkspaceOutcome`), so
`logs/SkillTrace.jsonl` always carries a positive record of what happened to
the workspace, never an absence indistinguishable from "nothing ran."

`--run-id` names the run instead of letting one be auto-generated:

| Value | Behavior |
|---|---|
| omitted (still default-on) | Open a fresh run; the generated ID is printed to stderr |
| `<id>` that exists | Attach to it |
| `<id>` that does not exist | Create it under that exact name |

The third row is what makes fan-out work: an orchestrator hands the same
`--run-id` to every ticker's invocation, the first creates it, the rest attach,
and all their bundles land in one run with one audit log. The cost is that a
mistyped ID becomes a new empty run rather than an error — hence the
`note: created run workspace <id>` line on stderr whenever creation happens.
An ID that could traverse a path, or a directory that exists but is half-built,
is still a hard error. Two concurrent processes racing to create the *same*
explicit ID is handled too: `ensure_run` catches the loser's `RunExistsError`
and re-attaches to the winner's run instead of failing.

With a run attached (`docs/architecture/run_workspace.md`):

- the bundle is written to `workspace/runs/<id>/evidence/<TICKER>-<date>-resources.json`
- an `EvidenceRecord` is appended to that run's `evidence/sources.jsonl`
  (`evidence_type: market_data_bundle`, `source_name: duckdb+yfinance`, sha256
  content hash, `retrieved_at`) with status `available`, or **`partial` when
  the trace found real gaps** -- so a downstream stage reading the manifest is
  told the truth rather than assuming "present" means "complete"
- `evidence_registered` and `trace_recorded` events are appended to the run's
  audit log

`--no-run` opts out: the bundle goes to
`exports/investment-analyst-resources/<TICKER>-<date>-resources.json`, exactly
the pre-default-on behavior, and the trace records why no run exists for this
pull. `--no-run` together with `--run-id` is a usage error -- naming a run and
refusing to attach to one are contradictory. Registry failures after a
successful pull are warned about on stderr, never fatal.

## Bundle (on disk, `exports/investment-analyst-resources/<TICKER>-<date>-resources.json`)

```json
{
  "schema": "investment-analyst-resources.v1",
  "ticker": "PLTR",
  "subject": {"registered": true, "owned": true, "declared_status": null,
              "rationale": null, "declared_at": null, "status": "owned", "scope": "portfolio"},
  "provider_symbol": "PLTR",
  "asset_class": "stock",
  "as_of": "2026-08-04",
  "earnings_window": false,
  "freshness": {"prices": {}, "earnings": {}, "dividends": {}, "financials": {}, "classification": {}, "positions": {}},
  "refreshed": {"prices": {"tickers": 1, "rows": 5, "error": null}},
  "db": {
    "position": {}, "ledger_summary": {}, "ledger": [],
    "prices": {"rows": [], "count": 252, "start": "...", "end": "...", "latest_close": 0, "period_return_pct": 0, "week52_low": 0, "week52_high": 0},
    "financials": [], "earnings": [],
    "dividends": {"declared": [], "received": {}},
    "stock_details": {}, "etf_details": null,
    "classification": {},
    "portfolio_context": {"role": "Growth", "weight_pct": 4.4, "position_market_value": 276.5,
                           "cost_basis_cad": 261.72, "unrealized_gain_cad": 14.78, "unrealized_gain_pct": 5.6,
                           "account_type": "TFSA", "price_source": "live_quote"}
  },
  "live": {"valuation": {}, "analyst": {}, "options": {}, "news": [], "insider": {}, "institutional": {}, "overview": {}, "funds": {}},
  "derived": {"fcf_yield": 0, "put_call_oi_ratio": 0, "iv_skew": 0, "return_30d": 0, "...": "..."},
  "quote": {"price": 162.66, "as_of": "2026-08-04T22:07:45", "source": "fast_info", "previous_close": 160.02, "day_high": 163.10, "day_low": 159.80, "year_high": 207.18, "year_low": 107.27},
  "trace": {"fields_ok": 41, "fields_total": 45, "completeness_pct": 91.1, "domains_ok": 9, "domains_empty": 1, "domains_failed": 0, "missing_fields": []},
  "errors": {},
  "gaps": []
}
```

`db.portfolio_context.price_source` is `"live_quote"` or `"db_close"`,
naming which price the value/weight figures above actually used.

Not `Read` by an LLM -- consumed programmatically by the next script
(worksheet builder / validator, once the Phase 1b judgment agent exists).
Local-only: `exports/` is gitignored.

## Scope boundary

Fixed queries only, against the configured `config.DATABASE_PATH` -- no
arbitrary SQL, no caller-supplied database path beyond `--db-path` (testing
only). No writes to `Knowledge-Base/`. No portfolio-math recomputation (see
SKILL.md). No trades.
