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
