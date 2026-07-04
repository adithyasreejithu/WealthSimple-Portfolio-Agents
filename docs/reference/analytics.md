# Analytics Calculations and Sources

The analytics layer is read-only. It derives metrics from normalized database
tables and does not replace or rewrite source transaction values. Date filters
are inclusive; omitted filters mean all available history.

## Existing Portfolio Values

Holdings, cash, current portfolio value, positions, and historical values retain
their existing calculations in `src/analytics.py`. The reference data-access
module was not copied because it would duplicate those functions.

## Financial Metrics

- Position weight = position market value / total invested holdings value.
- Unrealized gain = market value - cost basis.
- Unrealized gain percentage = unrealized gain / cost basis.
- Total return = ending historical value / starting historical value - 1.
- Volatility is the population standard deviation of daily returns, annualized
  using the configured trading-period count.
- Maximum drawdown is the worst historical value / prior peak - 1.
- Sharpe ratio uses daily excess return and population daily volatility,
  annualized using the configured trading-period count.

These formulas come from `ref/portfolio_metrics.py`. Policy-dependent bucket,
limit, and contribution recommendation functions are excluded because the
required policy configuration is not present.

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

## Cash Flow, Gains, and Income

- Contributions and withdrawals default to `activities`, using `CONT` rows and
  the sign of `net_cash_amount`. Statements can be selected instead.
- Net contributions = contributions - withdrawals.
- Net investment profit = current portfolio value + withdrawals - contributions.
  Fees already affect the account value and must not be subtracted a second time.
- Realized gains use the complete statement trade ledger through the report end
  date and weighted-average cost. A start date limits reported sales, but earlier
  purchases remain available to establish cost basis.
- XIRR treats contributions as investor outflows, withdrawals as inflows, and
  current portfolio value as the terminal inflow. It is unavailable without both
  a negative and positive dated flow. Date-filtered XIRR is also unavailable
  until the schema can reconstruct opening portfolio value and historical cash;
  returning the current value for an earlier period would be misleading.
- Explicit commissions come from activity exports and are grouped by currency.
- Dividend income defaults to email because it is the closest-to-live source.
  `activities` and `statements` can be selected. Sources are never combined
  automatically, preventing duplicate dividend counting.
- Dividend yield on cost = CAD dividends / current portfolio cost basis. Other
  currencies remain separately reported rather than being converted implicitly.

## Source Limitations

Source selection changes which records are read; it does not reconcile or merge
overlapping events. Currency is never inferred solely to make totals combine.
An unavailable result is preferred over a fabricated zero or implicit conversion.
