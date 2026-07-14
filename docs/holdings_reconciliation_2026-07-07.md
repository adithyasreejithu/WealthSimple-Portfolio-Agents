# Holdings Reconciliation Report
**Date Generated:** 2026-07-07  
**Report Period:** Holdings snapshot as of 2026-07-07 22:15 GMT-04:00  
**Data Sources:**
- **Ground Truth:** `ref/holdings-report-2026-07-07.csv` (Wealthsimple official export)
- **Pipeline Computation:** `src/analytics.py::get_holdings()` reading `Data/PRD_WealthSimple.duckdb`

---

## Executive Summary

Comparison of 24 ticker holdings reveals **21 mismatches** (88% mismatch rate):
- **7 tickers** have incorrect quantities (missing stock splits, ghost positions, unreconciled email trades)
- **20 tickers** have grossly incorrect unrealized P/L calculations (all US-listed and recently-traded positions)
- **2 tickers** show as impossible negative positions (excluded from holdings)

**Total portfolio value mismatch:** DB reports CAD 4,290.71 vs true CSV total unable to be directly compared due to mixed currency summation without FX conversion.

---

## Detailed Mismatch Table

| Ticker | DB Qty | CSV Qty | Qty Δ | DB P/L (CAD) | CSV P/L (USD) | P/L Δ | Root Cause(s) |
|--------|--------|---------|-------|--------------|---------------|-------|---------------|
| **AAPL** | 0.5058 | 0.5058 | — | 7.80 | 51.47 | -43.67 | RC2b, RC2c |
| **AMZN** | 0.5148 | 0.5148 | — | -5.51 | 32.69 | -38.20 | RC2b, RC2c |
| **BN** | 2.0143 | 3.0170 | -1.0027 | -24.21 | 38.24 | -62.45 | **RC1**, RC2b, RC2c |
| **DGRO** | 0.0040 | 0.0040 | — | -102.74 | 0.02 | -102.76 | **RC2a** (full proceeds subtracted) |
| **DRAM** | 2.4774 | 2.4774 | — | -23.26 | 25.94 | -49.21 | RC2b, RC2c |
| **ENB** | 2.0850 | 2.0850 | — | 39.40 | 39.40 | **0.00** ✓ | (no issues; CAD-listed, no sales) |
| **HIMS** | 0.7605 | 0.7605 | — | -23.45 | -8.36 | -15.09 | RC2b, RC2c |
| **INDA** | 2.0000 | 2.0000 | — | -49.27 | -8.09 | -41.18 | RC2b, RC2c |
| **L** | 1.9736 | 8.0000 | -6.0264 | -209.73 | 139.43 | -349.17 | **RC1** (split missing), **RC4** (DRIP double-counted) |
| **MDA** | 0.5894 | 0.5894 | — | 33.20 | -1.01 | 34.21 | **RC2c** (email trade cost missing), RC2b |
| **META** | 0.1921 | 0.1921 | — | 118.08 | -8.51 | 126.59 | **RC2a**, **RC3** (NULL debit/credit) |
| **NOW** | 2.0000 | 2.0000 | — | -63.73 | 15.42 | -79.15 | RC2b, RC2c |
| **NVDA** | 1.0000 | 1.0000 | — | 23.87 | 73.23 | -49.36 | RC2b, RC2c |
| **PLTR** | 1.7449 | 1.7449 | — | -27.26 | 46.22 | -73.48 | RC2b, RC2c |
| **PZA** | 4.3215 | 4.3215 | — | -7.91 | -7.91 | **0.00** ✓ | (no issues; CAD-listed, no sales) |
| **RTX** | 0.4507 | 0.4507 | — | 12.88 | 34.42 | -21.54 | RC2b, RC2c |
| **SCHD** | 3.1835 | 5.1835 | -2.0000 | -87.17 | 33.78 | -120.94 | **RC1** (split missing) |
| **SMH** | 0.6616 | 0.6616 | — | 105.85 | 85.22 | 20.62 | RC2b, RC2c |
| **SPYM** | 0.0025 | [CSV: 0] | +0.0025 | 17.41 | [none] | — | **RC4** (DRIP unmatched, ghost position) |
| **T** | 4.2798 | 4.2798 | — | -13.58 | 15.15 | -28.73 | RC2b, RC2c |
| **XEQT** | 28.0270 | 28.0270 | — | 369.80 | 207.40 | +162.40 | **RC2c** (recent email buys cost missing) |
| **XNDU** | 2.0000 | 2.0000 | — | 32.24 | -5.68 | 37.92 | **RC2c** (email buy cost missing) |
| **ZEB** | 2.0000 | 2.0000 | — | 76.14 | 80.09 | -3.95 | RC2b, RC2c |
| **ZGLD** | 5.0000 | 5.0000 | — | 39.50 | 50.55 | -11.05 | RC2b, RC2c |

