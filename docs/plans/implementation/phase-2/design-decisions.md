# Phase 2 Design Decisions

**Date:** 2026-08-10
**Status:** Decided before implementation. Binding on Phase 2 code.

---

## Decision 1 — Reuse boundary between Phase 2 technicals and the dashboard analytics layer

### The question

Phase 2 builds a technicals module computing moving averages, beta, drawdown,
volatility, and relative strength. `src/portfolio_metrics.py` already computes
volatility, drawdown, Sharpe, Sortino, and alpha/beta for the dashboard. **Is Phase 2
duplicating the dashboard?**

### What the investigation found

**a) No functional duplication exists today.** The existing functions are
**portfolio-level**: they take a whole-portfolio valuation series (`list[dict]`
hardcoded to the keys `date` / `portfolio_value`, or adjusted-return rows keyed
`date` / `return`) and feed the `/portfolio` page's risk tiles via
`analytics.portfolio_report` → `GET /api/portfolio/report`. Phase 2 needs
**per-security** metrics for a single ticker's OHLCV out of `historical_records`,
consumed by the Phase 3 worksheet builder. A grep of `dashboard/web/src/app/holdings`
and `.../stocks` confirms **no per-security beta, drawdown, or volatility is surfaced
anywhere in the frontend** — the only hits for those terms are on the portfolio page
and in `concentration-card.tsx`.

**b) The overlap applies to one of three deliverables, and within it, ~3 formulas.**

| Phase 2 deliverable | Overlap with dashboard |
|---|---|
| Technicals (MA, beta, drawdown, volatility, relative strength) | Formula-level only: cummax drawdown, `std → × √252` volatility, `cov/var` beta |
| Ratio module (EV/EBITDA, EV/Revenue, Debt/EBITDA, ROE, ROIC, PEG) | **None** — nothing in the repo computes these |
| `return_30d==90d==365d` mislabeling fix | **None** — defect is in a skill script, see Decision 3 |

**c) `calculate_benchmark_stats` is already generic.** Despite its portfolio-flavored
parameter names (`portfolio_returns`, `benchmark_returns`), `src/portfolio_metrics.py:334`
is a plain "series A vs series B" function over `[{date, return}]` rows. Nothing in its
body is portfolio-specific. It returns beta, alpha, tracking error, and information
ratio, and it already handles inner-join date alignment and a `minimum_overlap` guard
that returns `None` rather than a misleading statistic on thin history.

**d) The repo has documented precedent for deliberate duplication of pure math.**
`.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` lines 15–20:
formulas were "ported from `scoring_worksheet.py`, not imported … duplicating pure math
here (rather than reaching into another skill's private functions) keeps the two
research tracks independent."

**e) Importing from `src/` inside skill scripts is already established** —
`db_resources.py`, `freshness_gate.py`, `investment_analyst_resources.py`,
`rubric.py`, and `scoring_worksheet.py` all do `sys.path.insert(0, str(ROOT / "src"))`.
So a shared `src/` module is reachable from either track if one is ever wanted.

### Options considered

**Option A — Extract shared primitives out of `portfolio_metrics.py`** into Series-taking
pure functions (`volatility_from_returns`, `max_drawdown_from_values`,
`beta_alpha_from_returns`), then adapt both the portfolio and per-security callers.

- *For:* one implementation per formula; a fix propagates to both consumers.
- *Against:* modifies working, dashboard-critical, tested code for a consumer that does
  not exist yet. `calculate_benchmark_stats` does not decompose cleanly — it performs
  merge-on-date, beta, alpha, tracking error, and information ratio in one pass, so
  extracting "just beta" strands the rest. Expands the Phase 2 diff well past its gate
  and forces re-verification of `tests/test_analytics.py` and
  `tests/test_dashboard_api.py`. The payoff is deduplicating three textbook formulas
  that have not changed in decades.

**Option B — Standalone `src/security_technicals.py`, no refactor.** *(chosen)*

