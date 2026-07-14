# Analytics Calculations and Sources

The analytics layer is read-only. It derives metrics from normalized database
tables and does not replace or rewrite source transaction values. Date filters
are inclusive; omitted filters mean all available history.

Every metric that cannot be computed reliably from available data is recorded
in the report's `unavailable_metrics` list with a plain-English reason instead
of being guessed, defaulted to zero, or silently omitted.

## Data Sources by Calculation

Each metric pulls from specific database tables and columns. This reference maps
where each number comes from:

| Calculation | Source Table(s) | Key Columns | Notes |
|---|---|---|---|
| **Current Holdings** | `position_snapshots` JOIN `tickers` | ticker_id, quantity, book_value_cad, book_value_mkt | Filtered: quantity > 0 (positive net) |
| **Excluded Positions** | `position_snapshots` JOIN `tickers` | quantity, data_quality_flags | Filtered: quantity < 0 OR flags not null |
| **Latest Price** | `historical_records` | ticker_id, record_date, close | Most recent close per ticker |
| **FX Conversion** | `historical_records` (filtered by FX_PAIR_SYMBOL) | record_date, close | Carries forward latest rate per date |
| **Historical Valuation** | `position_ledger` JOIN `historical_records` JOIN `tickers` | running_quantity (per day), close, record_date, currency | Quantity forward-filled; prices forward-filled; split-adjusted |
| **Cash Balance** | `cash_transactions` | balance, transaction_date | Prefers explicit balance; falls back to cumulative net flow (credit - debit) |
| **Allocation: By Group** | `portfolio_classifications` | primary_group, secondary_tags | Unclassified if no row; geography filtered by fixed tag set (Canada, US, Global, India) |
| **Allocation: By Sector** | `stock_details` JOIN `etf_details` | ticker_id, sector, security_type | Stocks use sector; ETFs bucketed as "ETF"; Unknown if missing |
| **Allocation: Look-through Sector** | `etf_details` + `stock_details` | sector_weights (JSON), sector | Blends ETF weights with stock sectors |
| **Concentration** | `holdings` (current) | market_value (CAD) per ticker | Weights = market_value / total portfolio value |
| **Turnover** | `transactions` | transaction_date, transaction_type, debit, credit | Statement transactions only (BUY/SELL); min(total_buys, total_sells) / avg_portfolio_value |
| **Avg Holding Period (Open)** | `transactions` | transaction_date, transaction_type, ticker_id, quantity | Statement-only ledger walk; measures from avg acquisition date to today |
| **Avg Holding Period (Closed)** | `transactions` | transaction_date, transaction_type, ticker_id, quantity | Statement-only ledger walk; measures from avg acquisition date to sale date |
| **Realized Gains** | `v_trade_events` (deduplicated view) | event_date, event_type, ticker_id, quantity, amount_cad | Multi-source deduplicated; running weighted-average cost per ticker |
| **Dividends** | `email_transactions` OR `activities` OR `transactions` | transaction_date, transaction_type/activity_code, debit/net_cash_amount, ticker_id | Source selectable (email/activities/statements); by month and currency |
| **Dividend Yield on Cost** | `email_transactions` or source + `position_snapshots` | trailing-12-month dividends (CAD), book_value_cad | 12-month div total / current CAD cost basis |
| **Dividend Trailing Yield** | `email_transactions` or source + summary | trailing-12-month dividends (CAD), portfolio_value | 12-month div total / current portfolio value (CAD) |
| **FX Fee (Wealthsimple)** | `transactions` | transaction_date, transaction_type, debit, credit, fx_rate | Statements only; extracted from gross BUY debit or net SELL credit using embedded 1.5% rate |
| **Commissions** | `activities` | transaction_date, transaction_currency, commission_amount | Explicit commission column; grouped by currency |
| **Weighted MER** | `etf_details` + `position_snapshots` | expense_ratio, market_value (CAD) | Only ETFs with known expense_ratio; weighted by CAD market value |
| **Adjusted Daily Returns** | `historical_values` (portfolio) + `activities` or `transactions` | portfolio_value, external flows (contributions/withdrawals per date) | Formula: (V_t - V_{t-1} - F_t) / V_{t-1}; flows source-selectable |
| **Volatility / Sharpe / Sortino** | `adjusted_returns` (derived) | daily return % | Computed from adjusted-return series, not raw valuation |
| **Drawdown Details** | `wealth_index` (derived) | cumulative wealth index value | Peak-to-trough from wealth index (base 1.0) |
| **Money-Weighted Return (XIRR)** | `activities` or `transactions` + summary | external flows (contributions/withdrawals), terminal portfolio value | Treats flows as investor cash in/out; unavailable for date-filtered periods |
| **Benchmark Stats** | yfinance (live fetch) | daily close for benchmark symbol (default XEQT.TO) | Fetched at report time; requires ≥20 overlapping dates with portfolio |
| **Allocation Targets / Drift** | `policy_file` (YAML) + `portfolio_classifications` | allocation_targets block (target_percent, min_percent, max_percent) | Reads from config.POLICY_FILE; unavailable if missing or no targets key |
| **Unrealized Gain %** | `position_snapshots` | market_value, cost_basis (both CAD) | (market_value - cost_basis) / cost_basis per holding |