**Legend:**
- **RC1:** Stock splits never applied
- **RC2a:** Cost basis methodology (proceeds subtracted instead of average-cost reduction)
- **RC2b:** Currency mismatch (CAD debit/credit vs USD market value)
- **RC2c:** Provisional email trades missing cost basis
- **RC3:** NULL debit/credit in statement export
- **RC4:** Email↔statement reconciliation gaps

**Excluded (negative) positions:**
- **WN:** -1.0992 (exactly the missing SUBDIVISION quantity from 2025-08-19)
- **ZEQT:** -2.0168 (exactly the missing SUBDIVISION quantity from 2025-08-18)

---

## Root Cause Analysis

### Root Cause 1: Stock Splits Never Applied

**Problem:** Statements store stock splits as `STKREORG` rows in the `transactions` table with **NULL quantity values**. The pipeline's `_get_net_positions()` function only sums rows where `transaction_type IN ('BUY', 'SELL')`, completely ignoring splits. The `activities` table (sourced from the official Wealthsimple export) correctly records the same splits as `CorporateAction/SUBDIVISION` rows with exact share quantities, but the analytics module never reads `activities`.

**Evidence:**

| Ticker | Split Date | Missing Qty | CSV Total | DB Total | DB Error |
|--------|------------|------------|-----------|----------|----------|
| SCHD | 2024-10-11 | +2.00 | 5.1835 | 3.1835 | -2.00 (-38.6%) |
| L | 2025-08-19 | +6.0327 | 8.0000 | 1.9736 | -6.0264 (-75.3%) |
| BN | 2025-10-10 | +1.0027 | 3.0170 | 2.0143 | -1.0027 (-33.2%) |
| WN | 2025-08-19 | +1.0992 | [3.329 from activities] | **-1.0992** (excluded) | Sold post-split without recording split |
| ZEQT | 2025-08-18 | +2.0168 | [6.095 from activities] | **-2.0168** (excluded) | Sold post-split without recording split |

**Queries proving the split data exists:**

```sql
-- Split quantities recorded in activities (CORRECT)
SELECT t.ticker_symbol, a.transaction_date, a.quantity
FROM activities a
JOIN tickers t USING(ticker_id)
WHERE a.activity_type = 'CorporateAction'
  AND a.activity_subtype = 'SUBDIVISION'
ORDER BY a.transaction_date;

-- Result:
-- SCHD, 2024-10-11, 2.00
-- ZEQT, 2025-08-18, 2.0168
-- WN, 2025-08-19, 1.0992
-- L, 2025-08-19, 6.0327
-- BN, 2025-10-10, 1.0027
```

The fact that `WN` and `ZEQT` show as impossible negative positions proves that shares were sold after the split (correctly, because they did split), but the split credit itself was never recorded in the BUY/SELL view of `transactions`.

---

### Root Cause 2: Cost Basis and Unrealized P/L Methodology is Fundamentally Broken

