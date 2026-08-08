# Portfolio-Manager Gap Analysis

*Status: **review document, not a plan**. Written 2026-07-29 by reading the repo
as it stands (branch `Agent-Development`, HEAD `cacf72c`) plus the 58 real
recommendation artifacts in `exports/stock-recommendations/`. Nothing here is
implemented. Where a claim is quantitative it was computed from files in this
repo, and the source is named so it can be re-derived.*

> Not financial advice. This is an engineering and process review of a
> decision-support system, written from a portfolio-manager's perspective. Every
> allocation, trade, and tax decision remains the owner's.

---

## 0. The one-paragraph version

The pipeline and analytics engine are genuinely good — better than the roadmap
gives them credit for. The **decision layer is where money is leaking**, and not
because the research is thin. It leaks because (a) the hard gates mechanically
force `Sell` on two of the highest-quality income/compounder positions for
reasons that are accounting artifacts, not business problems; (b) the weighted
score is noisy enough that 25% of re-scored tickers changed their recommended
action within days on no new information; (c) the system has no concept of what a
trade *costs*, which on a $4,113 portfolio that is 48.6% USD is the single
largest controllable drag; and (d) nothing anywhere records whether a past
decision made money, so the rubric can never be tuned by evidence. Fixing the
decision layer's *structure* is worth far more than adding research sources.

---

## 1. What has actually been built

Grounded in code read, not in doc claims.

### 1.1 Ingestion and storage — mature

| Component | File | State |
|---|---|---|
| Activity CSV ingestion, dedup, file archiving | `src/data_sorter.py` (772 ln) | Working; orchestration still tangled (known TODO) |
| PDF statement extraction | `src/statement_extractor.py` (463 ln) | Working |
| Gmail confirmation-email extraction, message-level idempotency | `src/email_extractor.py` (426 ln) | Working |
| Ticker resolution / provider symbol mapping | `src/ticker_mapping.py` (949 ln) | Working, with a pending-symbol queue |
| DuckDB schema + migrations (v13) | `src/database.py` (1194 ln) | Working, versioned |
| Position engine (split-adjusted ledger, FX-resolved, fingerprint-cached) | `src/position_engine.py` (393 ln) | Working |
| yfinance market data, benchmark history | `src/market_data.py`, `src/yfinance_extractor.py` | Working |
| Quarterly + annual financials, earnings, dividends | `src/financial_snapshots_extractor.py`, `src/earnings_dividends_extractor.py` | Working |

### 1.2 Analytics — substantially more complete than the roadmap implies

`src/analytics.py` (2261 ln) + `src/portfolio_metrics.py` (450 ln) already
compute, correctly and with cash-flow adjustment:

- Time-weighted return (geometric linking of flow-adjusted daily returns),
  wealth index, and XIRR (money-weighted) side by side
- Annualized volatility, Sharpe, **Sortino**, drawdown with peak/trough/recovery
  dates
- Concentration: top-N weights, **HHI**, single-name max
- Benchmark stats: active return, tracking error, information ratio, **alpha and
  beta**
- Rebalance drift vs. policy target/min/max bands
- Weighted MER, commission summary, **FX-fee estimation** at the configured
  1.5% (`config.WEALTHSIMPLE_FX_FEE_RATE`)
- Realized gains on a running weighted-average CAD cost basis
- **ETF overlap** and **look-through sector exposure** (de-duplicating index
  exposure held both directly and inside funds)
- Currency exposure, turnover and average holding period, dividend history and
  income summary, and a `build_data_quality` report

That list is most of a real risk system. The gap is not that these don't exist —
it is that **almost none of them reach the decision** (§3.4).

### 1.3 Decision-support layer — built, exercised, then archived

- `Knowledge-Base/taxonomy/decision-rubric.yml` (v1.3): 4 hard gates, 11
  weighted dimensions on a two-track equity/ETF split, position-aware verdict
  bands, confidence rules, horizon defaults.
- `.claude/skills/evaluate-stock-decision/`: deterministic worksheet builder →
  LLM scoring step → deterministic validator that recomputes the arithmetic, so
  the model cannot talk its way past the math. This separation is the best
  design decision in the repo.
- `.claude/agents/stock-data-prep.md` (haiku, mechanical) and
  `stock-analyst.md` (opus, judgment).
- 25 thesis pages in `Knowledge-Base/stocks/`, 58 artifacts in
  `exports/stock-recommendations/`, 34 rows in
  `Knowledge-Base/logs/decision-log.md`. **This system really ran, at portfolio
  scale.** That is what makes the evidence in §3 usable.

### 1.4 Presentation

- `dashboard/api/main.py`: 12 read-only endpoints (summary, holdings,
  allocation, trend, report, classifications, ETF overlap, price history,
  pending tickers, jobs).
- `dashboard/web/`: Next.js + shadcn frontend, in progress.

---

## 2. What is planned but not built