### Source Selection Notes

- **Dividends**, **Cash Flows**, and **Commissions**: source parameter selects which table is read; sources are never merged to avoid duplicates
- **Statements vs Activities**: Turnover and holding period currently use `transactions` (statements) only; activities not yet integrated
- **FX Rates**: Forward-filled across date grid; unavailable rates flagged in data_quality
- **Email vs Statements**: Realized gains use deduplicated `v_trade_events` (multi-source); most other calculations pick one source via parameter

## Current Holdings vs Excluded Positions

Holdings are read from `position_snapshots`, the average-cost position engine's
output (`src/position_engine.py`), which deduplicates the same trade appearing
in statements/activities/email and applies stock splits — see
`docs/architecture/ingestion_and_reconciliation.md` for the full design.
`get_holdings()` returns only positions with a positive net quantity (recorded
buys exceed recorded sells). A ticker whose net quantity the engine could not
resolve cleanly — recorded sells exceed recorded buys (clamped to zero rather
than left negative) or a data-quality flag such as `buy_missing_cost` was
raised — is excluded from holdings, allocation, concentration, currency
exposure, and the current-holdings unrealized-gain figures. It is never
dropped silently: `get_excluded_positions()` returns it, and
`data_quality.excluded_negative_positions` / the `negative_quantity` and
`position_engine_*` flags surface it in the report. Realized gains are
computed independently from the deduplicated multi-source trade ledger and are
unaffected by this exclusion.

## Historical Valuation Series

`get_historical_portfolio_values()` reconstructs, for every relevant date, each
ticker's split-adjusted running quantity from `position_ledger` (the position
engine's per-event audit trail — one deduplicated event per source trade, in
chronological order), takes the last event of each day, and forward-fills
across the date grid; a data-error negative position is clamped at zero so it
never subtracts value from the series. Prices are carried forward from the
latest known close on or before each date and, for non-CAD listings, converted
to CAD using the configured FX pair's (`config.FX_PAIR_SYMBOL`, `USDCAD=X`)
close on or before that date — also forward-filled — so the whole series stays
in one currency. Cash is included per date using the latest explicit
`cash_transactions.balance` on or before that date, falling back to cumulative
net cash flow — the same precedence `get_cash_summary` uses. Each row is
`{date, securities_value, cash_balance, portfolio_value}` (CAD).

Including cash in the series matters because a contribution lands in cash
before it becomes a purchase; without it, the day of a deposit would show as a
security-value jump with no offsetting cash line, contaminating the return
series below.

