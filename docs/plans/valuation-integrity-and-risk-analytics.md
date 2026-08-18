# Valuation Integrity and Risk Analytics (Workstreams C → A → B, D parallel)

Status at time of writing: design approved, implementation not started.

## Context

The dashboard's Performance tab presents eleven risk and return metrics — total
return, annualized return, volatility, Sharpe, Sortino, XIRR, max drawdown,
current drawdown, beta, alpha, tracking error, information ratio — as plain
facts, with `unavailable_metrics` listing only `fees.weighted_mer`. A review of
what the pipeline computes versus what the dashboard shows found that the daily
return series feeding all of them is converting accounting and reconciliation
timing differences into investment performance.

The original proposal was to add multi-horizon volatility (63 / 252 / 756
trading days) on the existing time-weighted return series. That design is
correct and still ships. It is not sufficient, because changing the lookback
window changes only how strongly a bad observation influences a metric — it does
not make the observation valid.

The governing principle for this plan:

```
BAD RETURN SERIES  ×  CORRECT FORMULA  =  BAD METRIC
```

Valuation integrity therefore comes before risk analytics, and the work is
sequenced accordingly.

## Evidence

Every extreme daily return in the live series decomposes into a securities leg
and a cash leg, and the cash leg dominates. Measured against the live
`/api/portfolio/report` on 2026-08-12 (27 holdings, $5,807.67, 802 valuation
days):

| Window | Date | Reported return | Securities move | Cash move |
| --- | --- | --- | --- | --- |
| 3-month | 2026-06-05 | −3.55% | −0.20% | −$200.98 |
| 1-year | 2026-03-11 | −9.29% | −0.14% | −$507.80 |
| 1-year | 2026-03-05 | +5.49% | −1.37% | +$358.94 |
| 3-year | 2024-06-14 | −36.30% | −0.20% | −$432.32 |

On 2026-03-05 the reported return and the securities movement disagree in
*sign*: the portfolio is recorded as gaining 5.49% on a day its holdings lost
1.37%.

## Key facts established during review

- **The code already uses cash-flow-adjusted time-weighted returns.**
  `portfolio_metrics.calculate_adjusted_daily_returns` (line 146) computes
  `r = (V − V₋₁ − F) / V₋₁`, and `calculate_adjusted_volatility`,
  `calculate_adjusted_sharpe_ratio`, `calculate_sortino_ratio` and
  `calculate_twr_total_return` all consume that series. Adding TWR is not the
  change; the horizons and the series repair are.
- **Historical withdrawals total $0.00.** No external-flow adjustment can
  explain or remove the drops above, so the defect is upstream of TWR. An
  external cash-flow problem is not the same as a valuation reconciliation
  problem, and the architecture must distinguish them.
- **`v_trade_events` derives `event_date` three different ways**
  (`database.py`): activities use `transaction_date` (line 340), statements use
  `COALESCE(execution_date, transaction_date)` (line 366), email uses
  `transaction_date` (line 382). The cash leg meanwhile rolls forward from
  `statement_balances.transaction_date`.
- **`activities.settlement_date` is populated on ingest and read by nothing in
  `src/`.** `data_sorter.py` writes it (lines 304–426); no reader exists outside
  `database.py`'s DDL. The settlement basis is already in DuckDB and is
  currently discarded — so an alignment hypothesis can be tested without
  re-ingestion.
- **Cash construction is a supersession chain.**
  `analytics.get_historical_portfolio_values` (line 469) takes cash from the
  latest explicit `statement_balances` row on or before each date, rolls it
  forward with CSV-sourced activity, and falls back to cumulative net cash flow
  before any statement balance exists. When a statement balance differs from the
  rolled-forward value, the difference currently lands in NAV.
- **The proposed windows are clean of the low-base problem, but not of this
  one.** The 63-day window opens 2026-05-15 at $5,337.84 and the 252-day window
  opens 2025-08-21 at $4,201.45, with zero sub-$500 days in either. The 756-day
  window opens 2023-09-13 at $464.94 with 130 sub-$500 days, but excludes the
  worst artifacts (the +6,500% and +245% days both precede it).
- **The $2.00 series start is an amplifier, not the cause.** A small denominator
  turns a modest reconciliation difference into an extreme percentage. Closing
  the historical window would hide the mechanism while leaving it live in recent
  data, where 2026-03-11 sits at −9.29% against a −0.14% securities move.
- **The valuation grid is not a trading-day calendar.** The series is a union of
  event dates: 802 points, 800 of them weekdays, across 1,124 calendar days.
  Slicing the last 63 rows is not the same as 63 trading days, and forward-filled
  flat days depress standard deviation while `√252` annualization assumes they
  did not occur.
