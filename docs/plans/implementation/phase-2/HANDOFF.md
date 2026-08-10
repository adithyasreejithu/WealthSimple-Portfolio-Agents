# Phase 2 Handoff — Key Changes and Context for Future Sessions

**Date:** 2026-08-10
**Status:** Phase 2 complete, plus a follow-on skill refactor (see below)

> **Read this first.** After Phase 2 shipped, `security_technicals` was wrapped in a
> proper skill so an agent can invoke it and its output is traceable. Two new skills
> exist — `security-technicals` (agent-invoked) and `read-security-price-history`
> (dependency-only) — and `runs/<id>/calculations/` now has its first producer.
> `src/security_technicals.py` did **not** move; the skill wraps it. The full record,
> including what the original Option C rejection got wrong, is
> [`design-decisions.md`](design-decisions.md) **Decision 4**. Everything below about
> the math, the `ddof` finding, and the `extra`-blob verification still stands.

---

## What Phase 2 Built

Closed the two "cheap" Doc A gaps ahead of any LLM judgment stage:

1. **Per-security technicals** — new `src/security_technicals.py`: moving averages,
   max drawdown, annualized volatility, relative strength vs. a benchmark series, and
   beta/alpha (the last by **importing** `portfolio_metrics.calculate_benchmark_stats`,
   not reimplementing it).
2. **Six normalized ratios** — extending
   `.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` in place:
   `ev_to_ebitda`, `ev_to_revenue`, `roe`, `peg_ratio` (live pass-throughs, zero new
   fetch), `net_debt_to_ebitda`, `roic` (DB-sourced from `financial_snapshots.extra`).
3. **The `return_30d==90d==365d` mislabeling defect** — fixed in the same file.

**Read `design-decisions.md` before touching any of this again.** It is the binding
record of *why* the technicals module is standalone (not merged into
`portfolio_metrics.py`) and *why* the ratios extend `derived_metrics.py` rather than
becoming a fourth module. Both decisions were made with real data checked against a
live yfinance pull and the actual persisted DB, not assumed — the evidence is in that
file, not just the conclusion.

All new/modified tests pass: 16 in `test_security_technicals.py`, plus 11 new methods
across `DerivedLiveMetricsTest`/`DerivedLiveRatioMetricsTest`/`DerivedDbMetricsTest`/
`DerivedDbRatioMetricsTest` in `test_investment_analyst_resources.py`. Full suite: 966
tests, 1 unrelated pre-existing Windows flake (see below).

---

## Critical Things to Know

### 1. The dashboard's analytics layer was not touched — verify this stays true