| Planned | Where | Status |
|---|---|---|
| Technical-analysis dimension (SMA/RSI/MACD/support-resistance/relative strength) | `docs/plans/stock-decision-support-technical-analysis.md` | Fully designed, **proposed — not approved**. Every thesis page has an empty `## Technical Analysis` section. |
| Correlation between holdings; "does this position improve or worsen diversification" | `docs/plans/goals/stock_decision_support_system.md` §"Portfolio-Level Analysis" | **Required by the goals doc, absent from the rubric and from the codebase.** Zero matches for `correlation`/`covariance` in any `src/` module. |
| FX-normalized cross-currency portfolio totals | `docs/project/todo.md` | Not built. Market values still carry each ticker's listing currency. |
| Holdings ↔ Knowledge-Base cross-reference (held tickers with no thesis, theses for unheld tickers) | `docs/project/todo.md`, marked **Priority** | Not built. Consequences in §3.2. |
| Persisted `insider_events` history | `docs/project/todo.md` | Deliberately deferred. `net_insider_shares` is recomputed fresh each run with no history. |
| Operational monitoring for partial email syncs / failed Yahoo lookups | `docs/project/todo.md` | Not built. |
| Reconciliation review command for unmatched statement/email rows | `docs/project/todo.md` | Not built. |
| Scheduling / MCP automation | `docs/ideas-mcp.md` | Planning only; roadmap Phase 6. |
| Hosted, phone-reachable view with auth | roadmap Phase 3 | API done, frontend in progress, deploy + auth not done. |

The roadmap (Phase 4) says the rubric is **"reused as-is from the archive
(already validated, not being redesigned)."** That single sentence is the most
expensive line in the repo, because §3 shows the rubric is the part that most
needs redesign.

---

## 3. Where the money is leaking

Ordered by expected cost, not by how hard it is to fix.

### 3.1 The hard gates force wrong sells on the best positions — **highest cost**

Gates are absolute: any failing gate overrides the weighted score and forces
`Sell` on a held position. Two of 27 tickers hit this in the real run, and both
are false positives:

**ENB (Enbridge)** — `dividend_integrity` failed on a trailing GAAP payout
ratio of **128.7%**, forcing `Sell` on a 3.96%-weight, 5.11%-yield Income-sleeve
position. Pipelines are *structurally* GAAP-payout > 100% because depreciation on
a long-lived asset base is enormous; the industry-standard coverage metric is
distributable cash flow, where Enbridge runs roughly 60–70%. The analyst's own
citation in `ENB-2026-07-15.json` says *"on a GAAP earnings basis"* — the model
saw the problem and the gate overruled it anyway.

**BN (Brookfield)** — `solvency` failed on FCF of −$2.22B. For a consolidated
asset manager/insurer, yfinance's "free cash flow" (operating cash flow minus
capex) sweeps in fund-level and insurance flows and carries no solvency
information. Worse, the gate reads *"debt-to-equity above 2.0 **(or unknown)**"* —
so for any financial, REIT, or holdco where the provider simply doesn't populate
a balance-sheet ratio, **missing data counts as a failing leg**. That directly
contradicts the rubric's own stated principle two sections earlier: *"A gate that
cannot be evaluated is 'unknown', never 'pass'"* — and never *fail* either.

Cost: selling ENB and BN would realize a taxable disposition (if
non-registered), pay the FX/commission spread, remove ~$288 of combined
Quality/Income exposure along with ENB's 5.11% yield, and be wrong on the
merits. Both names are held
precisely because they're durable. A gate that fires on financials, pipelines,
REITs, utilities, and royalty trusts — i.e. most of an income sleeve — is not a
safety mechanism, it's a recurring tax on the sleeve.

**The structural fix is not a threshold tweak.** It is: (i) gates only ever
`pass` / `fail` / `unknown`, and `unknown` never contributes to `fail`; (ii)
gates are sector-conditional (financials, REITs, utilities, midstream, and
royalty structures get their own coverage metric or are excluded); (iii) a failed
gate raises a **flag for review**, not an automatic `Sell` — the only truly
mechanical `Sell` should be a documented, pre-committed thesis-invalidation
trigger (§6.3).

### 3.2 Position context is wrong, and it drives the verdict

The `position` block determines which verdict band applies. `held: true` maps
score 2.58 → `Trim`; `held: false` maps the same 2.58 → `Watchlist`. Same
evidence, opposite action. It is wrong in three ways today:

- **`IREN-2026-07-16.json` records `weight_pct: 77.7`.** On a $4,113 portfolio
  that would be a $3,196 position. IREN is not in `holdings.md` at all.
- **IREN, OUST, SOFI and ZETA were all scored with `held: true`** but appear
  nowhere in `Knowledge-Base/portfolio/holdings.md`. Four of 27 verdicts —
  15% — were produced against a fabricated position state.
- **`BN` weight is 3.73% in the recommendation artifact and 3.05% in
  `holdings.md`.** `portfolio_fit` is a 0.15-weight dimension scored off that
  number.