- *For:* zero regression risk to the dashboard; keeps the phase exactly the size that
  was approved; consistent with the repo precedent in (d).
- *Against:* beta logic conceptually exists twice; conventions could drift.
  **Both mitigated below.**

**Option C — Put the technicals beside the skill** in
`.claude/skills/investment-analyst-resources/scripts/`.

- *For:* follows CLAUDE.md's skill-locality rule; the OHLCV is already in that skill's
  `db_bundle["prices"]["rows"]`; all three Phase 2 deliverables would land in one module.
- *Against:* the roadmap's own verification section calls for
  `python -m unittest tests.test_security_technicals`, and Phase 3's
  `src/workspace/investment_worksheet.py` would then have to import skill-private
  scripts — the dependency runs backwards. The technicals are also not genuinely
  single-skill-owned: `/holdings/[symbol]` is a plausible future second consumer.

### Decision: **Option B**, with the beta case solved by import rather than refactor

1. **Create `src/security_technicals.py`.** Per-security, OHLCV in.
2. **Do not modify `src/portfolio_metrics.py` or `src/analytics.py`.** Enforced by the
   phase gate (`git diff --stat` must show no change to either).
3. **Import `portfolio_metrics.calculate_benchmark_stats` for beta and alpha vs
   `XEQT.TO`.** Per finding (c) it is reusable as-is. Beta is the one formula here with
   real subtleties — date alignment, minimum overlap, risk-free handling — so this
   removes the strongest duplication concern at the cost of one import statement and
   zero changes to existing code.
4. **Write moving averages, drawdown, volatility, and relative strength locally** as
   small pure functions over a single price series, **importing `ANNUALIZATION_PERIODS`
   and `DEFAULT_RISK_FREE_RATE` from `src/config.py`** — already the shared source
   `portfolio_metrics.py` reads. Sharing the *constants* makes conventions match by
   construction without sharing code.
5. **State convention parity in the module docstring**: `ddof=0`, the same
   annualization, and which price field is used (adjusted vs raw close), with a pointer
   to `portfolio_metrics.py` as the portfolio-level counterpart. The conventions are
   where the real bugs live; the algebra is not.
6. **Defer extraction of shared primitives** until a genuine second consumer exists —
   realistically when the dashboard surfaces per-security beta on `/holdings/[symbol]`.
   Refactor with two known consumers and real requirements, not one speculative one.

### Revisit trigger

Reopen this decision when **either** holds:

- a third consumer of the same formulas appears, or
- `/holdings/[symbol]` (or any dashboard view) needs per-security beta / drawdown /
  volatility — at which point `src/security_technicals.py` already exists and the
  question becomes "should `analytics.py` call it", not "should we duplicate it".

Phase 5's benchmark verdict (full replacement / dual system / targeted adoption) is the
natural checkpoint to re-examine this.

---

## Decision 2 — Resolved: extend `derived_metrics.py` in place; no collision

`.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` already
computes `_debt_to_equity` (line 351) and `_current_ratio` (line 356) from
`financial_snapshots` — the **same table** Phase 2's ratio module reads, at the same
quarterly cadence. Two modules deriving leverage ratios from one table with possibly
different period conventions would be a live conflict; portfolio-level Sharpe versus
per-security drawdown is not.

**Resolution: extend `derived_metrics.py` in place.** Implemented as six new
functions (`_ev_to_ebitda`, `_ev_to_revenue`, `_roe`, `_peg_ratio`,
`_net_debt_to_ebitda`, `_roic`) registered into the existing `LIVE_METRICS`/
`DB_METRICS` dicts, not a new module. This was chosen over a standalone module
because the ratios reuse the exact `live_data`/`db_bundle` plumbing `derived_metrics.py`
already has — building a separate module would mean re-plumbing the same data access
for no benefit, and CLAUDE.md's skill-locality rule (single-skill-owned Python belongs
beside the skill, not in `src/`) applies here since `investment-analyst-resources` is
still the only consumer.

