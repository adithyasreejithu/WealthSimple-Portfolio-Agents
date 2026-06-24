from __future__ import annotations

import os
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]

"""
Shared folder paths used by multiple source files.
"""
DATA_FOLDER = BASE_DIR / "Data"
EXPORT_FOLDER = BASE_DIR / "exports"
DEFAULT_DATA_FOLDER = DATA_FOLDER
DEFAULT_EXPORT_FOLDER = EXPORT_FOLDER
LOG_FOLDER = BASE_DIR / "logs"
DATABASE_PATH = Path(
    os.getenv("DB_PATH", str(DATA_FOLDER / "PRD_WealthSimple.duckdb"))
).expanduser()
DATABASE_SCHEMA_VERSION = 2

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
]

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
    "company_name",
    "asset",
    "exchange",
    "currency",
    "sector",
    "industry",
]
YFINANCE_ETF_INFO_COLUMNS = [
    "ticker",
    "company_name",
    "currency",
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
YFINANCE_MAX_WORKERS = 10
YFINANCE_SESSION_IMPERSONATE = "chrome"
YFINANCE_DOWNLOAD_AUTO_ADJUST = False
YFINANCE_DOWNLOAD_GROUP_BY = "ticker"
YFINANCE_DOWNLOAD_THREADS = True
