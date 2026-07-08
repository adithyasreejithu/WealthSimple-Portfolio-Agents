# Analytics Calculations and Sources

The analytics layer is read-only. It derives metrics from normalized database
tables and does not replace or rewrite source transaction values. Date filters
are inclusive; omitted filters mean all available history.

Every metric that cannot be computed reliably from available data is recorded
in the report's `unavailable_metrics` list with a plain-English reason instead
of being guessed, defaulted to zero, or silently omitted.

## Current Holdings vs Excluded Positions

`get_holdings()` returns only positions with a positive net quantity (recorded
buys exceed recorded sells). A ticker whose net quantity is negative — recorded
sells exceed recorded buys, almost always a reconciliation or data-entry issue
rather than a real short position — is excluded from holdings, allocation,
concentration, currency exposure, and the current-holdings unrealized-gain
figures. It is never dropped silently: `get_excluded_positions()` returns it,
and `data_quality.excluded_negative_positions` / the `negative_quantity` flag
surface it in the report. Realized gains are computed independently from the
full statement ledger and are unaffected by this exclusion.

## Historical Valuation Series

`get_historical_portfolio_values()` reconstructs, for every relevant date, the
signed cumulative BUY/SELL quantity per ticker (statement trades plus resolved
provisional email trades), carries forward the latest known close price, and
clamps quantity at zero so a data-error negative position never subtracts
value from the series. Cash is included per date using the latest explicit
`cash_transactions.balance` on or before that date, falling back to cumulative
net cash flow — the same precedence `get_cash_summary` uses. Each row is
`{date, securities_value, cash_balance, portfolio_value}`.

Including cash in the series matters because a contribution lands in cash
before it becomes a purchase; without it, the day of a deposit would show as a
security-value jump with no offsetting cash line, contaminating the return
series below.

## Adjusted (Time-Weighted) Returns vs Money-Weighted Return

Two different, deliberately separate performance figures are reported:

- **Adjusted daily returns** (`performance.adjusted_returns`) strip out
  external cash flows so contributions and withdrawals are never counted as
  investment performance:

  `r_t = (V_t - V_{t-1} - F_t) / V_{t-1}`

  where `F_t` is the net external flow on date `t` (from the selected
  cash-flow source). These daily returns are geometrically linked into a
  wealth index (base 1.0), and every risk statistic below — total return,
  volatility, Sharpe, Sortino, drawdown, and the benchmark comparison — is
  computed from that adjusted series, never from the raw valuation series.
  Annualized total return is only reported once the return history spans at
  least 365 days; otherwise only the un-annualized total return is given.
- **Money-weighted return (XIRR)** (`performance.money_weighted`) treats
  contributions as investor outflows, withdrawals as inflows, and current
  portfolio value as the terminal inflow. It is unavailable without both a
  negative and positive dated flow, and date-filtered XIRR is unavailable
  because the schema cannot reconstruct opening portfolio value and historical
  cash for an arbitrary period.

These answer different questions — TWR-style adjusted returns measure how the
holdings actually performed; XIRR measures the investor's personal return
given the timing of their contributions — and are not interchangeable.

## Risk Metrics (from Adjusted Returns)

- Volatility is the population standard deviation of adjusted daily returns,
  annualized using the configured trading-period count (`ANNUALIZATION_PERIODS`,
  252).
- Sharpe ratio uses adjusted daily excess return over the configured risk-free
  rate and population daily volatility, annualized the same way. Unavailable
  when volatility is zero.
- Sortino ratio uses the downside deviation of adjusted returns below the
  daily risk-free rate. Unavailable when there are no downside periods or
  downside deviation is zero.
- Drawdown details (`performance.adjusted_returns.drawdown`) report the worst
  peak-to-trough decline on the wealth index, plus the peak date, trough date,
  recovery date (`None` if the prior peak was never regained), days to
  recover, and the current drawdown.

## Benchmark Comparison

`--benchmark SYMBOL` (default `XEQT.TO`) fetches that symbol's daily closes
live from yfinance at report time via `yfinance_extractor.fetch_security_history`
and computes active return, tracking error, information ratio, beta, and alpha
against the adjusted portfolio return series, requiring at least 20
overlapping dates. `--no-benchmark` skips the fetch entirely. Benchmark data
is never persisted to the database — it exists only for the duration of one
report run — and any fetch failure (offline, bad symbol, insufficient
overlap) degrades to `performance.benchmark.available: false` with a reason,
never a fabricated result. Because market values retain each holding's
listing currency (see below), a benchmark denominated in a different currency
introduces a currency-mismatch caveat the report does not attempt to correct.