## Adjusted (Time-Weighted) Returns vs Money-Weighted Return

Two different, deliberately separate performance figures are reported:

### Adjusted Daily Returns (Time-Weighted)

**Data source:** `historical_values` (portfolio valuations) + external flows 
(from activities or statements, selectable)

**Key columns:** `portfolio_value`, `date` + flow `amount`, `transaction_date`

**Formula:**
```
r_t = (V_t - V_{t-1} - F_t) / V_{t-1}
```

Where:
- V_t = portfolio value on date t
- V_{t-1} = portfolio value on prior date
- F_t = net external cash flow on date t (contributions positive, withdrawals negative)

**Processing:**
1. Strip out external cash flows so contributions/withdrawals are never counted as performance
2. Geometrically link daily returns into a wealth index (base = 1.0)
3. All risk statistics (volatility, Sharpe, Sortino, drawdown, benchmark) computed from this series

**Total return:**
```
total_return = (final_wealth_index / 1.0) - 1
If span >= 365 days: annualized_return = (1 + total_return) ^ (1 / years) - 1
```

**Unavailable:** when adjusted return series has < 2 dates

### Money-Weighted Return (XIRR)

**Data source:** `activities` or `transactions` (external flows) + summary `portfolio_value`

**Key columns:** `transaction_date`, `net_cash_amount` or `credit - debit` + terminal portfolio value

**Calculation:** Solve for annualized IRR where:
```
contributions = investor cash outflows (negative)
withdrawals = investor cash inflows (positive)
terminal_value = current portfolio value (positive inflow)
Solve: sum(flow_t / (1 + r)^(days_from_t / 365)) = 0
```

**Requires:** At least one dated contribution AND a positive terminal value