Meanwhile three *actual* holdings — **WN (−$109.23), ZEQT (−$46.90)**, and SPYM
($0.22) — were never scored. Negative market values are a position-engine
integrity failure (unreconciled sell, corporate action, or a spin-off: WN/L are
related by exactly that). The `build_data_quality` function exists; nothing gates
the decision workflow on it.

This is the `Priority` TODO — "nothing today diffs what the DB says I hold
against what has a thesis page" — showing up as materially wrong
recommendations. **A decision run should refuse to start when position state
fails validation.** Reasoning carefully over a fabricated position is worse than
not reasoning at all, because it produces confident output.

### 3.3 The score is too noisy for its own band boundaries

Computed across all 58 artifacts:

| Measure | Value |
|---|---|
| Verdicts that were `Hold` | **21 of 27 (78%)** |
| Cross-ticker score standard deviation | 0.498 |
| Mean score range when the same ticker was re-scored | **0.26** (max 0.60) |
| Tickers re-scored whose **action changed** | **6 of 24 (25%)** |
| Observed score range, all tickers | 2.37 – 4.57 |
| Width of the `Hold` band (2.75–4.00) | 1.25 = **57% of the observed range** |

Concrete flips, days apart, no new fundamentals:

- **NVDA**: 4.46 `Add` (07-14) → 3.96 `Hold` (07-15)
- **META**: 4.11 `Add` (07-14) → 3.77 `Hold` (07-15)
- **ZEB**: 4.00 `Add` → 3.94 `Hold` — a **0.06** score move crossing a hard
  boundary
- **DRAM**: 3.28 `Hold` → 2.74 `Trim` → 2.83 `Hold`
- **SMH**: 2.59 `Trim` → 2.91 `Hold`

Roughly a third of the cross-sectional dispersion that is supposed to separate
one name from another is re-run noise. Two mechanisms produce this:

1. **Averaging 9 dimensions (equity track) or 7 (ETF track) collapses
   signal.** Extremes cancel, everything
   converges toward 3.4, and the widest band swallows the result. The system's
   most common output is "do nothing," which is often right — but it is right by
   construction, not by analysis, and that means it is equally silent when
   something *is* wrong.
2. **Hard cutoffs with no dead-band.** A 0.06 move flips `Add` to `Hold`. Any
   scoring system with measurement noise needs hysteresis: require a margin to
   cross a boundary, and require a bigger margin to reverse a recent decision.

Also: `rubric_version` in the artifacts spans v1.0, v1.1, v1.2, v1.3 while the
file on disk is v1.3. AAPL alone was scored 3.47, 3.55, 3.76, 3.71, 3.58. Nothing
re-scores history when the rubric changes, so **the rubric has never been
compared against itself across versions.**

### 3.4 The rubric can't see the portfolio it's supposed to fit into

`portfolio_fit` carries a full **0.15 weight** and reads exactly three fields:

```yaml
- classification:holdings.primary_group
- classification:holdings.position_weight
- classification:holdings.secondary_tags
```

`analytics.py` already computes `get_etf_overlap()` and
`get_look_through_sector_exposure()` — and neither is registered as a rubric
source. So when the system scores NVDA's portfolio fit, it cannot see that NVDA
is *also* held inside XEQT (30.8%), SMH (9.35%), DRAM (3.65%) and ZEQT. Direct
NVDA weight reads 4.79%; true economic exposure is materially higher. The
anchors talk about "adds sector, currency, or geographic diversification" and
"duplicates an already-overweight group" — questions the supplied evidence
cannot answer.

The goals doc explicitly required *"correlation between holdings"* and *"whether
a position improves or worsens diversification."* Neither exists in the rubric or
anywhere in `src/`.

### 3.5 Policy is written down and not enforced

From `Knowledge-Base/portfolio/portfolio-overview.md` against
`Knowledge-Base/ref/policy_v1_1.yaml`:

| Group | Current | Target | Band | Breach |
|---|---|---|---|---|
| Core | **30.8%** | 60% | 55–70% | **−24.2 pp** (≈ $995 short) |
| Growth | **32.7%** | 10% | 5–15% | **+17.7 pp over max** (≈ $728 over) |
| Alternatives | **7.6%** | 5% | 0–5% | **+2.6 pp over max** |
| Income | 11.6% | 15% | 10–20% | within band |
| Quality | 15.3% | 15% | 10–20% | within band |

The Growth sleeve is more than **3× its target and 2× its ceiling**; Core is at
roughly half its target. `calculate_rebalance_drift()` measures this correctly
and then nothing happens. Meanwhile the rubric returned `Hold` on 78% of names —
including every Growth-sleeve name — so the decision system is actively ratifying
a portfolio that violates its owner's own written policy on three of six sleeves.

This is the clearest case of "the repo is costing you money": the *analysis* is
right, the *policy* is right, and there is no mechanism that connects them to an
action.

### 3.6 No trade-cost or tax model — the biggest drag at this size

This is where portfolio size changes the entire prescription. Total market value
is **$4,112.85** across **26 positions** — a mean position of **$158**, with a
long tail: DGRO $0.31, SPYM $0.22, MDA $33, HIMS $28, XNDU $32.

