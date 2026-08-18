from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]

"""
Shared folder paths used by multiple source files.
"""
DATA_FOLDER = BASE_DIR / "Data"
EXPORT_FOLDER = BASE_DIR / "exports"
KNOWLEDGE_BASE_FOLDER = BASE_DIR / "Knowledge-Base"
PORTFOLIO_GROUPING_FOLDER = KNOWLEDGE_BASE_FOLDER / "ref"
DEFAULT_DATA_FOLDER = DATA_FOLDER
DEFAULT_EXPORT_FOLDER = EXPORT_FOLDER
LOG_FOLDER = BASE_DIR / "logs"
# Operational policy/configuration (e.g. investment-analysis-policy.yml), kept
# separate from KNOWLEDGE_BASE_FOLDER which is reserved for research content
# (stock theses, market analysis, research notes). See
# docs/plans/implementation/phase-0/HANDOFF.md.
POLICIES_FOLDER = BASE_DIR / "config" / "policies"
"""
Run workspace: one directory per research/portfolio request, holding that
request's inputs, evidence, calculations, agent outputs, and audit log. This is
the working state for a single question, distinct from `EXPORT_FOLDER` (data
exported out of the pipeline for human consumption) and `KNOWLEDGE_BASE_FOLDER`
(durable curated research). Consumers must import these constants rather than
rebuilding the path from their own root, so there is one definition to change.
See `docs/architecture/run_workspace.md`.
"""
WORKSPACE_FOLDER = BASE_DIR / "workspace"
WORKSPACE_RUNS_FOLDER = WORKSPACE_FOLDER / "runs"
WORKSPACE_ARCHIVE_FOLDER = WORKSPACE_FOLDER / "archive"
DATABASE_PATH = Path(
    os.getenv("DB_PATH", str(DATA_FOLDER / "PRD_WealthSimple.duckdb"))
).expanduser()
DATABASE_SCHEMA_VERSION = 15

"""
Financial analytics assumptions. Keep business constants here so calculations
remain reviewable and do not diverge between CLI and library callers.
"""
WEALTHSIMPLE_FX_FEE_RATE = Decimal("0.015")
ANNUALIZATION_PERIODS = 252
DEFAULT_RISK_FREE_RATE = 0.0
STALE_PRICE_MAX_AGE_DAYS = 7
# Floor on how far back `market_data.sync_market_data` backfills `historical_records`,
# independent of when the holding was first bought. 400 calendar days is ~275 trading
# bars: enough for an SMA-200 and for the 365-day relative-strength window, both of
# which a recently-purchased name would otherwise never be able to compute.
MINIMUM_PRICE_HISTORY_DAYS = 400
SUSPICIOUS_UNREALIZED_GAIN_THRESHOLD = 0.95
DEFAULT_BENCHMARK_SYMBOL = "XEQT.TO"
# Benchmarks persisted to `historical_records` by `market_data.ensure_benchmark_history`
# (db ticker symbol -> yfinance symbol). VFV (CAD-listed, unhedged) stands in
# for the S&P 500 so the overlay stays in the portfolio's currency.
BENCHMARK_TICKERS = {"XEQT": "XEQT.TO", "VFV": "VFV.TO"}
# Overlay key served in the report's performance.trend_overlays -> db ticker symbol.
TREND_BENCHMARKS = {"XEQT": "XEQT", "SP500": "VFV"}
SINGLE_NAME_MAX_WEIGHT = Decimal("0.10")
# Trading-day windows offered by analytics.get_return_correlation, keyed by the
# label the dashboard's window selector sends. 63/252/756 approximate 3
# months/1 year/3 years of trading days.
CORRELATION_WINDOWS = {"3m": 63, "1y": 252, "3y": 756}
# A pairwise correlation/covariance computed from fewer daily observations than
# this is not reported -- too few points to be a meaningful estimate.
MINIMUM_CORRELATION_OBSERVATIONS = 20
ANALYTICS_EXPORT_FOLDER = EXPORT_FOLDER / "analytics"
ANALYTICS_EXPORT_FILENAME = "portfolio-analytics.json"
POLICY_FILE = PORTFOLIO_GROUPING_FOLDER / "policy_v1_1.yaml"
CLASSIFICATION_RULES_FILE = PORTFOLIO_GROUPING_FOLDER / "classification_rules_v1_1.yaml"
MANUAL_OVERRIDES_FILE = PORTFOLIO_GROUPING_FOLDER / "manual_overrides_v1_1.yaml"