**No collision occurred, confirmed against real data, not assumed:**

- Four of the six (`ev_to_ebitda`, `ev_to_revenue`, `roe`, `peg_ratio`) are **live
  pass-throughs** of `enterpriseToEbitda` / `enterpriseToRevenue` / `returnOnEquity` /
  `trailingPegRatio`+`pegRatio` — fields `fetch-stock-research-data`'s `valuation`
  group (`VALUATION_INFO_KEYS`) already fetches today but this skill never surfaced.
  Confirmed live against AAPL: `enterpriseToEbitda=27.36`, `returnOnEquity=1.49`,
  `pegRatio=2.53` all present in `yfinance.Ticker(...).info` under the exact keys
  already in `VALUATION_INFO_KEYS`. **Zero new fetch required.**
- The other two (`net_debt_to_ebitda`, `roic`) read `financial_snapshots.extra` line
  items — `Net Debt`, `EBITDA`, `EBIT`, `Tax Provision`, `Pretax Income`, `Invested
  Capital` — none of which `_debt_to_equity`/`_current_ratio` touch (those read only
  the named `debt_to_equity`/`current_ratio` columns, sourced from `Total Debt` /
  `Stockholders Equity`, which are *consumed* labels and therefore absent from `extra`
  entirely). Confirmed against a real persisted row (NVDA, `period_end_date
  2026-04-30`): `extra.income_statement` contains `EBIT`, `EBITDA`, `Tax Provision`,
  `Pretax Income`; `extra.balance_sheet` contains `Invested Capital`. Confirmed against
  AAPL/MCD/T rows that `Net Debt` is present and `Total Debt` is consistently absent
  (`None`), which is *why* the new leverage ratio is named `net_debt_to_ebitda`, not
  `debt_to_ebitda` — the gross figure is not recoverable from this table, so the
  function is named for what it actually measures rather than implying parity with a
  figure it cannot compute.

So this is two ratio pairs computed from one table with **no overlapping raw inputs**,
not two implementations of the same ratio. `derived_metrics.py`'s docstring and
`resource-contract.md` both record this explicitly.

Naming discipline followed: matching the existing `net_income_latest_quarter` (not
`net_income_latest`) precedent, nothing here is named to imply parity with an annual or
TTM figure it is not — these are quarterly-snapshot-derived ratios, named as such in the
docstrings.

---

## Decision 3 — Scope of the windowed-return defect fix

The `return_30d == return_90d == return_365d` defect is entirely inside
`.claude/skills/investment-analyst-resources/scripts/derived_metrics.py::_return_over`
(line 410). The window math itself is correct; the bug is the fallback at line 431:

```python
past = [p for p in points if p[0] <= cutoff]
past_close = past[-1][1] if past else points[0][1]
```

When history is shorter than the requested window, `past` is empty and **all three
windows silently fall back to the earliest available price**, producing three identical
returns presented as three distinct windows.

The fix returns an explicit insufficient-history signal instead of a silently wrong
number. This touches no dashboard code and no `src/` module.

---

## Decision 4 — Reversal: technicals becomes a skill after all (2026-08-10)

**Decisions 1-3 stand. This one supersedes Decision 1's Option C rejection only.**

Phase 2 shipped `src/security_technicals.py` as a plain importable module. The owner's
objection afterward was correct and this decision records the reversal: the module was
not runnable by an agent, and nothing it computed was traceable — no `SKILL.md`, no CLI,
no run attachment, no evidence entry, no audit event, no log line.

### What Option C got wrong

Decision 1 rejected "put the technicals beside the skill" partly on the grounds that
Phase 3's `src/workspace/investment_worksheet.py` "would then have to import
skill-private scripts — the dependency runs backwards." That reasoning was sound but the
conclusion drawn from it was too broad: it conflated **where the math lives** with
**whether a skill exists**. Those are independent. The math can stay in `src/` (so no
dependency runs backwards) *and* a skill can wrap it (so an agent can invoke it with full
traceability). That is what was built.