- **FX is the dominant cost.** 48.6% of the portfolio ($2,000.73) is USD.
  Wealthsimple's 1.5% conversion applies each way, so a round trip is ~3.0%. A
  full rotation of the USD sleeve costs ≈ **$60 — about 1.5% of the entire
  portfolio.** `estimate_wealthsimple_fx_fee_cad()` measures this *after* the
  fact and no part of the decision process sees it *before*.
- **The rebalance in §3.5 is itself expensive.** Correcting the Growth overweight
  means moving $728–$995; if that crosses currencies it costs $11–$30 in FX
  alone. A recommendation engine that says `Trim` without pricing the trip can
  easily recommend a move whose cost exceeds its benefit.
- **A "Trim" on a $28 position is noise.** No minimum trade size exists, so the
  system will happily recommend actions on positions where the round-trip cost is
  a double-digit percentage of the position.
- **Zero tax-location awareness.** `account_type` is captured in
  `raw_activity_exports` and `activities` — and then **dropped**:
  `position_ledger` and `position_snapshots` are keyed on `ticker_id` alone. No
  account dimension survives into positions or analytics. Consequences:
  - **US-dividend withholding.** US-domiciled dividend payers (SCHD, DGRO, T,
    and the US slice inside XEQT held directly) suffer a 15% non-recoverable
    withholding tax in a TFSA that is exempt in an RRSP under the Canada–US
    treaty. On a ~3.5% yielding US dividend sleeve, that is ~0.5%/yr of that
    sleeve, permanently, and it is a **pure placement error** — no market view
    required to fix it.
  - **ACB is computed wrong for tax purposes** if registered and non-registered
    lots are pooled, because Canadian ACB pools across non-registered accounts
    only.
  - Tax-loss harvesting, superficial-loss avoidance (the 30-day rule, which
    matters a lot when you hold overlapping ETFs), and gain-realization
    sequencing are all impossible without the account dimension.
  - **Withholding tax already paid is probably sitting in the data,
    unclassified.** `config.ACTIVITY_TYPE_MAPPING` maps only
    `MoneyMovement → CONT`, `Dividend → DIV`, `Interest → INT`; there is no
    withholding-tax code, and unmapped rows are retained as `UNKNOWN`. So the
    realized drag is likely measurable from `raw_activity_exports` today with a
    code-map addition — you could quantify the actual dollars lost before
    changing anything.

  Beyond one hardcoded `"account": "TFSA"` string in `email_extractor.py`, and
  the word "withholding" in an `analytics.py` docstring, nothing in `src/`
  matches `rrsp`, `fhsa`, `superficial`, `tax_lot`, or `tax-loss`.

**At a $4.1k portfolio with 26 positions, cost control and asset location almost
certainly dominate security selection as sources of return.** A better research
pipeline improves a term that is small; eliminating a recurring 1.5–3% frictional
drag improves a term that is large and certain. Any honest ranking of "what will
grow this portfolio" has to start there.

### 3.7 The output isn't actionable

`proposed` contains exactly four fields:

```json
{"action": "Hold", "confidence": "High", "time_horizon": "Long-term",
 "verdict_vs_previous": "unchanged"}
```

No target weight. No share count. No dollar amount. No funding source. No limit
price or valuation anchor. `Add` with no size is not a decision — it is a mood.
Every verdict still requires the owner to do the sizing work by hand, which is
exactly where discipline breaks down under pressure.

### 3.8 The loop never closes — no outcome tracking

`Knowledge-Base/logs/decision-log.md` records date, ticker, action, verdict,
note. It records **no price at decision, no benchmark level, and no forward
return.** There is no way to answer:

- Did `Sell` verdicts avoid losses, or sell bottoms?
- Are `High` confidence calls actually more accurate than `Low`?
- Which dimension has predictive power, and which is noise that should be
  down-weighted or deleted?
- Was rubric v1.3 better than v1.1?

Without this, the weights (0.20 valuation, 0.15 sentiment, 0.02 insider…) are
**arbitrary and permanently unfalsifiable.** This is the meta-gap: it is the one
missing piece that would let every other gap be discovered and priced
automatically. It is also cheap — three columns and a nightly price join.

### 3.9 The benchmark is also the largest holding

`config.DEFAULT_BENCHMARK_SYMBOL = "XEQT.TO"`, and XEQT is 30.8% of the
portfolio. Alpha and beta against a benchmark that constitutes a third of the
portfolio can't separate "my active decisions added value" from "XEQT went up."
The satellite sleeve — the part where all the effort goes — is never measured on
its own.

### 3.10 Structurally momentum-biased scoring

`market_sentiment` (0.15) blends analyst upgrades/downgrades with 90-day and
365-day price returns; anchor "1" is explicitly *"net downgrades … and negative
90-day and 365-day returns."* `valuation` (0.20) rewards cheapness. Because
analyst revisions themselves follow price, roughly 0.15 of the weight is a pure
trend-following term that fights the 0.20 value term — so the system's response
to a genuine drawdown in a good business is to mark it down toward `Trim`
precisely when the value case is strongest. Momentum is a legitimate factor and
can stay, but it should be **explicit, separately weighted, and separately
measured** — not smuggled into a "sentiment" bucket where it silently cancels
valuation.