## Allocation

- **By ticker / by currency**: computed directly from current holdings'
  market value; currency exposure is by each ticker's *listing* currency
  (`tickers.currency`) — values are never FX-converted, so a CAD/USD split is
  a split of face-value market values, not a single-currency exposure figure.
- **By group**: from `portfolio_classifications.primary_group`. A holding
  without a classification row is bucketed as `Unclassified` rather than
  dropped, so group weights always sum to the full current-holdings value; if
  the classifications table is empty entirely, the report notes
  `allocation.by_group` as unavailable-with-reason even though the bucket
  still renders as 100% Unclassified.
- **By geography**: read from each classification's `secondary_tags`, matched
  against a fixed tag set (`Canada`, `US`, `Global`, `India`). Unavailable if
  no current holding carries one of these tags.
- **By sector**: `stock_details.sector` for stocks; ETFs are bucketed as
  `ETF` rather than guessed a sector, since ETFs hold many sectors at once.
  Stocks missing a `stock_details` row are bucketed `Unknown` and listed in
  `missing_sector_tickers` / flagged `missing_sector` in data quality.
- **Look-through sector exposure**: blends each ETF's stored
  `etf_details.sector_weights` with individual stock sectors into one view,
  reporting `coverage_percent` — the share of current-holdings value backed by
  known sector data. Unavailable when coverage is zero.
- **Concentration**: top-1/5/10 cumulative weight, the Herfindahl-Hirschman
  Index (sum of squared weights), and the single largest position, checked
  against the configured single-name cap (`SINGLE_NAME_MAX_WEIGHT`, 10%) with
  a `single_name_limit_breached` flag.

## Allocation Targets and Rebalance Drift

Group targets (`target_percent`/`min_percent`/`max_percent` per group) are
read from the `allocation_targets` block in the active classifier policy file
(`Knowledge-Base/ref/policy_v1_1.yaml`, `config.POLICY_FILE`). For each group,
`targets.groups[]` reports the actual current weight, the drift in percentage
points from target, and `rebalance_needed` (true when actual weight falls
outside the configured min/max band). A group with no configured target
(e.g. `Cash`) never triggers a rebalance flag. If the policy file is missing
or has no `allocation_targets` key, `targets.available` is false with a
reason and no drift is fabricated.

## Wealthsimple FX Fee Estimate

`WEALTHSIMPLE_FX_FEE_RATE` is stored in `src/config.py` and is currently 1.5%.
The fee is calculated on demand from statement transactions with a positive FX
rate. No derived database column is stored.

The statement CAD amount already reflects the fee, so the embedded fee is:

- BUY gross debit: `debit - debit / (1 + fee_rate)`
- SELL net credit: `credit / (1 - fee_rate) - credit`

For example, a $101.50 gross BUY debit contains a $1.50 fee, and $98.50 net SELL
proceeds imply a $1.50 fee. Multiplying those recorded amounts directly by 1.5%
would not isolate the embedded fee.

This is an estimated Wealthsimple conversion fee, not total FX drag. It does
not compare the applied rate with a market or Bank of Canada benchmark. Email
and activity-export records currently lack both a recorded FX rate and confirmed
CAD conversion amount, so FX reports for those sources are explicitly unavailable.

## Fee Drag

`fees.fee_drag` reports the estimated FX fee as a percentage of current
portfolio value and as a percentage of total unrealized gain (unavailable
when there is no positive value/gain to divide by), plus a market-value
weighted MER (expense ratio) across ETF holdings that have a stored
`etf_details.expense_ratio`, with a `coverage_percent` noting what share of
portfolio value that weighted figure actually covers.

## Cash Flow, Gains, and Income

- Contributions and withdrawals default to `activities`, using `CONT` rows and
  the sign of `net_cash_amount`. Statements can be selected instead.
- Net contributions = contributions - withdrawals.
- Net investment profit = current portfolio value + withdrawals - contributions.
  Fees already affect the account value and must not be subtracted a second time.