- **`database.py` has a versioned migration path** (`schema_metadata`,
  `DATABASE_SCHEMA_VERSION`), so a new diagnostics table has an established
  mechanism — but each migration step is real work.

## Two failure modes

### Mode 1 — statement balance supersession

A statement balance silently overwrites the rolled-forward balance, and the
difference becomes P&L. Nothing classifies it as a reconciliation difference, so
by the time the return series is built it is indistinguishable from a market
loss.

### Mode 2 — trade and position legs on different dates

The cash leg and securities leg of one economic event are recognized on
different dates:

```
DAY T      buy recognized → cash −$500 → securities unchanged → NAV −$500 → false negative return
DAY T+1    position enters grid → securities +$500 → cash unchanged → NAV +$500 → false positive return
```

Economically this is one internal transaction. The return engine sees two
performance events, and they do not cancel in a volatility calculation because
squaring makes both contribute.

## Scope of contamination

A single false daily return propagates into every one of: 63D volatility, 252D
volatility, 756D volatility, Sharpe, Sortino, beta, tracking error, information
ratio, maximum drawdown, TWR, annualized TWR. None of these should be treated as
trustworthy until Workstream C completes.

---

## Workstream C — Valuation and return integrity (first)

**Objective:** produce a trustworthy daily economic valuation and return series
before any higher-level risk analytics consume it.

### C1. Daily cash reconciliation

Establish for every day what cash *should* be, and compare against what is
observed. Any unexplained difference becomes a reconciliation item, never a
return.

```
  opening_cash
+ deposits
− withdrawals
− purchases
+ sale_proceeds
+ dividends
+ interest
− fees
− taxes
± fx_movements / conversions
─────────────────────────────
= expected_closing_cash

expected_cash  vs  observed_cash  →  unexplained difference
```

### C2. Statement supersession, classified

On encountering a statement balance, compare it against the rolled-forward value
and classify the difference explicitly — `cash_reconciliation_adjustment` or an
equivalent audited field — rather than absorbing it into NAV. The source of the
difference must be investigated before deciding how, or whether, it affects
portfolio economics. A statement balance must never silently create P&L.

### C3. Trade-date / settlement-date alignment

Declare one accounting convention for the analytics engine and apply it
consistently across `trade date`, `settlement date`, `position effective date`,
`statement date` and `valuation date`. A buy cannot reduce cash on one basis
while the security enters the valuation grid on another without a balancing
asset or receivable.

Determine whether an intermediate state is required — `unsettled_trades`,
`trade_receivable`, `trade_payable`, `pending_positions`. **The specific
implementation is deliberately not chosen in this plan**; it is identified as a
reconciliation requirement to be resolved during C's investigation phase.
`activities.settlement_date` being already populated means hypotheses can be
tested against the existing database.

### C4. The daily accounting identity

Replaces the current implicit assumption that `cash + visible position grid` is
complete economic NAV. Movements *between* components are internal and must not
create returns.

```
  cash
+ market_value_of_settled_securities
+ receivables / unsettled_assets
− payables / unsettled_liabilities
─────────────────────────────────────
= economic_nav
```

### C5. Return construction, after reconciliation

Only once the valuation series is trustworthy are returns finalized, and then as
three distinct series:

**A. Invested securities return** — performance and risk of the invested
securities. Inputs: `securities_value`, `v_trade_events`, security income,
relevant fees. Trade flows must prevent purchases and sales from being mistaken
for performance.

**B. Total portfolio TWR** — actual portfolio-level investment performance.
External deposits and withdrawals removed through TWR treatment. Internal
transactions already reconcile inside economic NAV and must not appear as
external flows.

**C. Cash allocation / cash drag** — reported separately, so security-selection
performance can be distinguished from the capital-allocation effect of holding
cash.

### C6. Unexplained residual

```
  observed_nav_change
− market_pnl
− external_flows
− internal_trade_effects
− income
− fees
− fx_effects
− known_adjustments
──────────────────────────
= unexplained_residual
```

A large residual causes the observation to fail the analytics quality gate. It
must not silently enter volatility.

### C7. Data-quality gate

An explicit boundary between the valuation layer and the analytics layer:

```
raw transactions / statements / prices
                 ↓
        valuation reconciliation
                 ↓
         return construction
                 ↓
          data quality gate
            ↓         ↓
          pass      fail
            ↓         ↓
       analytics   flag / investigate
```

