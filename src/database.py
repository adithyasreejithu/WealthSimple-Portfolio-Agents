"""DuckDB connection lifecycle and normalized application schema."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb

from config import DATABASE_PATH, DATABASE_SCHEMA_VERSION
from system_logger import get_logger


logger = get_logger(__name__)

SCHEMA_COMPONENT = "portfolio_database"
REQUIRED_TABLES = frozenset(
    {
        "schema_metadata",
        "tickers",
        "stock_details",
        "etf_details",
        "transactions",
        "cash_transactions",
        "historical_records",
        "email_checkpoints",
        "email_transactions",
        "activity_imports",
        "raw_activity_exports",
        "activities",
    }
)

_connection: duckdb.DuckDBPyConnection | None = None
_connection_path: Path | None = None


def _resolve_database_path(db_path: str | Path) -> Path:
    """Return an absolute database path without requiring it to exist."""
    return Path(db_path).expanduser().resolve()


def get_shared_connection(
    db_path: str | Path = DATABASE_PATH,
) -> duckdb.DuckDBPyConnection:
    """Return the process-wide DuckDB connection for one database path."""
    global _connection, _connection_path

    resolved_path = _resolve_database_path(db_path)
    if _connection is not None:
        if resolved_path != _connection_path:
            raise RuntimeError(
                "A DuckDB connection is already open for "
                f"{_connection_path}; close it before opening {resolved_path}."
            )
        return _connection

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    _connection = duckdb.connect(str(resolved_path))
    _connection_path = resolved_path
    logger.info("Database connection opened: %s", resolved_path)
    return _connection


def close_connection() -> None:
    """Close the process-wide DuckDB connection if one is open."""
    global _connection, _connection_path

    if _connection is None:
        return

    connection_path = _connection_path
    _connection.close()
    _connection = None
    _connection_path = None
    logger.info("Database connection closed: %s", connection_path)


@contextmanager
def get_connection(
    db_path: str | Path = DATABASE_PATH,
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Yield the shared connection without closing it after each operation."""
    connection = get_shared_connection(db_path)
    try:
        yield connection
    except Exception:
        logger.exception("Database operation failed")
        raise