#### Subproblem 2a: Cost basis calculation loses track of realized gains

**Problem:** `src/analytics.py:168` computes `cost_basis = debit − credit`, treating all credits (including full sale proceeds) as cost reductions. This conflates **realized gains** (locked in at sale) with **unrealized gains** (remaining position).

**Formula in code:**
```python
cost_basis = _decimal(debit) - _decimal(credit)
unrealized_gain = market_value - cost_basis
```

**What happens on a SELL:**
- Debit (cost): CAD 102.66 to buy DGRO
- Later, SELL records **full proceeds** as credit: CAD 102.97
- Pipeline calculates: `cost_basis = 102.66 − 102.97 = −0.31`
- Then: `unrealized_gain = (1 share × $102.97 USD) − (−0.31) = 103.28`

The realized profit of 0.31 is incorrectly rolled into the unrealized gain of the remaining position.

**Evidence (DGRO):**
- DB reports: P/L = −102.74 CAD (nonsensical for a $103 position)
- CSV reports: P/L = 0.02 USD (correct; one remaining share after sell, minimal unrealized)
- Root cause: `0.0040 qty × ~$102.97 = 0.41 market value; cost_basis = 102.66 − 102.97 = −0.31; P/L = 0.41 − (−0.31) = 0.72`, but then mixed currency conversion applies...

#### Subproblem 2b: Currency mismatch

**Problem:** `debit` and `credit` columns in `transactions` are in CAD (account settlement currency), but `market_value = quantity × close` uses `close` from `historical_records`, which is in the listing currency (USD for NASDAQ/NYSE tickers).

For AAPL:
- Debit: CAD 148.80 (for 0.5043 shares via 1.40y FX rate = ~USD 106)
- Market Value: 0.5058 × USD 311.21 = USD 157.38
- Cost Basis (DB): 148.80 CAD (but never converted to USD for comparison)
- DB P/L = 157.38 − 148.80 = 8.58 (mixing currencies)
- CSV P/L = 157.38 − 105.91 = 51.47 USD (correct)

The `fx_rate` column in `transactions` (populated for 65 BUY/SELL rows) is never used.

#### Subproblem 2c: Provisional email trades contribute quantity but zero cost

**Problem:** The statement export ends 2026-05-21. Recent trades (June–July 2026) come from `email_transactions`, marked `provisional` and `reconciliation_status = 'provisional'`. These rows update the quantity count but their `total_cost` is never added to `debit` (since `_get_net_positions` only reads `transactions`, not `email_transactions`).

**Evidence:**

| Ticker | Email Trade | Date | Qty | Provisional Cost (CAD) | In DB Cost? | Result |
|--------|------------|------|-----|------------------------|-------------|--------|
| MDA | Buy | 2026-06-19 | 0.5894 | CAD 34.21 | No | Cost = 0, P/L = MV |
| XNDU | Buy | 2026-06-19 | 2.0000 | CAD 37.92 | No | Cost = 0, P/L = MV |
| XEQT | Buy | 2026-06-04 | 3.625 | CAD 162.40 | No | Missing CAD 162.40 from cost |
| L | Buy (DRIP) | 2025-12-31 | 0.0063 | CAD 1.19 | No | Double-counted qty, zero cost |

For XEQT, the June buy contributes 3.625 shares × 45.21 CAD = 163.88 CAD to market value, but contributes zero to debit → unrealized gain inflated by 162.40 CAD.

---

### Root Cause 3: Statement Extraction Dropped Costs on Two BUY Rows

**Problem:** Two BUY transactions have **NULL debit and NULL credit**, meaning no cost information was extracted from the statement export.

**Evidence:**