Risk calculations consume only periods that pass the required reconciliation
checks. The existing `unavailable_metrics` contract in `portfolio_report` is the
right shape for the gate's output to plug into.

### C8. Reconciliation diagnostics

Proposed per-day fields so a suspect return has explicit lineage back to its
valuation components. Exact schema design deferred to implementation.

| Group | Fields |
| --- | --- |
| Identity | `date` |
| Cash | `opening_cash`, `expected_cash`, `observed_cash`, `cash_difference` |
| Positions | `securities_value`, `unsettled_trade_value` |
| Flows | `external_flow`, `internal_trade_flow` |
| NAV | `economic_nav`, `reported_nav` |
| Returns | `daily_return`, `securities_return` |
| Status | `reconciliation_status`, `reconciliation_reason` |

### C9. Large-move validation

When `abs(daily_return) > threshold` — threshold configurable in `src/config.py`,
not hard-coded — decompose the move into: security market movement, cash
movement, external flows, trade flows, income, fees, FX, reconciliation
adjustment, unexplained residual. The goal is that a −9.29% reported return
sitting on a −0.14% securities movement is immediately visible rather than
requiring manual decomposition to find.

---

## Workstream A — Realized risk (second, gated by C)

Computed on the repaired cash-flow-adjusted securities return series.

- 63 trading days — short-term
- 252 trading days — primary / headline
- 756 trading days — long-term
- Drawdown
- Dollar-risk translation

The full historical window remains open. There must be no arbitrary reset
because portfolio capital increased, because cash allocation was historically
high, or because the starting portfolio value was small. Those conditions are
handled through correct return construction, not by deleting history.

**Implementation notes.** The existing metric functions already accept a returns
list and annualize with `ANNUALIZATION_PERIODS`, so a window is a tail slice plus
a loop — composition, not a rewrite. The report gains a
`performance.risk_windows` block alongside the existing `adjusted_returns`, so
nothing downstream breaks and `dashboard/api/main.py` needs no change; it
serializes whatever the report returns.

Two traps to handle explicitly:

1. **Slice against a market calendar, not row count** — the grid is a union of
   event dates, and forward-filled flat days depress volatility while `√252`
   assumes they did not occur. Either slice by trading calendar or derive the
   annualization factor from actual sampling density.
2. **Minimum-observation guard** — 756 trading days against ~800 available
   leaves 44 observations of margin. A window without enough observations must
   report `unavailable` through the existing contract rather than silently
   annualizing a short sample.

## Workstream B — Risk-adjusted / relative analytics (third, gated by A)

- Sharpe
- Sortino
- Beta
- Tracking error
- Information ratio

Reuses A's windowing. Current benchmark stats
(`portfolio_metrics.calculate_benchmark_stats`, line 334) are computed
since-inception only and currently report a 38.4% tracking error with an
information ratio of 0.0014 — a direct consequence of the contaminated series.

## Workstream D — Current portfolio risk (parallelizable, no dependency on C)

- Current-weight volatility
- Current-weight beta
- Covariance
- Correlation
- Risk contribution

**D reads per-security closes from `historical_records` and never touches the
portfolio valuation series**, so it is unaffected by the defects above and can
start immediately alongside C. Inputs are ready: 802 days × 27 tickers already
stored.

Design notes:

- **Alignment** — holdings bought recently have short histories. The matrix
  needs either a common-window intersection or per-pair overlap with a
  minimum-observation floor, and coverage must be reported rather than hidden.
- **Payload shape** — 27×27 across two matrices is roughly 40–60KB. This belongs
  behind its own `/api/portfolio/correlation?window=…` endpoint rather than
  inside `portfolio_report`, which every route already fetches.
- **Risk contribution falls out for ~15 lines** once the covariance matrix
  exists, turning the weight-vs-risk scatter into a by-product.
- **Rendering** — Recharts has no heatmap primitive, so the component is a
  hand-rolled grid with a diverging scale symmetric about zero.

---

## Sequencing

```
C. Valuation & return integrity     ← prerequisite for all historical analytics
        ↓
A. Realized risk
        ↓
B. Risk-adjusted / relative

D. Current portfolio risk           ← independent, parallel from day one
```

## Acceptance criteria before Workstream A starts

Workstream C is **not** complete simply because a daily return series can be
generated. Before volatility ships, verify against the live database that:

- [ ] The 2026-03-11 event no longer appears as ≈−9.29% when economic securities
      movement was ≈−0.14%, unless another genuine economic loss is identified.
- [ ] The 2026-03-05 positive return is reconciled against the ≈−1.37%
      securities movement.