### Two findings that changed the analysis

1. **`calculations/` exists, is wired, and had no producer.** `src/workspace/paths.py:23`
   creates a `calculations/` directory in every run and `src/workspace/manifest.py:75`
   already surfaces its contents as `calculation_paths` in `context_manifest.yaml` — but
   nothing in the repository wrote there. The workspace schema distinguishes `evidence/`
   (facts obtained from outside, carrying provenance) from `calculations/` (deterministic
   arithmetic over data the run already holds). Technicals is the second kind. This skill
   is that directory's first producer.
2. **No canonical benchmark price reader existed.** `src/analytics.py:689` runs an ad-hoc
   inline query inside `get_trend_overlays`, `analytics.get_benchmark_returns` fetches
   live from yfinance and never persists, `market_data.ensure_benchmark_history` only
   writes, and `db_resources.read_price_history` was never called with a benchmark
   ticker. `security_technicals.py`'s own `benchmark_rows` parameter was supplied by
   **tests only**. Beta, alpha, and relative strength all need that series.

### What was built

- **`.claude/skills/read-security-price-history/`** — a dependency-only skill following the
  `read-portfolio-classification-data` template (9-line `SKILL.md`, a
  `references/database-contract.md`, one script, no argparse). Provides
  `read_security_prices` and the previously-missing `read_benchmark_prices`. Its
  connect/validate helpers duplicate `db_resources.py`'s — the same duplication
  `db_resources.py`'s own docstring already blesses, for the same reason.
- **`.claude/skills/security-technicals/`** — the agent-invoked skill: `SKILL.md` with a
  workflow and guardrails, `references/technicals-contract.md`, and a CLI that attaches to
  a run by default, writes the artifact to `calculations/`, registers it as
  `derived_calculation` evidence, appends a `calculation_written` audit event, and emits a
  completeness trace.
- **`src/security_technicals.py` — unmoved and unchanged.** All 16 existing tests pass
  untouched, which is the proof the math did not move.

### The trace rule that mattered most

Insufficient history grades as **`not_applicable`, never `missing`**. A 60-day-old holding
genuinely cannot have an SMA-200; grading that as a gap would report a healthy run as
incomplete and bury real gaps in noise — the exact failure `src/skill_trace.py`'s module
docstring documents (a healthy run reading 81% complete with eleven bogus "missing"
fields). Only real absences — a resolvable ticker with no stored rows, or a benchmark with
no history — grade as `missing`.

### What this does *not* change

Decision 1's core rule holds: **`src/portfolio_metrics.py` and `src/analytics.py` remain
untouched**, and `beta_alpha` still delegates to `calculate_benchmark_stats` by import.
The `ddof` asymmetry recorded in `HANDOFF.md` is still unfixed, still deliberately.

---

## Summary of the file boundary for Phase 2

| Action | Files |
|---|---|
| **Create** | `src/security_technicals.py`; `tests/test_security_technicals.py` |
| **Modify** | `.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` (Decisions 2 and 3, in place — no new ratio module file); `.claude/skills/investment-analyst-resources/references/resource-contract.md`; `tests/test_investment_analyst_resources.py` |
| **Import from, never edit** | `src/portfolio_metrics.py` (`calculate_benchmark_stats`), `src/config.py` (constants) |
| **Must not appear in the diff** | `src/portfolio_metrics.py`, `src/analytics.py`, `dashboard/**`, `.claude/skills/fetch-stock-research-data/**` (no new live fields needed) |

**As implemented** (2026-08-10): matches the table above exactly. No new ratio-module
file was created — Decision 2's "extend" path made a new file unnecessary once the live
pass-through fields were confirmed already-fetched and the DB-sourced ratios were
confirmed to read `extra` fields the existing functions never touch.

---

## Decision 5 — Benchmark default kept; a live current-price quote added (2026-08-10)