```sql
SELECT t.ticker_symbol, tr.transaction_date, tr.transaction_type, tr.quantity, 
       tr.debit, tr.credit
FROM transactions tr
JOIN tickers t USING(ticker_id)
WHERE UPPER(tr.transaction_type) IN ('BUY', 'SELL')
  AND tr.debit IS NULL AND tr.credit IS NULL;

-- Result:
-- META, 2025-11-04, BUY, 0.1919, NULL, NULL
-- CDZ, 2025-11-04, BUY, 0.0030, NULL, NULL
```

The matching `activities` rows **do** have cost information:
- META: transaction_date = 2025-11-04, quantity = 0.1919, net_cash_amount = −180.45 CAD (correct)
- CDZ: transaction_date = 2025-11-04, quantity = 0.0030, net_cash_amount = −0.3200 CAD

**Impact on META:**
- DB sees: 0.1919 qty, but cost_basis = (NULL debit) − (NULL credit) = 0 − 0 = 0
- DB P/L = MV − 0 = 117.5 USD ≈ 118 CAD
- CSV P/L = −8.51 USD (correct; position is underwater)

---

### Root Cause 4: Email↔Statement Reconciliation Gaps Allow DRIP Double-Counting

**Problem:** Dividend Reinvestment (DRIP) buys appear in email exports first, then get matched to statement rows. The matching logic uses exact-date lookup; DRIP buys show up 1–2 days apart between sources and fail to match.

Three confirmed cases remain `provisional`:

| Ticker | Email Date | Statement Date | Qty | Total Cost | Status |
|--------|------------|------------------|-----|------------|--------|
| SPYM | 2025-10-01 | 2025-10-02 | 0.0025 | 0.28 CAD | provisional (matched?) |
| L | 2025-12-31 | 2025-12-31 | 0.0063 | 1.19 CAD | provisional |
| VDY | 2024-11-08 | 2024-11-09 | 0.0032 | 0.16 CAD | provisional |

**Impact:**

- **SPYM:** DB records 0.0025 shares from the email transaction (not yet matched/removed), but the user holds **zero SPYM** (sold 2026-03-03). The email quantity was from a DRIP buy that should have been consolidated post-statement.
- **L:** The 0.0063 DRIP buy exists in email_transactions but likely also in `activities` (internal Wealthsimple record); `_get_net_positions` counts both, inflating quantity by 0.0063.

---

### Root Cause 5: Portfolio Value Summation Mixes Currencies

**Problem:** `get_portfolio_summary()` sums market values across USD and CAD holdings without FX conversion.

```python
portfolio_value = cash.balance + sum(h.market_value for h in holdings)
```

For each USD holding, `market_value = quantity × close_usd`. For CAD holdings, `market_value = quantity × close_cad`. These are summed directly, assuming 1 USD ≈ 1 CAD.

**Impact:**
- Holdings value reported as CAD 4,290.71 (mixed currency)
- Actual value should use a single reference currency (typically CAD) with proper FX conversion
- Split errors (RC1) alone understate the portfolio by ~CAD 440 (missing SCHD, L, BN)

---

## Corrective Actions

### Required Fixes (to achieve reconciliation)

#### Fix 1: Apply Stock Splits
**File:** `src/analytics.py` in `_get_net_positions()`

Add a CTE that unions split quantities from `activities`:

```python
# Current: only BUY/SELL summed
# Needed: add CorporateAction/SUBDIVISION quantities
```

Alternative: Backfill `STKREORG` rows in `transactions` with quantities from `activities`, so both BUY/SELL and splits flow through the same path.

**Priority:** High (affects 5 tickers, 2 show as negative positions)

---

#### Fix 2: Rewrite Cost Basis Logic
**File:** `src/analytics.py:_get_net_positions()` lines 141–188

Replace the debit-minus-credit formula with proper average-cost tracking:

**Current (broken):**
```python
cost_basis = _decimal(debit) - _decimal(credit)
```

**Correct approach:**
1. Track `total_cost` separately from `realized_proceeds`
2. On SELL: reduce open `cost_basis` by `qty_sold × avg_cost_per_share`
3. Record realized gain separately (not mixed into unrealized P/L)