def _get_table_names(connection: duckdb.DuckDBPyConnection) -> set[str]:
    rows = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_type = 'BASE TABLE'
        """
    ).fetchall()
    return {row[0] for row in rows}


def is_database_active(connection: duckdb.DuckDBPyConnection) -> bool:
    """Return whether the complete current schema is installed."""
    table_names = _get_table_names(connection)
    if not REQUIRED_TABLES.issubset(table_names):
        return False

    row = connection.execute(
        """
        SELECT schema_version
        FROM schema_metadata
        WHERE component = ?
        """,
        [SCHEMA_COMPONENT],
    ).fetchone()
    return row is not None and row[0] == DATABASE_SCHEMA_VERSION


def _deploy_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the complete normalized schema in one transaction."""
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            CREATE TABLE schema_metadata (
                component VARCHAR PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("CREATE SEQUENCE ticker_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE tickers (
                ticker_id BIGINT PRIMARY KEY DEFAULT nextval('ticker_id_sequence'),
                ticker_symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                currency VARCHAR(10) NOT NULL,
                security_name VARCHAR NOT NULL,
                security_type VARCHAR NOT NULL,
                CHECK (ticker_symbol = UPPER(TRIM(ticker_symbol))),
                CHECK (exchange = UPPER(TRIM(exchange))),
                CHECK (currency = UPPER(TRIM(currency))),
                UNIQUE (ticker_symbol, exchange)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE stock_details (
                ticker_id BIGINT PRIMARY KEY,
                sector VARCHAR,
                industry VARCHAR,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE etf_details (
                ticker_id BIGINT PRIMARY KEY,
                fund_family VARCHAR,
                yield DECIMAL(18, 8),
                expense_ratio DECIMAL(18, 8),
                aum DECIMAL(20, 2),
                nav DECIMAL(20, 6),
                top_holdings JSON,
                sector_weights JSON,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
            )
            """
        )
        connection.execute("CREATE SEQUENCE activity_import_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE activity_imports (
                import_id BIGINT PRIMARY KEY
                    DEFAULT nextval('activity_import_id_sequence'),
                source_file VARCHAR NOT NULL,
                file_hash VARCHAR NOT NULL UNIQUE,
                imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR NOT NULL,
                source_row_count INTEGER NOT NULL DEFAULT 0,
                normalized_row_count INTEGER NOT NULL DEFAULT 0,
                unresolved_ticker_count INTEGER NOT NULL DEFAULT 0,
                duplicate_row_count INTEGER NOT NULL DEFAULT 0,
                error_message VARCHAR
            )
            """
        )
        connection.execute("CREATE SEQUENCE raw_activity_export_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE raw_activity_exports (
                raw_activity_export_id BIGINT PRIMARY KEY
                    DEFAULT nextval('raw_activity_export_id_sequence'),
                import_id BIGINT NOT NULL,
                source_row_number INTEGER NOT NULL,
                transaction_date VARCHAR,
                settlement_date VARCHAR,
                account_id VARCHAR,
                account_type VARCHAR,
                activity_type VARCHAR,
                activity_sub_type VARCHAR,
                direction VARCHAR,
                symbol VARCHAR,
                name VARCHAR,
                currency VARCHAR,
                quantity VARCHAR,
                unit_price VARCHAR,
                commission VARCHAR,
                net_cash_amount VARCHAR,
                row_fingerprint VARCHAR NOT NULL,
                duplicate_ordinal INTEGER NOT NULL,
                FOREIGN KEY (import_id) REFERENCES activity_imports(import_id),
                UNIQUE (import_id, source_row_number)
            )
            """
        )
        connection.execute("CREATE SEQUENCE activity_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE activities (
                activity_id BIGINT PRIMARY KEY
                    DEFAULT nextval('activity_id_sequence'),
                transaction_date DATE NOT NULL,
                settlement_date DATE,
                account_id VARCHAR NOT NULL,
                account_type VARCHAR NOT NULL,
                activity_type VARCHAR,
                activity_subtype VARCHAR,
                activity_code VARCHAR NOT NULL,
                direction VARCHAR,
                ticker_id BIGINT,
                transaction_currency VARCHAR(10),
                quantity DECIMAL(20, 8),
                unit_price DECIMAL(20, 8),
                commission_amount DECIMAL(20, 4),
                net_cash_amount DECIMAL(20, 4),
                row_fingerprint VARCHAR NOT NULL,
                duplicate_ordinal INTEGER NOT NULL,
                first_seen_import_id BIGINT NOT NULL,
                last_seen_import_id BIGINT NOT NULL,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                FOREIGN KEY (first_seen_import_id) REFERENCES activity_imports(import_id),
                FOREIGN KEY (last_seen_import_id) REFERENCES activity_imports(import_id),
                UNIQUE (row_fingerprint, duplicate_ordinal)
            )
            """
        )
        connection.execute("CREATE SEQUENCE transaction_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE transactions (
                transaction_id BIGINT PRIMARY KEY
                    DEFAULT nextval('transaction_id_sequence'),
                transaction_date DATE NOT NULL,
                transaction_type VARCHAR NOT NULL,
                ticker_id BIGINT NOT NULL,
                quantity DECIMAL(20, 8),
                execution_date DATE,
                debit DECIMAL(20, 4),
                credit DECIMAL(20, 4),
                fx_rate DECIMAL(18, 8),
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                UNIQUE (
                    transaction_date,
                    transaction_type,
                    ticker_id,
                    quantity,
                    execution_date,
                    debit,
                    credit,
                    fx_rate
                )
            )
            """
        )
        connection.execute("CREATE SEQUENCE cash_transaction_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE cash_transactions (
                cash_transaction_id BIGINT PRIMARY KEY
                    DEFAULT nextval('cash_transaction_id_sequence'),
                transaction_date DATE NOT NULL,
                transaction_type VARCHAR NOT NULL,
                execution_date DATE,
                debit DECIMAL(20, 4) NOT NULL DEFAULT 0,
                credit DECIMAL(20, 4) NOT NULL DEFAULT 0,
                fx_rate DECIMAL(18, 8) NOT NULL DEFAULT 0,
                balance DECIMAL(20, 4),
                UNIQUE (
                    transaction_date,
                    transaction_type,
                    execution_date,
                    debit,
                    credit,
                    fx_rate,
                    balance
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE historical_records (
                ticker_id BIGINT NOT NULL,
                record_date DATE NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                adjusted_close DOUBLE NOT NULL,
                volume BIGINT NOT NULL,
                PRIMARY KEY (ticker_id, record_date),
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
            )
            """
        )
        connection.execute("CREATE SEQUENCE email_checkpoint_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE email_checkpoints (
                email_checkpoint_id BIGINT PRIMARY KEY
                    DEFAULT nextval('email_checkpoint_id_sequence'),
                source VARCHAR NOT NULL UNIQUE,
                checked_through_date DATE NOT NULL,
                email_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("CREATE SEQUENCE email_transaction_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE email_transactions (
                email_transaction_id BIGINT PRIMARY KEY
                    DEFAULT nextval('email_transaction_id_sequence'),
                account VARCHAR,
                transaction_type VARCHAR NOT NULL,
                ticker_id BIGINT,
                quantity DECIMAL(20, 8),
                average_price DECIMAL(20, 6),
                total_cost DECIMAL(20, 4),
                debit DECIMAL(20, 4),
                transaction_date DATE NOT NULL,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                UNIQUE (
                    account,
                    transaction_type,
                    ticker_id,
                    quantity,
                    average_price,
                    total_cost,
                    debit,
                    transaction_date
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_metadata (component, schema_version)
            VALUES (?, ?)
            """,
            [SCHEMA_COMPONENT, DATABASE_SCHEMA_VERSION],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        logger.exception("Database schema creation failed; transaction rolled back")
        raise


def initialize_database(db_path: str | Path = DATABASE_PATH) -> bool:
    """
    Ensure the current schema exists.

    Returns True when the schema is created and False when it was already active.
    """
    with get_connection(db_path) as connection:
        if is_database_active(connection):
            logger.info(
                "Database schema is active at version %d",
                DATABASE_SCHEMA_VERSION,
            )
            return False

        existing_tables = _get_table_names(connection)
        if existing_tables:
            raise RuntimeError(
                "Database contains an incomplete or incompatible schema. "
                "Automatic migration is not implemented."
            )

        logger.info(
            "Database schema is inactive; creating version %d",
            DATABASE_SCHEMA_VERSION,
        )
        _deploy_schema(connection)
        logger.info("Database schema creation complete")
        return True