The whole point of Decision 1 in `design-decisions.md` was that `src/portfolio_metrics.py`
and `src/analytics.py` are dashboard-critical and out of scope for this phase. Confirmed
via `git diff --stat`: neither file, nor anything under `dashboard/`, appears in this
phase's diff. **If a future phase needs to touch either file, that is a deliberate
decision to make explicitly** (see Decision 1's "revisit trigger"), not something to
slide into as a side effect of another change.

### 2. `calculate_benchmark_stats` has a `ddof` asymmetry — found, not fixed

While writing the beta/alpha test, a deterministically-constructed 2x-correlated series
produced `beta = 2.0833...` instead of the analytically-exact `2.0`. Root cause: pandas'
`.cov()` defaults to `ddof=1`, but `calculate_benchmark_stats` divides by `.var(ddof=0)`
— a real, pre-existing asymmetry in the imported function, not a bug introduced here.
**Not fixed**, because fixing it means editing `portfolio_metrics.py`, which this phase's
gate explicitly forbids (it would also silently change the dashboard's live alpha/beta
tile without a deliberate decision to do so). The effect shrinks as the sample size
grows (`n/(n-1) -> 1`), which is likely why it hasn't surfaced on the dashboard's typical
multi-hundred-point series, but it is non-trivial (~5%) at the `minimum_overlap` default
of 20 that `security_technicals.beta_alpha` will typically operate near. Whoever next
opens `portfolio_metrics.py` should look at this — full details, including the
independent derivation that caught it, are in `test_security_technicals.py`'s
`BetaAlphaTest` and in `deliverables-checklist.md`'s Known Issues.

### 3. `financial_snapshots.extra` really does carry `EBIT`/`EBITDA`/`Net Debt`/`Invested Capital` — verified against the live DB, not assumed

Before writing `_net_debt_to_ebitda`/`_roic`, I queried `Data/PRD_WealthSimple.duckdb`
directly (NVDA, AAPL, MCD, T rows) and a live `yfinance.Ticker('AAPL').info` pull to
confirm which fields actually land in `extra` vs. which are silently dropped as
"consumed" by `financial_snapshots_extractor.py`. Key finding: `Total Debt` and
`Stockholders Equity` are **never** in `extra` (they're consumed to compute the stored
`debt_to_equity` ratio and then discarded) — this is *why* the new leverage ratio is
`net_debt_to_ebitda`, not `debt_to_ebitda`. If `financial_snapshots_extractor.py` is ever
refactored to also persist `Total Debt`/`Stockholders Equity` as their own fields (noted
as a possible future change, not done here), `derived_metrics.py` could gain a true gross
Debt/EBITDA — but do not assume that field exists without checking `extra` again first,
the way this phase did.

### 4. Pre-existing flaky test — not a regression

`DefaultOnRunCreationTest.test_default_mode_with_no_run_id_still_opens_a_run` failed once
during a full-suite run with a Windows `PermissionError` inside `src/workspace/run.py`'s
`os.replace`. Confirmed unrelated: passes reliably in isolation, and no Phase 2 file
touches `src/workspace/run.py`. Don't chase this if it recurs — it's a filesystem timing
race (likely AV/indexer), pre-existing.

---

## Files to Remember

| File | Purpose | Notes |
|---|---|---|
| `src/security_technicals.py` | Per-security technicals (new) | Only imports from `portfolio_metrics` (`calculate_benchmark_stats`) and `config` |
| `.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` | Extended, not replaced | `LIVE_METRICS`/`DB_METRICS` dicts are where new metrics register |
| `.claude/skills/investment-analyst-resources/references/resource-contract.md` | Updated | Documents every derived metric, including the 6 new ones and the return-window fix |
| `docs/plans/implementation/phase-2/design-decisions.md` | Binding decisions | Read before touching `portfolio_metrics.py`, `analytics.py`, or `derived_metrics.py`'s ratio section again |
| `docs/plans/implementation/phase-2/deliverables-checklist.md` | Full inventory | Gate verification, Known Issues in more detail |

---

## What Phase 3 Depends On

Phase 3 (the roadmap's worksheet builder — note the numbering mismatch flagged in
`../phase-1/HANDOFF.md`: the combined plan's own file named "phase-2" is this roadmap's
Phase 3) can call:

- `security_technicals.compute_security_technicals(rows, benchmark_rows)` for the full
  technicals bundle on one ticker, given that ticker's and `XEQT.TO`'s
  `historical_records` rows.
- `derived_metrics.compute_live_metrics(live_data, quote)` /
  `derived_metrics.compute_db_metrics(db_bundle, quote)` — now including the six new
  ratios — exactly as before.

Neither is wired into `investment-analyst-resources`'s digest/bundle output yet. Whether
to wire `security_technicals` into that skill's output, or have the Phase 3 worksheet
builder call it directly and separately, is an open decision for Phase 3 — not resolved
here.

---

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_security_technicals -v
uv run python -m unittest tests.test_investment_analyst_resources -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py dashboard/   # must be empty
```

---

## Ready for Phase 3

The Phase 2 gate is met. Phase 3 can begin when approved. See the roadmap's Phase 2
design-decision section and `design-decisions.md` for what this phase decided and why;
the combined plan's `03-phase-2-worksheet-builder.md` is the next phase's contract
(despite its filename).