```python
avg_cost_per_share = total_buy_cost / total_buy_qty  # CAD
open_qty = total_buy_qty - total_sell_qty
cost_basis = open_qty * avg_cost_per_share  # CAD
unrealized_gain = (market_value_cad) - cost_basis
```

**Priority:** Critical (affects 20 tickers)

---

#### Fix 3: Apply FX Conversion
**File:** `src/analytics.py:_get_net_positions()`

Convert USD holdings to CAD using `fx_rate`:

```python
if currency == 'USD':
    cost_basis_cad = cost_basis * avg_fx_rate
    market_value_cad = quantity * current_price_usd * spot_fx_rate
else:
    cost_basis_cad = cost_basis
    market_value_cad = quantity * current_price_cad
```

Or maintain separate cost_basis in native currency and convert only for reporting.

**Priority:** High (affects all USD holdings)

---

#### Fix 4: Integrate Provisional Email Trades
**File:** `src/analytics.py:_get_net_positions()`

Include `email_transactions` where `reconciliation_status = 'provisional'`:

```python
email_cost_query = """
  SELECT ticker_id, SUM(total_cost) as email_total_cost
  FROM email_transactions
  WHERE reconciliation_status = 'provisional'
    AND ticker_resolution_status = 'resolved'
    AND transaction_type ILIKE '%buy%'
  GROUP BY ticker_id
"""
```

Add email_total_cost to the debit total before calculating cost_basis.

**Priority:** High (affects XEQT, XNDU, MDA, and June/July trades)

---

#### Fix 5: Backfill NULL-Cost Transactions
**File:** `Data/PRD_WealthSimple.duckdb` (data fix)

Update `transactions` rows with NULL debit/credit from the matching `activities` rows:

```sql
UPDATE transactions tr
SET debit = COALESCE(
  (SELECT a.net_cash_amount 
   FROM activities a
   JOIN tickers t USING(ticker_id)
   WHERE tr.ticker_id = t.ticker_id
     AND tr.transaction_date = a.transaction_date
     AND tr.transaction_type = 'BUY'
     AND a.activity_type = 'Trade'
     AND a.activity_subtype = 'BUY'),
  tr.debit)
WHERE tr.debit IS NULL
  AND UPPER(tr.transaction_type) = 'BUY';
```

**Priority:** Medium (only 2 rows, but META is a large position)

---

#### Fix 6: Reconcile Email↔Statement DRIP Buys
**File:** Reconciliation logic (likely in `database_command.py` or email ingestion)

Widen the matching window from exact-date to ±2 days and re-run reconciliation:

```python
# Current: email_date = statement_date
# Needed: email_date BETWEEN statement_date - 2 DAYS AND statement_date + 2 DAYS
```

Re-reconcile SPYM, L (DRIP 2025-12-31), and VDY (2024-11-08) from `provisional` → `matched`.

**Priority:** Medium (affects 3 DRIP buys, SPYM shows as ghost position)

---

## Verification Checklist

After implementing fixes, re-run:

```bash
python ref/portfolio_metrics.py  # Compare against holdings-report-2026-07-07.csv
```

Expected outcome:
- All 24 tickers should have quantity match (Qty Δ = 0)
- All unrealized P/L should match within 1 USD/CAD (rounding)
- No excluded (negative) positions
- Portfolio value should reconcile to CSV total when converted to single currency

---

## Appendix: Data Quality Notes

- **Price data:** Using last stored `close` from `historical_records`, which may lag the CSV's intraday snapshot by hours (cents-level difference, acceptable).
- **Activities table:** Is the authoritative source for corporate actions; should be trusted over statement `STKREORG` rows.
- **Email transactions:** Represent the most recent trades; must be included until fully reconciled with statements.
- **Cash balance:** Uses explicit balance where available (2026-07-07), falling back to net cash flow otherwise (current: explicit balance = CAD 21.73).
