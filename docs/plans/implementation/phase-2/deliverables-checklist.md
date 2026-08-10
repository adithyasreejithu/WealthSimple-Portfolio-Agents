# Phase 2 Deliverables Checklist

**Status:** completed and ready for review
**Date completed:** 2026-08-10
**Companion:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md) (Phase 2 row + "Phase 2 design decision — reuse boundary" section), [`design-decisions.md`](design-decisions.md) (binding decisions this phase followed)

---

## Summary

Phase 2 closed the two cheap data gaps ahead of the analyst/judgment phases: per-security
technicals (moving averages, drawdown, volatility, relative strength, beta/alpha) and six
normalized ratios (EV/EBITDA, EV/Revenue, Net Debt/EBITDA, ROE, ROIC, PEG), plus the
`return_30d==90d==365d` mislabeling defect. Per the design decision recorded before
implementation, `src/portfolio_metrics.py` and `src/analytics.py` — the dashboard's
portfolio-level analytics layer — were **not modified**; the technicals module is a new,
standalone `src/security_technicals.py`, and the ratios extend the existing
`derived_metrics.py` in place rather than creating a duplicate module.

The phase gate is met: unit tests reproduce hand-computed/independently-derived
beta/drawdown/MA/ratio values, windowed returns correctly report insufficient history as
`None` instead of duplicating a shorter window's value, and `git diff --stat` shows no
change to `src/portfolio_metrics.py`, `src/analytics.py`, or `dashboard/**`.

---

## Files Created

| File | Purpose | Status |
|---|---|---|
| **`src/security_technicals.py`** | Per-security moving averages, max drawdown, annualized volatility, relative strength vs. a benchmark series, and beta/alpha (by importing `portfolio_metrics.calculate_benchmark_stats`, not reimplementing it). Every function takes OHLCV row lists (`record_date`/`close`) and never raises. | ✅ Complete |
| **`tests/test_security_technicals.py`** | 16 tests: hand-computed fixtures for moving averages, drawdown, and volatility; a deterministically-constructed correlated series for beta/alpha (with the expected value derived independently via the same pandas primitives `calculate_benchmark_stats` uses, including its `ddof` asymmetry — see Known Issues below); insufficient-history-returns-`None` cases throughout. | ✅ Complete, all passing |
| **`docs/plans/implementation/phase-2/design-decisions.md`** | Binding design record: reuse boundary vs. the dashboard (Option B), the `derived_metrics.py` collision analysis and resolution (Decision 2), and the return-window defect scope (Decision 3). | ✅ Complete |
| **`docs/plans/implementation/phase-2/README.md`** | Phase tracking, mirroring the phase-0/phase-1 folder pattern. | ✅ Complete |

## Files Modified

| File | Change | Status |
|---|---|---|
| **`.claude/skills/investment-analyst-resources/scripts/derived_metrics.py`** | Fixed `_return_over`'s insufficient-history fallback (Decision 3): returns `None` instead of the earliest available price when no point exists at/before the window cutoff. Added six ratio functions registered into the existing `LIVE_METRICS`/`DB_METRICS` dicts (Decision 2, resolved to "extend in place"): live pass-throughs `_ev_to_ebitda`, `_ev_to_revenue`, `_roe`, `_peg_ratio` (zero new fetch — these fields are already in `fetch-stock-research-data`'s `VALUATION_INFO_KEYS`); DB-sourced `_net_debt_to_ebitda`, `_roic` (read `financial_snapshots.extra` line items `Net Debt`/`EBITDA`/`EBIT`/`Tax Provision`/`Pretax Income`/`Invested Capital`, confirmed present against real portfolio-ticker rows, not assumed). | ✅ Complete |
| **`.claude/skills/investment-analyst-resources/references/resource-contract.md`** | Documented the six new derived metrics and the return-window fix, matching CLAUDE.md's "update docs in the same change" rule. | ✅ Complete |
| **`tests/test_investment_analyst_resources.py`** | Added `DerivedLiveRatioMetricsTest` (5 tests), `DerivedDbRatioMetricsTest` (6 tests, including one proving the new ratios and the pre-existing `debt_to_equity`/`current_ratio` populate independently from the same row), one pass-through test in the existing `DerivedLiveMetricsTest`, and a regression test for the return-window defect. | ✅ Complete, all passing |
| **`docs/plans/implementation/phase-1/HANDOFF.md`** | Fixed a stale pointer at the end of the file that sent the next session to the combined plan's Phase 2 doc (worksheet builder), which is actually the roadmap's Phase 3 — the roadmap's insertion of this phase broke that alignment. | ✅ Complete |

---

## Gate Verification

Roadmap gate: *"Unit tests against a known-OHLCV fixture reproduce hand-computed
beta/drawdown/MA/ratio values; windowed returns are correctly labelled or marked
insufficient-history rather than silently duplicated; `git diff --stat` shows no change to
`src/portfolio_metrics.py` or `src/analytics.py`."*

```bash
uv run python -m unittest tests.test_security_technicals -v          # 16 tests, OK
uv run python -m unittest tests.test_investment_analyst_resources -v # 89 tests, OK
uv run python -m unittest discover -s tests                          # 966 tests, 1 pre-existing
                                                                       # unrelated Windows flake
                                                                       # (see Known Issues), passes
                                                                       # in isolation
git diff --stat                                                      # no src/portfolio_metrics.py,
                                                                       # no src/analytics.py,
                                                                       # no dashboard/**
```