Two questions raised after Decision 4 shipped, both resolved the same day.

### Benchmark: keep `XEQT.TO` as the default, `VFV.TO` stays a `--benchmark` override

`XEQT.TO` (`config.DEFAULT_BENCHMARK_SYMBOL`) is a globally-diversified, multi-region
equity ETF — the repo's proxy for "the whole portfolio's market" (`analytics.py:2004`
treats it as such for concentration checks, and it is also the portfolio-level beta/alpha
benchmark on the dashboard). For a *single* US-listed security, the more conventional
beta benchmark is the market that security actually trades in — `VFV.TO`, which
`config.py`'s own comment already documents as existing specifically as an "S&P 500
stand-in... so the overlay stays in the portfolio's currency," and which
`market_data.ensure_benchmark_history` already fetches and stores alongside XEQT.

Kept `XEQT.TO` as the default rather than switching: it keeps `security-technicals`
consistent with the dashboard's own portfolio-level convention, so the same benchmark
choice isn't silently different between the two surfaces unless asked. `--benchmark`
already existed and needed no code change — `--benchmark VFV.TO` is the answer whenever a
single US-listed holding's beta against "the market" (rather than against this
portfolio's asset-allocation benchmark) is what's being asked. Documented in `SKILL.md`'s
flag description rather than silently left for someone to discover.

### Live quote: one optional network call, sharply scoped

The technicals (`SMA`, drawdown, volatility, relative strength, beta/alpha) are unaffected
by staleness — they're long-window statistics over stored history, and one day's price
barely moves them. But `prices.latest_close` is only as fresh as the last pipeline
ingestion, and the artifact never distinguished "stored as of the last run" from "right
now." `investment_analyst_resources._fetch_latest_quote` already solved exactly this
problem for the sibling skill with a single cheap `yfinance` `fast_info` call, independent
of its own `--no-live` flag via a dedicated `--no-quote`.