- Realized gains use the complete statement trade ledger through the report end
  date and weighted-average cost. A start date limits reported sales, but earlier
  purchases remain available to establish cost basis. Realized gains are
  reported separately from current-holdings analytics and are unaffected by
  the negative-quantity exclusion above.
- Explicit commissions come from activity exports and are grouped by currency.
- Dividend income (`income`) defaults to email because it is the
  closest-to-live source; `activities` and `statements` can be selected.
  Sources are never combined automatically, preventing duplicate dividend
  counting. Income is broken down by month and by currency, plus:
  - Trailing-12-month totals by currency and a trailing yield on current
    portfolio value (CAD only; unavailable without a positive portfolio value).
  - Yield on cost = trailing-12-month CAD dividends / current CAD book cost
    (unavailable without positive book cost).
  - Dividend growth (year-over-year) is only reported once at least two full
    past calendar years of dividend history exist; a growing/partial current
    year is never compared against a full year.

## Portfolio Turnover and Holding Period

`activity.turnover` is `min(buy dollars, sell dollars) / average portfolio
value` over the period, from statement BUY/SELL debit and credit totals,
unavailable without a positive average valuation over the period.

`activity.average_holding_period` walks the statement trade ledger
chronologically per ticker (the same technique `get_realized_gain_summary`
uses for weighted-average cost) to track a quantity-weighted average
acquisition date. Open positions measure days held from that average
acquisition date to today; closed lots measure to their sale date. A ticker
whose net quantity ends negative contributes nothing to the open-holding-period
figure, consistent with excluding it from current holdings elsewhere.

## Data Quality Flags

`data_quality.flags` surfaces, per holding, anything that should be reviewed
before trusting a number at face value:

| Code | Meaning |
| --- | --- |
| `negative_quantity` | Excluded position; recorded sells exceed recorded buys. |
| `provisional_activity` | Includes unreconciled provisional email activity. |
| `stale_price` | No stored price, or the latest price is older than `STALE_PRICE_MAX_AGE_DAYS` (7) days. |
| `missing_cost_basis` | Cost basis is zero or negative for a currently held position. |
| `suspicious_gain` | `|unrealized gain %|` exceeds `SUSPICIOUS_UNREALIZED_GAIN_THRESHOLD` (95%) — usually a stale or wrong price/quantity, not a real gain/loss. |
| `missing_sector` | No `stock_details.sector` recorded for a stock holding. |
| `missing_classification` | No `portfolio_classifications` row, or the classifier flagged the ticker for manual review. |

`data_quality.counts_by_code` totals each code; `excluded_negative_positions`
lists every negative-quantity ticker with its quantity and cost basis.

## Source Limitations

Source selection changes which records are read; it does not reconcile or merge
overlapping events. Currency is never inferred solely to make totals combine.
An unavailable result is preferred over a fabricated zero or implicit conversion.

## Report JSON Structure

`portfolio_report()` (and `python src/app.py analytics --export`) returns:

```
schema_version, generated_at, parameters
summary            portfolio_value, cash, securities_value, book_cost, holdings_count,
                   unrealized_gain {amount, percent}, unrealized_gains_by_ticker,
                   realized_gain_total
holdings           current (positive-quantity) positions only
allocation         by_ticker, by_group, by_sector, by_currency, by_geography,
                   look_through_sector, concentration
targets            available, source_file, groups[] {group, target_percent,
                   min_percent, max_percent, actual_percent, drift_pp, rebalance_needed}
performance        historical_values, adjusted_returns {total_return, volatility,
                   sharpe_ratio, sortino_ratio, drawdown}, money_weighted (XIRR),
                   benchmark {available, symbol, active_return, tracking_error,
                   information_ratio, beta, alpha}
income             source, transaction_count, by_month, totals_by_currency,
                   trailing_12_month, yield_on_cost, dividend_growth
fees               commissions, fx, fee_drag {fx_fee_percent_of_value,
                   fx_fee_percent_of_gains, weighted_mer}
activity           turnover, average_holding_period {open, closed}
realized_gains     total_realized_gain, by_ticker
data_quality       flags, excluded_negative_positions, counts_by_code
unavailable_metrics  [{metric, reason}, ...]
```

See [`docs/reference/cli.md`](cli.md) for the CLI flags and the on-disk export
location.