---

## 4. More information to pull — and why

Ranked by decision value per unit of effort. Nothing here needs a paid data
feed.

### Tier 1 — already in the repo, not wired to the decision

| Data | Where it already is | Why it matters |
|---|---|---|
| ETF look-through holdings + overlap | `analytics.get_etf_overlap()`, `get_look_through_sector_exposure()` | Fixes §3.4 double-counting. True NVDA/AAPL exposure vs. direct weight. |
| Daily OHLCV (~274 rows/ticker) | `fetch-stock-research-data` `history` group, already persisted | Trend, drawdown, relative strength, **and pairwise correlation** — all from data already on disk. |
| Historical quarterly financials | `financial_snapshots` table | Trend and *deceleration*, which single-point ratios cannot show. |
| Realized/unrealized P&L and cost basis | `position_ledger` | Lets a `Trim` know its own tax and cost consequence. |
| FX fee model | `estimate_wealthsimple_fx_fee_cad()` | Turn a descriptive metric into a pre-trade cost estimate. |
| Policy bands | `Knowledge-Base/ref/policy_v1_1.yaml` | Drives a rebalance proposal rather than a passive drift report. |
| Data-quality flags | `analytics.build_data_quality()` | Becomes the precondition that blocks a run on bad position state (§3.2). |

**This tier is the highest-value work in the document.** It is wiring, not
research.

### Tier 2 — small additions, high decision value

1. **Account type carried through to positions.** Add `account_id`/`account_type`
   to the position ledger and snapshots. Unlocks withholding-tax placement, real
   ACB, tax-loss harvesting, and superficial-loss checks — the §3.6 items. This
   is the single highest-value *new* data dimension in the system.
2. **Sector-appropriate coverage metrics.** Distributable cash flow / FFO /
   AFFO for midstream, REITs and royalty structures; capital ratios for banks.
   Fixes the ENB and BN gates at the root instead of loosening thresholds.
3. **Forward estimates and revision breadth.** yfinance already exposes
   `analyst`; the derived metrics compute `net_revisions_365d` and then use them
   only for sentiment. *Direction of estimate revisions* is one of the better
   documented predictors and deserves its own treatment.
4. **Multi-source price/FX** (Stooq, Alpha Vantage free tier, or Bank of Canada
   for CAD/USD) purely as a **cross-check**. The `T-2026-07-22` research log
   shows a full yfinance failure that silently degraded a real Sell decision to
   unsourced narrative. Single-provider dependence on a free scraped API is a
   correctness risk, not just an availability one.
5. **Dividend calendar with ex-dates.** `dividend_events` exists. Expected
   monthly income and upcoming ex-dates were in the goals doc and are absent
   from every verdict. Ex-dates also interact with trade timing.
6. **Corporate-action feed** (splits, spin-offs, mergers). The WN/ZEQT negative
   positions are almost certainly an unhandled corporate action. Getting this
   wrong corrupts cost basis silently and permanently.

### Tier 3 — genuine research expansion, only after Tiers 1–2

7. **Primary filings** (SEDAR+ for Canada, EDGAR for US). Management discussion,
   segment detail, risk-factor *changes* year over year, and share-count dilution
   — none of which appear in yfinance summary fields. Diffing this year's risk
   factors against last year's is a high-signal, low-cost, purely mechanical
   extraction.
8. **Earnings-call transcripts** — guidance language and its changes. Best value
   as a *change detector* against the prior quarter, not as sentiment scoring.
9. **Competitive/industry context.** `Knowledge-Base/market-research/competitor-notes/`
   exists and contains nothing but its `index.md`. A moat assessment needs a
   named peer set with relative growth, margin and multiple — the goals doc asked
   for "key competitors" and the rubric never scores competitive position at all.
10. **Macro/rate context** (`macro-notes/` appears in the goals doc's planned KB
    tree but does not exist on disk).
    Relevant precisely because this portfolio is 11.6% Income, 7.6% gold, and
    holds pipelines, banks and telecoms — all rate-sensitive. One shared macro
    note referenced by every rate-sensitive name beats re-deriving it per ticker.
11. **Short interest, borrow, and float** for the speculative sleeve (XNDU, HIMS,
    OUST, IREN).

### Deliberately *not* recommended

- Paid data feeds, alternative data, sentiment-scraping social media. At $4.1k,
  the cost/benefit is absurd, and the win is elsewhere.
- More scoring dimensions before §3.3's noise problem is fixed. Adding dimensions
  to a system that already averages 11 makes the compression *worse*, not better.

---

## 5. Metrics to introduce

Grouped by what they're for. Starred (★) items are the ones that would change a
decision immediately.

### 5.1 Cost and tax — currently absent, highest leverage

- ★ **Round-trip trade cost estimate (bps and dollars)** — FX + spread +
  commission for a proposed trade, computed *before* recommending it.