**Unavailable reasons:**
1. Fewer than 2 cash flows
2. No positive flows (all withdrawals)
3. No negative flows (all contributions, no transactions)
4. Date-filtered period (can't reconstruct opening portfolio and historical cash safely)

**Difference from TWR:** XIRR measures investor's actual return given flow timing; 
TWR measures pure investment performance. These answer different questions and are 
not interchangeable.

## Risk Metrics (from Adjusted Returns)

All risk metrics use the **adjusted daily return series** (cash-flow-adjusted), 
not raw portfolio valuations.

**Data source:** `adjusted_returns` list (derived from `historical_values` + external flows)

**Input per day:**
```
r_t = (V_t - V_{t-1} - F_t) / V_{t-1}
```
Where: V = portfolio value; F = net external flow on date; r = daily return %

**Annualization config:** `ANNUALIZATION_PERIODS` (default 252 trading days/year) 
and `DEFAULT_RISK_FREE_RATE` (from `src/config.py`)

### Volatility (Annualized)

**Calculation:**
```
daily_volatility = std(adjusted_returns) [population std, ddof=0]
annualized_volatility = daily_volatility × sqrt(252)
```

**Unavailable:** when adjusted return series is empty (< 2 dates)

### Sharpe Ratio (Annualized)

**Calculation:**
```
daily_risk_free_rate = annual_risk_free_rate / 252
excess_returns = adjusted_returns - daily_risk_free_rate
sharpe = (mean(excess_returns) / std(excess_returns)) × sqrt(252)
```

**Unavailable:** when adjusted returns empty or volatility = 0

### Sortino Ratio (Annualized)

**Calculation:**
```
daily_risk_free_rate = annual_risk_free_rate / 252
downside_returns = [r for r in adjusted_returns where r < daily_risk_free_rate] - daily_risk_free_rate
downside_deviation = sqrt(mean(downside_returns²))
sortino = (mean(adjusted_returns - daily_rf) / downside_deviation) × sqrt(252)
```

**Unavailable:** when no downside periods exist or downside_deviation = 0

### Drawdown Details

**Data source:** `wealth_index` (derived from adjusted_returns)

**Wealth index:** Base = 1.0; each day: `index_t = index_{t-1} × (1 + r_t)`

**Calculations:**
```
drawdown_t = (index_t / cummax_to_t) - 1  [cumulative peak to date t]
max_drawdown = min(drawdown_t)
peak_date = date of index level before trough
trough_date = date of max_drawdown
recovery_date = first date where index >= peak_date level (or None if not recovered)
days_to_recover = (recovery_date - trough_date).days
current_drawdown = drawdown at latest date
```

**Returns:**
```
{
  "max_drawdown": float (percent),
  "peak_date": date,
  "trough_date": date,
  "recovery_date": date or None,
  "days_to_recover": int or None,
  "current_drawdown": float (percent)
}
```

## Benchmark Comparison

**Data source:** yfinance (live fetch at report time) vs adjusted portfolio returns

**Config:** `--benchmark SYMBOL` (default `XEQT.TO`); `--no-benchmark` to skip

**Fetch:** `yfinance_extractor.fetch_security_history([symbol], start_date, end_date)`

**Requirement:** ≥ 20 overlapping dates between portfolio and benchmark return series

**Daily benchmark return:**
```
r_benchmark_t = (close_t - close_{t-1}) / close_{t-1}
```

**Calculations (where available):**
```
active_return = mean(r_portfolio - r_benchmark) × 252  [annualized]
tracking_error = std(r_portfolio - r_benchmark) × sqrt(252)
information_ratio = active_return / tracking_error
beta = cov(r_portfolio, r_benchmark) / var(r_benchmark)
alpha = [mean(r_portfolio - rf_daily) - beta × mean(r_benchmark - rf_daily)] × 252
```

**Limitations:**
- Not persisted to database (ephemeral, report-time fetch only)
- Any fetch failure (offline, bad symbol, insufficient overlap) degrades gracefully 
  to `performance.benchmark.available: false` with reason—never a fabricated result
- Portfolio returns are in CAD; benchmark in its quoted currency. If benchmark is USD-quoted, 
  the comparison introduces a currency-mismatch caveat the report does not attempt to correct

**Example currency caveat:** Comparing CAD-adjusted portfolio returns against a USD-quoted 
benchmark (e.g., SPY) mixes two currencies; the difference includes CAD/USD FX movement, 
not pure investment alpha/beta.

## Allocation

All allocations are computed from CAD market values to ensure weights sum correctly 
across currencies. Base calculation: `market_value_cad = quantity × latest_price × fx_rate` 
(FX rate forward-filled to current date).

### By Ticker

**Data:** `position_snapshots` (quantity), `historical_records` (latest close), 
`tickers` (currency), FX rates

**Weights:** `market_value_cad / sum(market_value_cad for all holdings)`

### By Group

**Data:** `portfolio_classifications.primary_group` + holdings

**Key points:**
- A holding without a `portfolio_classifications` row is bucketed as `Unclassified` 
  rather than dropped
- If no classifications exist at all, `allocation.by_group` is marked unavailable-with-reason, 
  even though the bucket still renders as 100% Unclassified
- Weights still sum to full current-holdings value (100% CAD portfolio value)

### By Geography

**Data:** `portfolio_classifications.secondary_tags` (JSON array) + holdings

**Tags matched:** Fixed set: `Canada`, `US`, `Global`, `India`

**Weights:** Market value of holdings tagged with each geography / total tagged value

**Unavailable:** when no current holdings carry one of these geographic tags

### By Sector

**Data:** 
- Stocks: `stock_details.sector` per ticker
- ETFs: Bucketed as `ETF` (no per-ticker sector since ETFs hold many sectors)

**Bucketing:**
- Stocks with `stock_details.sector` → use sector value
- Stocks without `stock_details.sector` → `Unknown` bucket (also flagged `missing_sector` in data_quality)
- ETFs → `ETF` bucket regardless of holdings

**Weights:** Market value per bucket / total portfolio value

### Look-Through Sector Exposure

**Data:** `etf_details.sector_weights` (JSON: sector → weight %) + `stock_details.sector` + holdings

**Calculation:** For each holding:
- **If stock with known sector:** add market value to that sector bucket
- **If ETF with known sector_weights:** multiply market value by each sector weight; add to buckets

**Coverage:** `sum(market_value for holdings with sector data) / total_holdings_value`

**Returns:**
```
{
  "available": boolean,
  "weights": {sector: weight_percent},
  "coverage_percent": X (share of portfolio backed by known sector data)
}
```

**Unavailable:** when coverage = 0 (no holdings have sector data)

### Concentration

**Data:** `holdings` (market_value_cad per ticker)

**Calculations:**
```
sorted_weights = [market_value / total for each holding, descending]
top_1 = sum(sorted_weights[0:1])
top_5 = sum(sorted_weights[0:5])
top_10 = sum(sorted_weights[0:10])
hhi = sum(w² for each w in sorted_weights)
max_single_name_weight = max(sorted_weights)
```

**Single-name breach check:** `max_single_name_weight > SINGLE_NAME_MAX_WEIGHT` (config, default 10%)

### By Currency

**Data:** `tickers.currency` (listing currency) + `holdings` (market_value_cad, CAD-converted)

**Key distinction:** Grouped by listing currency (showing FX exposure), but values summed 
are CAD-converted market values (so weights sum to 100% of portfolio value). 
Use `holding.market_value_mkt` if you need actual listing-currency values instead.

## Allocation Targets and Rebalance Drift

**Data sources:**
- **Targets:** `config.POLICY_FILE` (YAML, `allocation_targets` block)
- **Actuals:** `portfolio_classifications.primary_group` + holdings (CAD market values)

**Target file structure:**
```yaml
allocation_targets:
  GroupName:
    target_percent: 25.0
    min_percent: 20.0
    max_percent: 30.0
```

**Per-group output in `targets.groups[]`:**
```
{
  "group": "GroupName",
  "target_percent": 25.0,
  "min_percent": 20.0,
  "max_percent": 30.0,
  "actual_percent": 27.5,  # sum(market_value for this group) / total portfolio
  "drift_pp": 2.5,         # actual_percent - target_percent (percentage points)
  "rebalance_needed": false  # true if actual outside [min, max] band
}
```

**Rebalance trigger:** `rebalance_needed = true` when:
- actual_percent < min_percent, OR
- actual_percent > max_percent

**No trigger when:** Group has no target_percent defined (e.g., `Cash` with no configured target)

**Unavailable:** when policy file missing or has no `allocation_targets` key; degraded to 
`targets.available: false` with reason—no drift fabricated

## Wealthsimple FX Fee Estimate

**Data source:** `transactions` table (statements only)

**Key columns:** `transaction_date`, `transaction_type` (BUY/SELL), `debit`, `credit`, `fx_rate`

**Config:** `WEALTHSIMPLE_FX_FEE_RATE` from `src/config.py` (currently 1.5%)

**Calculation:** The fee is extracted on demand from statement transactions where `fx_rate > 0`. 
The statement CAD amount already reflects the fee, so the embedded fee is:

- **BUY** (gross debit): `debit - debit / (1 + fee_rate)`
- **SELL** (net credit): `credit / (1 - fee_rate) - credit`

Example: A $101.50 gross BUY debit contains an estimated $1.50 fee (1.5% of $100 USD equivalent). 
A $98.50 net SELL proceeds implies a $1.50 fee. Multiplying those recorded amounts directly by 1.5% 
would not isolate the embedded fee.

**Limitations:** This is an estimated Wealthsimple conversion fee, not total FX drag. It does 
not compare the applied rate with a market or Bank of Canada benchmark. Email and activity-export 
records currently lack both a recorded FX rate and confirmed CAD conversion amount, so FX reports 
for those sources are explicitly unavailable. Non-statement sources degrade to 
`fees.fx.available: false` with reason `"Source does not store both an applied FX rate and a confirmed CAD amount."`

## Fee Drag

`fees.fee_drag` reports three components:

### FX Fee as % of Portfolio Value
**Data:** `transactions` (FX fees, see above) + `position_snapshots` + `cash_transactions` (current portfolio value)

**Calculation:** `estimated_fx_fee_cad / portfolio_value`

**Unavailable:** when portfolio_value ≤ 0

### FX Fee as % of Unrealized Gains
**Data:** `transactions` (FX fees) + `position_snapshots` (market_value, cost_basis per holding)

**Calculation:** `estimated_fx_fee_cad / sum(market_value - cost_basis for all holdings)`

**Unavailable:** when unrealized gains ≤ 0 (breakeven or all losses)

### Weighted MER (Expense Ratio)
**Data source:** `etf_details` (expense_ratio) + `position_snapshots` + `tickers` (filtered: security_type = 'etf')

**Key columns:** `ticker_id`, `market_value` (CAD), `expense_ratio`

**Calculation:**
```
weighted_mer = sum(market_value × expense_ratio for ETFs with known expense_ratio) 
               / sum(market_value for ETFs with known expense_ratio)

coverage_percent = sum(market_value for covered ETFs) / portfolio_value
```

**Only ETF holdings with a stored expense_ratio are included.** Stocks and ETFs 
without `etf_details.expense_ratio` are excluded from the average but counted in 
the coverage denominator. `coverage_percent` indicates what share of total portfolio 
value is backed by known MER data (e.g., 0.60 = only 60% of portfolio has known expense ratios).

**Unavailable:** when no ETF holdings have an `expense_ratio` value

## Cash Flow, Gains, and Income

### External Cash Flows (Contributions & Withdrawals)

**Data source:** `activities` (default) or `transactions` (selectable via `cash_flow_source` parameter)

**From activities:**
- **Column:** `activity_code` = 'CONT', `net_cash_amount`, `transaction_date`
- Positive amounts = contributions; negative = withdrawals

**From statements:**
- **Columns:** `transaction_type` in ('CONT', 'CONTRIBUTION', 'DEPOSIT', 'WITH', 'WITHDRAWAL'), 
  `transaction_date`, `credit - debit`
- Positive = inflow; negative = outflow

**Calculations:**
```
contributions = sum(flows where amount > 0)
withdrawals = sum(abs(flows where amount < 0))
net_contributions = contributions - withdrawals
net_investment_profit = current_portfolio_value + withdrawals - contributions
```

Note: Fees already affect the account value and must not be subtracted a second time.

### Realized Gains

**Data source:** `v_trade_events` (deduplicated view, multi-source: statements + activities + email)

**Key columns:** `event_date`, `event_type` (BUY/SELL/SPLIT), `ticker_id`, `quantity`, `amount_cad`

**Calculation:** Walks the event ledger chronologically per ticker, tracking 
running weighted-average cost:

```
For each BUY: add quantity and cost
For each SELL: allocate cost using (cost / held_qty) × sold_qty; compute gain as amount_cad - allocated_cost
Reported only if event_date is within [date_from, date_to]
```

**Key points:**
- Multi-source deduplicated (one event per trade across statements/activities/email)
- Split-adjusted quantities
- Earlier BUYs before date_from are included to establish cost basis
- Reported separately from current-holdings analytics
- Unaffected by the negative-quantity exclusion (realized gains always calculated)

### Commissions

**Data source:** `activities` table only

**Key columns:** `transaction_date`, `transaction_currency`, `commission_amount`

**Calculation:** `sum(abs(commission_amount))` grouped by currency

**Limitation:** Activity exports only; statement commissions not yet integrated

### Dividend Income

**Data source:** `email_transactions` (default) OR `activities` OR `transactions` (selectable via `dividend_source` parameter)

**From email:**
- **Columns:** `transaction_type` = 'dividend', `transaction_date`, `debit`, `ticker_id`

**From activities:**
- **Columns:** `activity_code` = 'DIV', `transaction_date`, `net_cash_amount` (> 0), `transaction_currency`

**From statements:**
- **Columns:** `transaction_type` = 'DIV', `transaction_date`, `credit`

**Breakdowns:**
- **By month:** `year-month` (YYYY-MM) grouping
- **By currency:** `tickers.currency` (email) or `transaction_currency` (activities/statements)
- **Trailing 12-month:** Last 365 days only
- **Yield on portfolio value:** `T12M_cad_dividends / current_portfolio_value`
- **Yield on cost:** `T12M_cad_dividends / current_cad_book_cost`
- **YoY growth:** Compares two most recent *full* calendar years; partial/current year excluded

**Source selection:** Sources are never combined. Selecting one source prevents duplicate 
dividend counting across overlapping records.

## Portfolio Turnover and Holding Period

### Portfolio Turnover

**Data source:** `transactions` table (statements only)

**Key columns:** `transaction_date`, `transaction_type`, `debit` (BUY), `credit` (SELL)

**Calculation:**
```
total_buys = sum(debit where transaction_type = 'BUY')
total_sells = sum(credit where transaction_type = 'SELL')
avg_portfolio_value = mean(portfolio_value for all dates in period)
turnover_ratio = min(total_buys, total_sells) / avg_portfolio_value
```

The ratio measures what fraction of the portfolio was traded over the period. 
A value of 0.25 means 25% of the portfolio changed hands.

**Unavailable:** when average portfolio value ≤ 0

**Limitation:** Currently statement-only; activities export and email trades are not yet 
included, so turnover may understate actual trading activity if you have unreconciled 
email trades.

### Average Holding Period (Open & Closed)

**Data source:** `transactions` table (statements only)

**Key columns:** `transaction_date`, `transaction_type`, `ticker_id`, `quantity`

**Calculation:** Walks the trade ledger chronologically per ticker, tracking 
quantity-weighted average acquisition date using the formula:

```
weighted_avg_acquisition_date = (sum of (quantity × acquisition_date)) / total_quantity
```

- **Open positions:** days from weighted avg acquisition date to today
- **Closed lots:** days from weighted avg acquisition date to sale date

A ticker whose net quantity ends negative (oversold) contributes nothing to 
the open-holding-period figure, consistent with excluding it from current holdings elsewhere.

**Example:** If you bought 10 shares on Jan 1 and 20 shares on Feb 1, the weighted 
average acquisition date is Jan 21 (roughly). If you sell 15 shares on Mar 1, those 
15 shares generate a closed holding period from Jan 21 to Mar 1. The remaining 15 
shares generate an open holding period from Jan 21 to today.

**Limitation:** Currently statement-only (not migrated to deduplicated `v_trade_events` 
like realized gains). Trades existing only in activities export or email are not yet 
reflected in these figures.

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
| `position_engine_buy_missing_cost` | A BUY event had no cost amount from any source; quantity is correct but book value is understated. |
| `position_engine_sell_missing_proceeds` | A SELL event had no proceeds amount; realized gain for that sale is approximated at cost (zero gain/loss). |
| `position_engine_oversell_clamped` | Recorded sells exceeded recorded buys; the position engine clamped quantity at zero instead of going negative. |
| `position_engine_fx_stale` | No dated FX rate was available for an event; the most recent known `transactions.fx_rate` was used instead. |
| `position_engine_fx_unavailable` | No FX rate was available at all for a non-CAD event; 1.0 was used as a last resort. |
| `position_engine_split_without_quantity` | A statement `STKREORG` row exists with no matching `activities` `CorporateAction` quantity, so the split could not be applied. |

See `docs/architecture/ingestion_and_reconciliation.md` for what produces each
`position_engine_*` flag and how to resolve it.

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