"""
Position reconciliation config used by `database_command.py` (statement <->
activities <-> email trade matching) and `position_engine.py` (average-cost
book value computation). Tolerances are sized to the confirmed DRIP/rounding
mismatches found during holdings reconciliation
(docs/holdings_reconciliation_2026-07-07.md): email quantities can drift up to
~2% from the matching statement/activities row, statement-vs-activities
quantities are tighter (~0.5%) since both come from the same broker.
"""
RECON_QTY_ABS_TOL = Decimal("0.0005")
RECON_QTY_REL_TOL_STMT = Decimal("0.005")
RECON_QTY_REL_TOL_EMAIL = Decimal("0.02")
RECON_DATE_WINDOW_DAYS_STMT = 5
RECON_DATE_WINDOW_DAYS_EMAIL = 4
FX_PAIR_SYMBOL = "USDCAD=X"

"""
Staleness-gate intervals for `market_data.py`'s per-domain refresh helpers,
used by `app.py run_pipeline`'s market-data refresh stage. Prices/FX/benchmark
are not gated here -- `MarketTarget.fetch_ranges` is already incremental from
the last stored date, so every run only fetches what is actually missing.
"""
EARNINGS_REFRESH_INTERVAL_DAYS = 1
DIVIDENDS_REFRESH_INTERVAL_DAYS = 7
FINANCIAL_SNAPSHOTS_REFRESH_INTERVAL_DAYS = 30

"""
Logging config used by `system_logger.py`.
"""
LOG_PATH = LOG_FOLDER / "SystemLogs.txt"
LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | pid=%(process)d | "
    "%(name)s:%(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

"""
Email extractor config used by `email_extractor.py`.
"""
# `extract_wealthsimple_date`/`extract_interac_date` hand whatever text follows
# a date-like label to `pd.to_datetime` with no format hint. If that text is
# missing an explicit year (a real observed case: a DRIP "Fractional Buy"
# confirmation whose matched field read just "Dec 31"), the resolved year
# depends on the date-parsing library's own default-year behavior, which is
# not pinned and has changed across pandas/dateutil versions -- silently
# producing a transaction_date up to a full year away from the truth. The
# email's IMAP `received_at` is always reliable, so any parsed body date more
# than this many days from `received_at` is treated as a bad parse and
# discarded in favor of the received-date fallback. Every correctly-parsed
# email observed in production has landed on drift_days == 0; this is sized
# well above the widest reconciliation window (`RECON_DATE_WINDOW_DAYS_STMT`
# = 5 days) to comfortably allow a legitimate multi-day record-date/payment-
# date gap without ever being wide enough to mask a wrong-year bug.
EMAIL_BODY_DATE_MAX_DRIFT_DAYS = 14
EMAIL_OUTPUT_COLUMNS = [
    "account",
    "transaction",
    "ticker_id",
    "ticker",
    "quantity",
    "avg_price",
    "total_cost",
    "debit",
    "date",
    "price_currency",
    "amount_quality",
    "source_message_id",
    "received_at",
]

# v_trade_events/position_engine/analytics distinguish how a trade's CAD
# amount was obtained, so a derived or missing figure can be flagged as an
# estimate instead of silently presented as a confirmed brokerage amount.
AMOUNT_QUALITY_REPORTED = "reported"
AMOUNT_QUALITY_DERIVED = "derived_from_quantity_and_price"
AMOUNT_QUALITY_MISSING = "missing"

WEALTHSIMPLE_SENDERS = (
    "support@wealthsimple.com",
    "notifications@o.wealthsimple.com",
)

INTERAC_SENDER = "catch@payments.interac.ca"
INTERAC_SUBJECT_PATTERN = re.compile(
    r"^Interac e-Transfer:\s*ADITHYA SREEJITHU PANICKER\b",
    flags=re.IGNORECASE,
)