- ★ **Cost-to-benefit ratio of a proposed action** — estimated cost as a % of the
  position. Suppress any recommendation below a minimum trade size.
- ★ **Withholding-tax drag by account** — annual dollars lost to non-recoverable
  US withholding on US dividend payers held in a TFSA.
- **Asset-location efficiency score** — how much of the portfolio's tax-
  inefficient income sits in the wrong account type.
- **Tax cost of realizing a gain/loss** — per position, per account, at marginal
  rate. Makes `Trim` and `Sell` honest.
- **Superficial-loss exposure** — flags where a loss sale would be denied due to
  a purchase of the same or identical property within 30 days on either side
  (highly relevant with XEQT/ZEQT/SPYM overlap).
- **Total cost drag, annualized** — MER + FX + commissions as bps of portfolio.
  `calculate_weighted_mer()`, `get_fx_fee_summary()`, and
  `get_commission_summary()` already exist; nothing sums them into one number.

### 5.2 Risk — extends what already exists

- ★ **Look-through position weight** — direct + fund-embedded exposure per name.
  The single most useful new risk number here.
- ★ **Pairwise correlation matrix** and **average pairwise correlation** from the
  OHLCV already on disk. Answers "am I diversified or do I own the same trade six
  ways?" 26 positions with high average correlation is 26 lines of admin for one
  bet.
- **Effective number of bets** (1/HHI on look-through weights) vs. nominal 26.
  Expect this to be startlingly low.
- **Marginal contribution to portfolio volatility** per position — the honest
  answer to "should this be in the portfolio," and the metric that makes
  `portfolio_fit` real.
- **Factor/sector beta decomposition** — how much of the portfolio is one
  semiconductor bet (SMH + DRAM + NVDA + the tech weight inside XEQT).
- **Drawdown contribution by position** — who actually hurt during the worst
  drawdown that `calculate_drawdown_details` already identifies.
- **Currency-hedged vs. unhedged return attribution** — with 48.6% USD, CAD/USD
  moves may be a bigger P&L driver than stock selection. Right now that is
  unmeasured.
- **Position-level VaR / stress scenarios** (rates +100bp, USD −10%, semis
  −30%). Simple, deterministic, and directly relevant to this book.

### 5.3 Decision quality — the metrics that make the system improvable

- ★ **Forward return after each decision** at 1/3/6/12 months, absolute and vs.
  benchmark. Three columns in `decision-log.md` plus a price join.
- ★ **Hit rate and average outcome by action type** — do `Sell` calls avoid
  drawdowns? Do `Add` calls outperform `Hold`?
- ★ **Confidence calibration** — realized accuracy of `High` vs `Medium` vs
  `Low`. If they don't separate, confidence is decoration.
- **Per-dimension predictive power** — correlation of each dimension's score
  with forward return. This is what should set the weights, replacing judgment
  calls like "insider_activity = 0.02."
- **Score stability / test-retest reliability** — re-score without new data and
  measure the delta. Today that number is 0.26 and nobody is watching it.
- **Action churn rate** — how often a verdict reverses within N days. Today 25%.
- **Rubric-version agreement** — re-score history under a new rubric before
  adopting it, and report how many verdicts change and why.
- **Gate false-positive log** — every gate failure the owner overrides, with the
  reason. Two entries already exist in substance (ENB, BN); nothing captures
  them.

### 5.4 Portfolio operations

- **Drift-to-action mapping** — the specific trade list that closes the §3.5
  gaps, ranked by cost-adjusted benefit.
- **Expected monthly and annual dividend income**, plus a forward ex-date
  calendar.
- **Cash drag** and **days-to-deploy** on incoming cash.
- **Position count vs. minimum viable size** — an explicit rule about the tail.
  Eight of 26 positions are under $60 (PZA $57, MDA $33, XNDU $32, HIMS $28,
  DGRO $0.31, SPYM $0.22, plus the two broken negatives). They cannot move the
  portfolio and each carries fixed attention and cost.

---

## 6. Better reasoning to introduce

The scoring architecture (human rubric + deterministic math + LLM judgment
confined to per-criterion scoring) is sound and should be kept. The problems are
in the *shape* of the rubric.

### 6.1 Separate "is this a good business" from "should I trade it today"

One weighted score currently answers both, which is why it answers neither well.
Split into three:

1. **Quality / durability score** — slow-moving, re-scored quarterly on
   fundamentals. Should barely change week to week. AAPL moving 3.47 → 3.76 →
   3.58 in two days is a bug, not information.
2. **Opportunity score** — valuation vs. history and peers, plus catalysts. Fast
   moving, price-driven.
3. **Portfolio action** — a function of (1), (2), current look-through weight,
   policy band, and **trade cost**. Only this layer emits Buy/Add/Trim/Sell.

This alone fixes most of §3.3: quality stops jittering, and the action layer can
require a *cost-justified* margin before it moves.

### 6.2 Fix the gate semantics

