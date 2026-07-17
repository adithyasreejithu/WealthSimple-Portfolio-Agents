# yfinance data availability

What the `fetch-stock-research-data` pull actually returns, by security type. This
is the ground truth behind the decision rubric's two asset-class tracks and its
`data_sufficiency` thresholds: several research groups are **structurally empty**
for funds (not failed — the fetch succeeds and returns an empty table with
`errors={}`), and a few more are empty for Canadian-listed ETFs specifically.

Findings verified against the 2026-07-14 pull for AAPL (US equity), DGRO
(US-listed ETF), and ZGLD / XEQT (Canadian-listed ETFs).

## The emptiness convention

A group can be in one of three states, and only the first counts as fetched:

| State | Meaning | Counts toward `groups_ok`? |
|---|---|---|
| **ok** | Returned usable, non-empty data | Yes |
| **empty** | Returned, but the payload is structurally empty (`{}`, empty tables, `{"expirations": []}`) with no error | **No** |
| **failed** | The fetcher raised; recorded in `errors[group]` | No |

`scoring_worksheet.py`'s `_has_data()` is the single implementation of "usable":
`None`, empty string, and empty/all-empty containers are not data; numbers
(including `0` / `0.0`) and non-empty strings are. Evidence citations into an
empty group are rejected by the validator exactly as if the field were null, and
the ETF-track `min_groups_ok` confidence thresholds are calibrated against the
smaller set of groups a fund can actually populate.

## Availability by group

| Group | US equity (AAPL) | US-listed ETF (DGRO) | Canadian-listed ETF (ZGLD / XEQT) |
|---|---|---|---|
| `overview` | full | full (has `quoteType`, `longBusinessSummary`) | partial (often **no** `longBusinessSummary`) |
| `valuation` | full | thin — basket `trailingPE` + `dividendYield` only | `dividendYield` only (sometimes `trailingPE`) |
| `financials` | full | **empty** | **empty** |
| `earnings` | full | **empty** | **empty** |
| `analyst` | full | **empty** | **empty** |
| `insider` | full | **empty** | **empty** |
| `institutional` | full | **empty** | **empty** |
| `options` | full chains | full chains | **empty** (`{"expirations": []}`) |
| `news` | full | full | **empty** |
| `dividends` | full (+ `payoutRatio`) | history + summary, **no** `payoutRatio` | summary, sparse history, no `payoutRatio` |
| `history` | full | full | full |
| `funds` | **errors by design** (`get_funds_data()` raises for equities) | full | full |

**Usable-group counts** (what `data_sufficiency` and the confidence thresholds
see): US equity ≈ 11, US-listed ETF ≈ 7 (overview, valuation, options, news,
dividends, history, funds), Canadian-listed ETF ≈ 5 (options and news drop out).

### Consistently-available fields

- **Equities:** every group; the derived company metrics (FCF yield, revenue
  growth, debt/equity, current ratio, net income, net insider shares) all resolve.
- **All ETFs:** `overview.quoteType`, price via `history` (and usually
  `valuation.dividendYield`), distribution history via `dividends`, and the
  `funds` group (expense ratio via `fund_operations`, `top_holdings`,
  `sector_weightings`). The `etf_details` table also carries expense_ratio / aum /
  nav / sector_weights / top_holdings into the classification JSON.
- **US-listed ETFs additionally:** option chains (so the options-positioning
  metrics resolve) and news headlines.

## Not possible with yfinance (no amount of code changes this)

These need a different data source, not more computation on what we already pull:

- **Implied-volatility history / percentile** ("is IV high *for this name*") — the
  `options` group is a single end-of-day snapshot; there is no IV time series.
- **Unusual-options-activity detection** — needs a historical volume/OI baseline
  to compare against; only the snapshot exists.
- **Option greeks (delta/gamma/vega)** — not provided; computing them means
  running a pricing model with assumptions, which crosses from "citing data" into
  "modeling" and is out of scope.
- **Intraday / flow data** (sweeps, blocks, tape) — the pull is end-of-day only.
- **Payout ratio for funds** — not reported for ETFs; distribution safety is judged
  from distribution-history stability instead.
- **Fund flows (creations/redemptions)** — not in yfinance.
- **Peer / sector-relative and historical valuation ranges** — need a
  multi-ticker or time-series source, not the single-ticker snapshot.

## Consumers

- `scoring_worksheet.py` — `_has_data()`, `groups_ok`/`groups_empty`, and the
  derived options metrics.
- `Knowledge-Base/taxonomy/decision-rubric.yml` — the `data_sufficiency` gate and
  per-track `confidence_rules.min_groups_ok`.
- [`fetch-stock-research-data/references/yfinance-research-contract.md`](../../.claude/skills/fetch-stock-research-data/references/yfinance-research-contract.md)
  and [`docs/architecture/decision_support_flow.md`](../architecture/decision_support_flow.md).