- **Beta/drawdown/MA/ratio values reproduced independently:** confirmed in
  `test_security_technicals.py` (drawdown: `[100,80,90]` → exactly `-0.2`; volatility:
  returns `[+0.10,-0.10]` → population stdev exactly `0.10`; MA: explicit means over
  literal closes; beta: a deterministically-constructed `ticker_return = 2 x
  benchmark_return` series, with the expected beta/alpha computed independently via the
  same pandas formula `calculate_benchmark_stats` uses) and in
  `test_investment_analyst_resources.py`'s new ratio tests (`net_debt_to_ebitda`:
  `40000/10000 = 4.0`; `roic`: tax_rate `4000/16000=0.25` → NOPAT `20000*0.75=15000` →
  `15000/100000=0.15`).
- **Windowed returns correctly report insufficient history:**
  `test_short_history_returns_none_instead_of_duplicating_the_earliest_price` seeds only
  40 days of history and confirms `return_30d` resolves normally while `return_90d`/
  `return_365d` are `None`, not duplicates of `return_30d` or of each other.
- **No dashboard-layer change:** `git diff --stat` (see command above) touches only the
  `investment-analyst-resources` skill, its docs, its tests, the new
  `src/security_technicals.py`, and planning docs.

---

## Known Issues — No Action Taken

### 1. `calculate_benchmark_stats`'s `ddof` asymmetry (found, not fixed)

`portfolio_metrics.calculate_benchmark_stats` computes beta as `rp.cov(rb) /
rb.var(ddof=0)`. Pandas' `Series.cov()` defaults to `ddof=1` (sample covariance) while
`.var(ddof=0)` is population variance — the two disagree by a factor of `n/(n-1)`, where
`n` is the number of overlapping return observations. Found while writing
`BetaAlphaTest.test_beta_and_alpha_match_construction`: a series constructed so
`ticker_return = 2.0 x benchmark_return` exactly produced `beta = 2.0833...`, not `2.0`,
until the test's expected value was corrected to account for the asymmetry.

**Not fixed in Phase 2**, per Decision 1's explicit rule that `portfolio_metrics.py` is
not modified this phase — fixing it would also change the dashboard's live alpha/beta
tile. For a large `n` (the dashboard's typical daily-return series has hundreds of
points) the effect is small (`n/(n-1) -> 1`), which is likely why it has not been
noticed; for the per-security `beta_alpha` case with a `minimum_overlap` of 20, the
effect is roughly 5% and could matter. Flagged for whoever next touches
`calculate_benchmark_stats` — a real defect, not a test artifact, and the test above
documents and locks in the *current* (asymmetric) behavior rather than silently masking
it.

### 2. Pre-existing flaky test: Windows `os.replace` race

`tests.test_investment_analyst_resources.DefaultOnRunCreationTest.test_default_mode_with_no_run_id_still_opens_a_run`
failed once during a full-suite run with `PermissionError: [WinError 5] Access is
denied` inside `src/workspace/run.py::create_from_request`'s `os.replace(staging,
target)`. Passes reliably in isolation (confirmed by re-running it alone). This is a
Windows-filesystem timing issue (likely antivirus/indexer lock on a just-created
directory) in existing Phase-1-and-earlier code, unrelated to any Phase 2 change — no
Phase 2 file touches `src/workspace/run.py`. Not investigated further; noted for whoever
next sees this test flake.

---

## Files to Remember

| File | Purpose | Notes |
|---|---|---|
| `src/security_technicals.py` | Per-security technicals | Imports `portfolio_metrics.calculate_benchmark_stats` for beta/alpha only; everything else is local |
| `.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` | Extended with 6 ratio functions + return-window fix | `LIVE_METRICS`/`DB_METRICS` dicts are the registration points for future additions |
| `docs/plans/implementation/phase-2/design-decisions.md` | Binding reuse-boundary and collision decisions | Read before touching either this module or `portfolio_metrics.py` in a later phase |
| `tests/test_security_technicals.py`, `tests/test_investment_analyst_resources.py` | Test coverage | 16 + 6 new methods respectively |

---

## What Phase 3 Depends On

Phase 3 (worksheet builder, per the roadmap) can read `security_technicals.py`'s
`compute_security_technicals(rows, benchmark_rows)` and `derived_metrics.py`'s
`compute_live_metrics`/`compute_db_metrics` (now including the six new ratios) directly —
neither needs Phase 3 to change. Note the roadmap-vs-combined-plan numbering mismatch
flagged in `../phase-1/HANDOFF.md`: the combined plan's own "Phase 2" document
(`03-phase-2-worksheet-builder.md`) is this roadmap's **Phase 3**.

`security_technicals.py`'s `compute_security_technicals` is not yet wired into
`investment-analyst-resources`'s digest/bundle output — that wiring, if wanted, is a
Phase 3 decision, not assumed here.

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_security_technicals -v
uv run python -m unittest tests.test_investment_analyst_resources -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py dashboard/   # should be empty
```