- Three states only: `pass` / `fail` / `unknown`. `unknown` never contributes to
  a `fail` — delete the `(or unknown)` clause in `solvency`.
- Sector-conditional gates, with an explicit exclusion list and an
  industry-appropriate substitute metric (DCF/FFO/AFFO coverage instead of GAAP
  payout for midstream, REITs and royalty structures).
- A failed gate produces **`Review` with a written reason**, not `Sell`.
- Only a pre-committed, written thesis-invalidation trigger produces a mechanical
  `Sell`.

### 6.3 Pre-commit the exit before entering

Every position should carry, written at entry:

- The 2–4 assumptions the thesis depends on
- The observable event that would falsify each ("dividend cut," "segment margin
  below X for two consecutive quarters," "guidance withdrawn")
- Target weight and maximum weight
- What would make you *add*

`decision-framework.yml` already has a `broken` verdict and the analyst already
writes `narratives.monitoring` — but nothing checks either against incoming data.
Pre-commitment is the single most effective known defence against selling
capitulation lows and holding losers, and it costs nothing to adopt. It also
converts "sell discipline" from a judgment call into a check.

### 6.4 Add hysteresis and margins

- Require a margin to cross a band boundary — at or above the 0.26 observed
  re-run noise, so roughly 0.20–0.30.
- Require a larger margin, or new evidence, to *reverse* a decision made within
  the last 30 days.
- Report the score with an uncertainty range, and refuse to act when the range
  straddles a boundary. "3.9 ± 0.3 — no action" is honest; "3.9 → Hold" is not.

### 6.5 Make the analyst argue against itself

The strongest available upgrade to reasoning quality, and it needs no new data:

- **Require a falsification attempt.** Before the verdict: "what would have to be
  true for this to be wrong, and what evidence would I expect to see?"
- **Score the bear case explicitly**, don't just narrate it. `bull_case` and
  `bear_case` exist as prose and carry no weight.
- **Force a base rate.** "Companies with these characteristics historically…"
  Anchoring on a base rate before scoring is a documented debiasing technique.
- **Name the disagreement.** `analyst_view` already exists separately from the
  mechanical verdict — good design. Add: when they disagree, *log it*, and review
  those cases quarterly. That log is how the rubric gets tuned.
- **Blind the analyst to the prior decision** on re-scores, or at least score
  first and reveal after. `position.prior_decision` is currently supplied *before*
  scoring, which invites anchoring and manufactures the `unchanged` verdict that
  dominates the log.

### 6.6 Make portfolio fit a real constraint

Replace group/weight/tags with: look-through weight, marginal contribution to
volatility, average correlation to the existing book, policy-band headroom, and
account-placement suitability. Then `portfolio_fit` can actually veto a good
business that adds nothing — which is the whole point of the dimension.

### 6.7 Reason at portfolio level, not just ticker level

The current design fans out per ticker and never asks the portfolio question:
"given a fixed amount of cash and these 26 positions, what is the single best
action?" That is a ranking-and-budget problem, not 26 independent verdicts. A
portfolio-level pass that consumes all per-ticker outputs plus drift, cost and
tax, and emits **one ranked trade list**, is what turns this from a research
tool into a portfolio manager.

---

## 7. Fit against what exists, and next steps

### 7.1 Fit assessment

| This document says | Repo today | Fit |
|---|---|---|
| Wire existing analytics into the rubric | `get_etf_overlap`, look-through, drift, MER, FX all exist and are unused by the decision layer | **Excellent** — the rubric's `sources:` registry was explicitly designed for new sources to slot in. Pure wiring. |
| Close the decision→outcome loop | `decision-log.md` is append-only prose; price history is in DuckDB | **Excellent** — three columns plus a join. Nothing needs redesign. |
| Fix gate semantics and sector conditioning | `decision-rubric.yml` is versioned, hand-curated, validated by `check_rubric.py`, and edited through a dedicated skill | **Excellent** — the governance for this already exists. It just needs the edit. Note the skill is currently archived. |
| Split quality / opportunity / action | Single `weighted_score` → single band | **Moderate** — real change to `rubric.py`, the worksheet schema, and the recommendation contract. Highest value, highest effort. |
| Add hysteresis and boundary margins | `verdict_bands` are pure `min` cutoffs | **Good** — localized change to the bands plus prior-decision awareness that is already in the worksheet. |
| Trade-cost and minimum-size gate | `estimate_wealthsimple_fx_fee_cad()` exists; nothing pre-trade | **Good** — the math is written; it needs to move upstream of the verdict. |
| Account dimension for tax | `account_type` captured at ingest, dropped at `position_ledger` | **Moderate** — schema migration (v12) + position-engine change + backfill. Contained, but it touches the foundation. |
| Correlation, marginal risk contribution | Absent; OHLCV already on disk | **Good** — ~50 lines of pandas against data already persisted. |
| Portfolio-level ranked trade list | Per-ticker only | **Moderate** — a new step, but it consumes existing outputs and adds no new data. |
| Filings, transcripts, competitor and macro research | Empty KB directories exist in the planned tree | **Poor fit right now** — real work, real tokens, and it improves the *smallest* term in the return equation. Defer. |

### 7.2 What this changes about the roadmap

The roadmap's sequencing (Phase 2 pipeline hardening → Phase 3 hosted view →
Phase 4 single-ticker → Phase 5 portfolio-wide → Phase 6 merge) is sound
discipline and worth keeping. Two amendments:

1. **Phase 4's "reuse the rubric as-is (already validated)" is the problem.**
   The rubric was *exercised*, not validated — it has never been checked against
   an outcome. Reusing it unchanged rebuilds a system that forces wrong sells on
   ENB and BN and flips 25% of its actions on noise. **The rubric fixes belong
   inside Phase 4, before the rebuild, not after.**
2. **Position-state validation belongs in Phase 2, as an exit criterion.** Phase 2
   already promises "`analytics` output matches actual current holdings with no
   known gaps." The negative WN/ZEQT positions and the 77.7% IREN weight are
   exactly that criterion failing. Add: *no decision run may start against a
   position snapshot with data-quality flags.*

### 7.3 Suggested next steps

Sequenced so each step is independently useful and none depends on a later one.

**Step 0 — Fix position truth (blocks everything).** Resolve the negative WN and
ZEQT positions and the 77.7% IREN weight. Build the `Priority` TODO diff between
DB holdings and KB thesis pages. Make `build_data_quality()` a hard precondition
on any decision run. *Why first: every downstream number and verdict inherits
this. 15% of the last run's verdicts were scored against a fabricated position.*

**Step 1 — Close the outcome loop.** Add `price_at_decision`,
`benchmark_at_decision`, and `rubric_version` to `decision-log.md`; add a job
that back-fills forward returns at 1/3/6/12 months. *Why second and why it is the
most important cheap step: it is the only change that makes every later change
measurable. Until it exists, every rubric edit — including the ones in this
document — is a guess. Start it now so the data accumulates while other work
happens.*

**Step 2 — Fix the gates.** Three-state semantics, drop `(or unknown)`,
sector-conditional coverage metrics, and `fail → Review` instead of `fail →
Sell`. *Why now: it is a contained YAML edit with existing validation tooling,
and it stops the highest-cost active failure. Verify by re-scoring ENB and BN and
confirming they no longer force `Sell` for accounting reasons.*

**Step 3 — Add the cost and tax layer.** Pre-trade cost estimate, minimum trade
size, and the `account_type` dimension through to `position_ledger`. *Why here:
on a $4.1k, 48.6%-USD book this is the largest controllable drag (§3.6), it is
certain rather than probabilistic, and asset location is a one-time fix that pays
every year. Do it before making recommendations more actionable, so the first
actionable recommendation is already cost-aware.*

**Step 4 — Wire the portfolio into the rubric.** Register look-through exposure,
ETF overlap, correlation and policy-band headroom as rubric sources; rewrite
`portfolio_fit`'s evidence and anchors against them. *Why here: it is mostly
wiring existing analytics, and it fixes the dimension carrying 0.15 weight while
being nearly blind.*

**Step 5 — Restructure the score.** Split quality / opportunity / action; add
hysteresis and boundary margins; add sizing (target weight, dollar amount,
funding source) to `proposed`. *Why after Steps 1–4: this is the largest change,
and Step 1's outcome data plus Step 2–4's inputs are what tell you whether the
restructure actually helped rather than just felt better.*

**Step 6 — Portfolio-level pass.** One ranked, cost- and tax-adjusted trade list
that closes the policy drift in §3.5. *Why last among the core work: it consumes
every prior step's output. It is also the step that finally answers the question
the whole system exists to answer.*

**Step 7 — Reasoning upgrades.** Falsification requirement, explicit bear-case
scoring, base rates, blinding the analyst to the prior decision, and the
analyst-vs-rubric disagreement log. *Why here rather than earlier: these are
prompt- and contract-level changes that are cheap to make and hard to evaluate —
they need Step 1's measurement to tell whether they helped.*

**Deferred, with reasons:** technical-analysis dimension (adds yet another
dimension to a rubric that already suffers from averaging compression — revisit
only after Step 5); filings/transcripts/competitor/macro research (real value, but it
improves the smallest term while Steps 0–4 improve the largest); multi-source
data (worth one cross-check on price and FX now, full redundancy later);
scheduling and alerting (needs Step 6 to exist before there is anything worth
scheduling).

### 7.4 The uncomfortable summary

The instinct behind this repo — write the rules down, make the machine apply them
consistently, never let the model predict — is correct, and rarer than it should
be. The architecture is good. But the system currently spends its most expensive
model (opus) producing a `Hold` 78% of the time, on a portfolio whose actual
problems are visible for free in three numbers already computed: Growth is 2×
its policy ceiling, half the book is in a currency that costs ~3% round-trip to
enter and exit, and roughly a third of the positions are too small to matter.
**The
most valuable assistant here is not a better stock analyst. It is a portfolio
manager that enforces the policy already written down, prices every trade before
recommending it, and keeps score.**