Reused the same pattern rather than inventing a new one: `_fetch_latest_quote` (a trimmed
copy — `price`/`as_of`/`source`/`previous_close` only, no day/year range, which is
`investment-analyst-resources` territory, not a price-technicals concern), a `--no-quote`
flag defaulting to fetch-on, and a `quote` artifact field kept clearly separate from the
history-derived `technicals` block. `read_price_history.py` gained one new function,
`resolve_provider_symbol` — every other read in that module deliberately does *not*
require a verified Yahoo mapping (`read_benchmark_prices`' docstring: requiring one "would
exclude a benchmark whose history is already stored"), but a real network call needs a
real symbol, so this one function does require `ticker_provider_mappings`' `provider =
'yahoo' AND verification_status = 'verified'` row, mirroring `db_resources.resolve_ticker`.

This is the one place the skill's "reads only, never fetches" guardrail gets a carve-out.
`SKILL.md`, the CLI's own `--help` description, and `technicals-contract.md`'s Boundary
table were all updated to state the carve-out explicitly rather than leave the guardrail
overstated. The trace gained a third domain, `quote`, with the same three-way split as
`technicals` but inverted in spirit: skipped-by-`--no-quote` is `not_applicable` (nothing
was attempted), while an attempted fetch that came back empty — no verified mapping, or
yfinance itself failing — is a real `missing`, because the quote was genuinely obtainable
in principle. 13 new tests cover `resolve_provider_symbol`, the trace's three quote
states, the artifact's `quote` field, and the CLI path with `_fetch_latest_quote` mocked
(no real network access anywhere in the test file); the full skill suite grew from 23 to
36 tests, all passing.

---

## Decision 6 — Ownership-based backfill was too shallow for the technicals it now feeds (2026-08-10)

Testing Decision 5's benchmark choice against a recently-bought real holding surfaced a
gap upstream of `security-technicals` entirely: `market_data.sync_market_data` only ever
backfilled `historical_records` from `first_owned_date` forward. A name bought a month ago
therefore has ~20 trading bars stored — nowhere near the ~200 an SMA-200 needs or the ~252
a 365-day relative-strength window needs — and no flag in the sync CLI could fix that,
because the sync itself never reached further back than ownership. This is a sync-layer
gap, not a `security_technicals.py` bug: `compute_security_technicals` was already
correctly reporting `null` for what it could not compute (Decision 1's local functions,
Decision 4's trace rule) — the fix is giving it more to compute *from*.

### The fix: a history floor independent of ownership

Added `config.MINIMUM_PRICE_HISTORY_DAYS = 400` (calendar days, ~275 trading bars — enough
slack above the 252-bar ceiling that a handful of provider gaps or holidays don't erode the
window). `MarketTarget.history_floor(today)` returns
`min(first_owned_date, today - MINIMUM_PRICE_HISTORY_DAYS)`, so ownership can only push the
backfill *further* back, never short of the floor. `fetch_ranges` requests up to two
`[start, end)` windows per ticker — a one-time "head gap" between the floor and whatever is
already stored (`earliest_market_date`), plus the ordinary incremental tail after
`latest_market_date` — mirroring the two-range shape `ensure_benchmark_history` already
used for benchmarks. The head gap is self-limiting: once stored history reaches the floor,
the floor keeps advancing by one day at a time while the stored minimum stays fixed, so it
naturally stops recurring without any extra bookkeeping. `_has_trading_weekday` skips a
range that provably covers only a weekend, since requesting one produces a misleading
"possibly delisted" log line from yfinance for zero benefit. A ticker newer than 400 days
old is still only backfilled to its actual listing date (yfinance returns what it has); that
remains a real, reportable data gap for the technicals layer, not something this change
papers over.

**Scope check, same as Decision 1's:** this touches `market_data.py`/`config.py`, neither
of which is `portfolio_metrics.py` or `analytics.py` — the dashboard's own valuation-series
ingestion is unaffected, and `--full` continues to mean "restart from the floor," now
correctly rather than from ownership alone. 6 new/updated tests in
`MarketDataSyncTest`/`MarketTargetRangeTest` cover the floor computation, the one-time head
gap, the steady-state tail-only case, and the weekend skip.

### The fallout: the benchmark didn't resolve

Decision 5 kept `XEQT.TO` (the Yahoo form, from `config.DEFAULT_BENCHMARK_SYMBOL`) as the
default `--benchmark` value. But `tickers.ticker_symbol` stores the bare canonical symbol
(`XEQT`) — the `.TO` suffix lives only in `ticker_provider_mappings.provider_symbol`. Before
this fix, `read_price_history.resolve_ticker` did an exact match only, so the default
benchmark silently failed to resolve and every beta/alpha/relative-strength field came back
`null` for every ticker unless `--benchmark XEQT` (undocumented, bare form) was passed
instead. Fixed by trying the exact symbol first, then a Canadian-suffix-stripped fallback
(`_bare_symbol`, reusing `config.YFINANCE_CANADIAN_SUFFIXES`) — exact-first so a ticker
genuinely stored *with* a suffix (e.g. a dual-listed name kept as `FOO.TO`) still resolves to
itself rather than being redirected to an unrelated `FOO`. 3 new tests cover the Yahoo-form
fallback, the exact-match-wins case, and the pre-existing case-insensitive match.

### Why this wasn't caught by Decision 5's own tests

Decision 5's fixtures seeded the benchmark ticker under its Yahoo form (`XEQT.TO`) directly,
which happened to match `resolve_ticker`'s exact-match query — masking the mismatch that a
real database (where `tickers` never stores the suffix) would hit immediately. The test
fixtures were corrected alongside the fix (`BENCHMARK_STORED = "XEQT"`, seeded separately
from the Yahoo-form `BENCHMARK` constant the CLI is invoked with) so the suite now exercises
the real shape instead of accidentally sidestepping the bug.