"""
Wealthsimple email parsing patterns used by `email_extractor.py`.
"""
WEALTHSIMPLE_DATE_PATTERNS = [
    r"Time:\s*(.+)",
    r"Filled at:\s*(.+)",
    r"Placed at:\s*(.+)",
    r"Submitted at:\s*(.+)",
    r"Executed at:\s*(.+)",
    r"Order date:\s*(.+)",
    r"Date:\s*(.+)",
    r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}(?:,\s+\d{1,2}:\d{2}\s*(?:AM|PM))?)",
    r"(\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
]

"""
Statement extractor config used by `statement_extractor.py`.
"""
STATEMENT_OUTPUT_COLUMNS = [
    "date",
    "transaction",
    "ticker_id",
    "quantity",
    "execDate",
    "fx_rate",
    "debit",
    "credit",
    "balance",
    "statement_code",
    "description",
]

ACTIVITY_HEADING = "Activity - Current period"
FUTURE_SETTLEMENT_HEADING = "Transactions for Future Settlement"
GLOSSARY_HEADING = "Information about Statement Codes"
DATE_CODE_PATTERN = re.compile(r"^\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<code>[A-Z0-9]+)\b")
MONEY_AT_END_PATTERN = re.compile(
    r"^(?P<prefix>.*?)\s+"
    r"(?P<debit>\$[\d,]+\.\d{2})\s+"
    r"(?P<credit>\$[\d,]+\.\d{2})\s+"
    r"(?P<balance>\$[\d,]+\.\d{2})\s*$"
)
GLOSSARY_ENTRY_PATTERN = re.compile(r"^(?P<code>[A-Z0-9]+)\s+-\s+(?P<description>.+)$")

"""
CSV sorter config used by `data_sorter.py`.
"""
SOURCE_PREFIX = "activities-export-"
PROCESSED_FOLDER_NAME = "processed_data"
REQUIRED_COLUMNS = {
    "transaction_date",
    "settlement_date",
    "account_id",
    "account_type",
    "activity_type",
    "activity_sub_type",
    "direction",
    "symbol",
    "name",
    "currency",
    "quantity",
    "unit_price",
    "commission",
    "net_cash_amount",
}
ACTIVITY_EXPORT_COLUMNS = [
    "transaction_date",
    "settlement_date",
    "account_id",
    "account_type",
    "activity_type",
    "activity_sub_type",
    "direction",
    "symbol",
    "name",
    "currency",
    "quantity",
    "unit_price",
    "commission",
    "net_cash_amount",
]
DROP_COLUMNS: set[str] = set()
COLUMN_RENAMES = {
    column: column for column in ACTIVITY_EXPORT_COLUMNS
}
ACTIVITY_TYPE_MAPPING = {
    "MoneyMovement": "CONT",
    "Dividend": "DIV",
    "Interest": "INT",
}

"""
YFinance extractor config used by `yfinance_extractor.py`.
"""
YFINANCE_STOCK_INFO_COLUMNS = [
    "ticker",
    "provider_symbol",
    "company_name",
    "asset",
    "exchange",
    "currency",
    "financial_currency",
    "sector",
    "industry",
]
YFINANCE_ETF_INFO_COLUMNS = [
    "ticker",
    "provider_symbol",
    "company_name",
    "exchange",
    "currency",
    "financial_currency",
    "fund_family",
    "asset",
    "yield",
    "expense_ratio",
    "aum",
    "nav",
    "top_holdings",
    "sector_weights",
]
YFINANCE_HISTORY_COLUMNS = [
    "Date",
    "Ticker",
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
]
YFINANCE_RETRY_QUOTE_TYPES = ("ECNQUOTE", None)
YFINANCE_CANADIAN_SUFFIX = ".TO"
YFINANCE_CANADIAN_SUFFIXES = (".TO", ".V", ".CN", ".NE")
# Explicit provider aliases take precedence over saved and automatically resolved
# mappings. Keys are (canonical Wealthsimple symbol, source currency).
YFINANCE_SYMBOL_OVERRIDES: dict[tuple[str, str], str] = {}
YFINANCE_MAX_WORKERS = 10
YFINANCE_SESSION_IMPERSONATE = "chrome"
YFINANCE_DOWNLOAD_AUTO_ADJUST = False
YFINANCE_DOWNLOAD_GROUP_BY = "ticker"
YFINANCE_DOWNLOAD_THREADS = True