- [ ] The 2026-06-05 ≈−3.55% event is reconciled against the ≈−0.20% securities
      movement and ≈−$200.98 cash change.
- [ ] Statement balance replacements cannot silently create portfolio P&L.
- [ ] A buy or sell cannot create return merely because its cash and position
      legs enter different source tables on different dates.
- [ ] External flows reconcile independently from internal trade flows.
- [ ] Large unexplained NAV residuals are detected.
- [ ] The daily return series has explicit lineage back to its valuation
      components.
- [ ] The repaired series can support the full available historical horizon
      without arbitrary resets.

## Effort estimate

| Workstream | Principal surfaces | Files | ~Lines | Effort | Risk |
| --- | --- | --- | --- | --- | --- |
| C · Valuation & return integrity | new reconciliation module, new table + migration, `analytics.py` valuation query, `portfolio_metrics.py`, data-quality codes, CLI diagnostics command, tests, docs | 10–14 | 900–1,400 | 5–8 days | High — investigation-led, fixture-heavy |
| A · Realized risk | `config.py`, `portfolio_metrics.py`, `analytics.py`, `types.ts`, `portfolio/page.tsx`, tests, docs | 9 | 250–300 | 0.5–1 day | Low — additive, no API change |
| B · Risk-adjusted / relative | `portfolio_metrics.py`, `analytics.py`, frontend tiles, tests | 5–6 | 200–260 | 0.5–1 day | Low — reuses A's windowing |
| D · Current portfolio risk | `analytics.py` matrix builder, new API route, heatmap component, new route or tab, `types.ts`, tests, docs | 7–8 | 450–550 | 1–2 days | Medium — alignment, hand-rolled heatmap |
| **Total** | | **~31–37** | **1,800–2,510** | **7–12 days** | D parallelizable |

Three factors drive C's size: a diagnostics table plus schema migration; a
trade/settlement convention that must be *decided* before it can be implemented
(investigation, not coding); and a quality gate that touches the contract
between `portfolio_report` and every metric it returns. Mitigating factor:
`activities.settlement_date` is already populated, so no re-ingestion is needed
to test an alignment hypothesis.

Per `CLAUDE.md`, each change updates `docs/reference/cli.md` and the affected
`docs/architecture/*.md` in the same change, not as a follow-up.

## Out of scope

Reporting gaps found during the same review, independent of Workstreams A–D and
neither blocking nor blocked by them. Recorded here so they are not lost:

1. `earnings_events`, `dividend_events` and `financial_snapshots` are populated
   by their sync commands but referenced by neither `analytics.py` nor
   `dashboard/api/main.py` — the only readers are the agent and skill layer.
2. `POST /api/actions/refresh` runs `app.py pipeline`, and `run_pipeline()`
   never calls `sync_market_data`, `sync_earnings_dividends` or
   `sync_financial_snapshots`.
3. Expense ratios are computed on two independent paths
   (`get_expense_ratios` from `etf_details`; `derive.ts → blendedMer` from
   classification fields) and coverage is 0/27 on both.
   `target_weight_percent` and `user_thesis` are likewise 0/27 but rendered.
4. `get_turnover_and_holding_period` queries `transactions` directly
   (`analytics.py:1597`, `:1631`) rather than the deduplicated `v_trade_events`
   view that `get_realized_gain_summary` already migrated to.
5. `targets.groups[].rebalance_needed` is computed and returned but the overview
   `Notifications` feed ingests only classification review counts and three
   data-quality codes — a −37.5pp Core drift is currently silent.
6. `get_dividend_history` groups by month, currency and year only, despite
   `email_transactions.ticker_id` being in the same query.
7. `build_wealth_index` output feeds `calculate_drawdown_details` and is then
   discarded; it never enters the report JSON.

**Reviewed and rejected:** the `"ETF"` bucket in `get_sector_allocation`
(`analytics.py:1205`) was flagged as misleading on the overview donut. The owner's
reading — the donut shows the sector shape of direct holdings with the fund
sleeve set aside, and the 94.4%-coverage look-through view is one click away on
the Allocation tab — is the accepted design. No change. A caption naming what
the ETF slice excludes would prevent the two charts' disagreement reading as a
bug.

## Related

- Design and methodology artifact (diagrams, evidence tables, coverage ledger):
  https://claude.ai/code/artifact/ff0b651e-d9fe-4123-a693-9e9055b7a89e
- `docs/architecture/ingestion_and_reconciliation.md` — source precedence and
  the reconciliation passes this plan builds on.
- `docs/architecture/dashboard_api.md` — the read-only GET surface that will
  carry `performance.risk_windows` and the new correlation route.
