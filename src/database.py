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
        "portfolio_classifications",
        "ticker_provider_mappings",
        "ticker_symbol_history",
        "ingestion_batches",
        "staged_files",
        "staged_records",
        "transactions",
        "cash_transactions",
        "historical_records",
        "email_checkpoints",
        "email_messages",
        "email_transactions",
        "activity_imports",
        "raw_activity_exports",
        "activities",
        "position_ledger",
        "position_snapshots",
        "position_engine_meta",
        "statement_balances",
        "earnings_events",
        "dividend_events",
        "financial_snapshots",
    }
)
TRADE_EVENTS_VIEW = "v_trade_events"

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


def _create_position_engine_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the position engine's ledger/snapshot/fingerprint tables.

    Shared between `_deploy_schema` (fresh installs) and the v9->10 migration
    so both paths stay in lockstep; see `src/position_engine.py` for the
    Python code that populates these tables.
    """
    connection.execute("CREATE SEQUENCE IF NOT EXISTS position_ledger_id_sequence START 1")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS position_ledger (
            ledger_id BIGINT PRIMARY KEY DEFAULT nextval('position_ledger_id_sequence'),
            ticker_id BIGINT NOT NULL,
            event_date DATE NOT NULL,
            event_type VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            source_id BIGINT NOT NULL,
            quantity_delta DECIMAL(20, 8) NOT NULL,
            cost_cad DECIMAL(20, 4),
            proceeds_cad DECIMAL(20, 4),
            fx_rate DECIMAL(18, 8),
            running_quantity DECIMAL(20, 8) NOT NULL,
            running_book_cad DECIMAL(20, 4) NOT NULL,
            running_book_mkt DECIMAL(20, 4) NOT NULL,
            realized_gain_cad DECIMAL(20, 4) NOT NULL DEFAULT 0,
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS position_snapshots (
            ticker_id BIGINT PRIMARY KEY,
            quantity DECIMAL(20, 8) NOT NULL,
            book_value_cad DECIMAL(20, 4) NOT NULL,
            book_value_mkt DECIMAL(20, 4) NOT NULL,
            realized_gain_cad DECIMAL(20, 4) NOT NULL,
            provisional_quantity DECIMAL(20, 8) NOT NULL DEFAULT 0,
            data_quality_flags JSON,
            computed_at TIMESTAMP NOT NULL,
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS position_engine_meta (
            component VARCHAR PRIMARY KEY,
            ledger_fingerprint VARCHAR NOT NULL,
            computed_at TIMESTAMP NOT NULL
        )
        """
    )


def _apply_v10_position_engine_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Migrate an existing v9 database up to the v10 position-engine schema."""
    connection.execute(
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS superseded_by_activity_id BIGINT"
    )
    connection.execute(
        "ALTER TABLE email_transactions ADD COLUMN IF NOT EXISTS matched_activity_id BIGINT"
    )
    _create_position_engine_tables(connection)


def _create_statement_balances_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the table tracking every statement line's trailing balance.

    Shared between `_deploy_schema` (fresh installs) and the v10->v11
    migration. A statement PDF states a running `balance` on *every* line,
    but `upload_statement_transactions` only used to persist it for lines
    with no resolved ticker (`cash_transactions.balance`) -- BUY/SELL/DIV
    rows tied to a security went to `transactions`, which has no `balance`
    column, silently discarding it. `get_cash_summary` (`analytics.py`) then
    had no choice but to report the latest *cash-only* balance, which is
    wrong whenever a trade is the last line of the statement. This table
    captures the balance from every line regardless of ticker resolution, so
    the true most recent balance is always available (`analytics.py`'s
    `get_cash_summary`, ordered by `transaction_date DESC,
    statement_balance_id DESC`, the same insertion-order tie-break already
    used for `cash_transactions`).
    """
    connection.execute("CREATE SEQUENCE IF NOT EXISTS statement_balance_id_sequence START 1")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS statement_balances (
            statement_balance_id BIGINT PRIMARY KEY
                DEFAULT nextval('statement_balance_id_sequence'),
            transaction_date DATE NOT NULL,
            transaction_type VARCHAR NOT NULL,
            balance DECIMAL(20, 4) NOT NULL,
            UNIQUE (transaction_date, transaction_type, balance)
        )
        """
    )


def _create_earnings_dividends_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the earnings_events/dividend_events market-data tables.

    Shared between `_deploy_schema` (fresh installs) and the v11->v12
    migration so both paths stay in lockstep. These record company-declared
    market data (earnings calendar, dividend schedule), distinct from
    `cash_transactions`/`transactions`, which record the user's own received
    dividend cash from brokerage statements -- never join or conflate them.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_events (
            ticker_id BIGINT NOT NULL,
            report_date DATE NOT NULL,
            period VARCHAR,
            eps_estimate DOUBLE,
            eps_actual DOUBLE,
            revenue_estimate DECIMAL(20, 2),
            revenue_actual DECIMAL(20, 2),
            surprise_pct DOUBLE,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, report_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dividend_events (
            ticker_id BIGINT NOT NULL,
            ex_dividend_date DATE NOT NULL,
            pay_date DATE,
            declared_amount DECIMAL(18, 8) NOT NULL,
            frequency VARCHAR,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, ex_dividend_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )


def _create_financial_snapshots_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the financial_snapshots table (per-quarter statement data).

    Shared between `_deploy_schema` (fresh installs) and the v12->v13
    migration so both paths stay in lockstep. Hybrid schema: named columns
    for the fields decision-rubric.yml's derived:* metrics actually score
    against today, plus one JSON `extra` column for every other line item
    yfinance's quarterly income statement/balance sheet/cash flow return, so
    a new ratio never requires its own migration. Figures are stored exactly
    as yfinance reports them in the ticker's financial_currency -- no FX
    conversion here (see analytics/portfolio_metrics for that).

    Unlike dividend_events/earnings_events, a row here can legitimately be
    overwritten in place on re-sync: quarterly figures can restate after the
    fact (reclassifications, discontinued-operations restatement, vendor
    data corrections), so ON CONFLICT DO UPDATE is the correct and desired
    behavior, not a bug -- a period_end_date is never a forward-looking or
    speculative row (a snapshot cannot exist before its period has ended and
    been reported), so there is nothing to retire the way
    upload_earnings_events retires abandoned speculative report dates.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_snapshots (
            ticker_id BIGINT NOT NULL,
            period_end_date DATE NOT NULL,
            revenue DECIMAL(24, 2),
            net_income DECIMAL(24, 2),
            eps DOUBLE,
            gross_margin DOUBLE,
            operating_margin DOUBLE,
            debt_to_equity DOUBLE,
            current_ratio DOUBLE,
            free_cash_flow DECIMAL(24, 2),
            extra JSON,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker_id, period_end_date),
            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
        )
        """
    )


def _create_trade_events_view(connection: duckdb.DuckDBPyConnection) -> None:
    """(Re)create the unified BUY/SELL/SPLIT event view read by the position engine.

    Source precedence is activities > statements > email (see
    docs/architecture/ingestion_and_reconciliation.md). Precedence is applied
    here by excluding statement rows that a reconciliation pass has linked to
    an activities row (`superseded_by_activity_id`) and by only including
    email rows still `provisional` (matched/superseded email rows are already
    represented by the statement or activities row they were matched to).
    STKREORG statement rows carry no quantity and are intentionally excluded;
    a split with no corresponding `activities` CorporateAction row is invisible
    to this view, which the position engine flags as `split_without_quantity`.
    """
    connection.execute(
        f"""
        CREATE OR REPLACE VIEW {TRADE_EVENTS_VIEW} AS
        WITH activity_events AS (
            SELECT
                activity_id AS source_id,
                ticker_id,
                transaction_date AS event_date,
                CASE
                    WHEN activity_type = 'CorporateAction' THEN 'SPLIT'
                    WHEN quantity < 0 OR UPPER(COALESCE(activity_subtype, '')) = 'SELL' THEN 'SELL'
                    ELSE 'BUY'
                END AS event_type,
                CASE
                    WHEN activity_type = 'CorporateAction' THEN quantity
                    ELSE ABS(quantity)
                END AS quantity,
                CASE WHEN activity_type = 'Trade' THEN ABS(net_cash_amount) END AS amount_cad,
                transaction_currency AS amount_currency,
                CAST(NULL AS DECIMAL(18, 8)) AS fx_rate,
                'activities' AS source,
                1 AS source_priority
            FROM activities
            WHERE ticker_id IS NOT NULL
              AND (
                    activity_type = 'Trade'
                    OR (activity_type = 'CorporateAction' AND quantity IS NOT NULL)
              )
        ),
        statement_events AS (
            SELECT
                transaction_id AS source_id,
                ticker_id,
                COALESCE(execution_date, transaction_date) AS event_date,
                UPPER(transaction_type) AS event_type,
                ABS(quantity) AS quantity,
                CASE WHEN UPPER(transaction_type) = 'BUY' THEN debit ELSE credit END AS amount_cad,
                'CAD' AS amount_currency,
                fx_rate,
                'statements' AS source,
                2 AS source_priority
            FROM transactions
            WHERE UPPER(transaction_type) IN ('BUY', 'SELL')
              AND superseded_by_activity_id IS NULL
        ),
        email_events AS (
            SELECT
                email_transaction_id AS source_id,
                ticker_id,
                transaction_date AS event_date,
                CASE WHEN UPPER(transaction_type) LIKE '%SELL%' THEN 'SELL' ELSE 'BUY' END AS event_type,
                ABS(quantity) AS quantity,
                total_cost AS amount_cad,
                'CAD' AS amount_currency,
                CAST(NULL AS DECIMAL(18, 8)) AS fx_rate,
                'email' AS source,
                3 AS source_priority
            FROM email_transactions
            WHERE ticker_id IS NOT NULL
              AND ticker_resolution_status = 'resolved'
              AND reconciliation_status = 'provisional'
              AND (UPPER(transaction_type) LIKE '%BUY%' OR UPPER(transaction_type) LIKE '%SELL%')
        )
        SELECT * FROM activity_events
        UNION ALL
        SELECT * FROM statement_events
        UNION ALL
        SELECT * FROM email_events
        """
    )


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
                financial_currency VARCHAR(10),
                contains_fx_rate VARCHAR(3),
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
        connection.execute(
            """
            CREATE TABLE portfolio_classifications (
                ticker_id BIGINT PRIMARY KEY,
                primary_group VARCHAR NOT NULL,
                secondary_tags JSON,
                confidence VARCHAR,
                reasoning VARCHAR,
                evidence_used JSON,
                missing_data JSON,
                review_needed BOOLEAN NOT NULL,
                fields JSON,
                field_provenance JSON,
                enrichment JSON,
                generated_at TIMESTAMP NOT NULL,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE ticker_provider_mappings (
                ticker_id BIGINT NOT NULL,
                provider VARCHAR NOT NULL,
                provider_symbol VARCHAR NOT NULL,
                verification_status VARCHAR NOT NULL,
                mapping_source VARCHAR NOT NULL DEFAULT 'automatic',
                effective_from DATE,
                effective_to DATE,
                reason VARCHAR,
                created_by VARCHAR NOT NULL DEFAULT 'pipeline',
                verified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker_id, provider),
                UNIQUE (provider, provider_symbol),
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                CHECK (provider = LOWER(TRIM(provider))),
                CHECK (provider_symbol = UPPER(TRIM(provider_symbol)))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE ticker_symbol_history (
                ticker_id BIGINT NOT NULL,
                source_symbol VARCHAR NOT NULL,
                provider_symbol VARCHAR NOT NULL,
                currency VARCHAR(10),
                exchange VARCHAR,
                effective_from DATE,
                effective_to DATE,
                reason VARCHAR NOT NULL,
                mapping_source VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                UNIQUE (source_symbol, currency, effective_from)
            )
            """
        )
        connection.execute("CREATE SEQUENCE ingestion_batch_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE ingestion_batches (
                batch_id BIGINT PRIMARY KEY DEFAULT nextval('ingestion_batch_id_sequence'),
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status VARCHAR NOT NULL,
                error_message VARCHAR
            )
            """
        )
        connection.execute("CREATE SEQUENCE staged_file_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE staged_files (
                staged_file_id BIGINT PRIMARY KEY DEFAULT nextval('staged_file_id_sequence'),
                batch_id BIGINT NOT NULL,
                source_type VARCHAR NOT NULL,
                source_path VARCHAR,
                source_hash VARCHAR NOT NULL,
                file_sequence INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                error_message VARCHAR,
                staged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES ingestion_batches(batch_id),
                UNIQUE (batch_id, file_sequence)
            )
            """
        )
        connection.execute("CREATE SEQUENCE staged_record_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE staged_records (
                staged_record_id BIGINT PRIMARY KEY DEFAULT nextval('staged_record_id_sequence'),
                staged_file_id BIGINT NOT NULL,
                record_sequence INTEGER NOT NULL,
                transaction_date DATE,
                transaction_type VARCHAR,
                source_symbol VARCHAR,
                security_name VARCHAR,
                fx_rate DECIMAL(18, 8),
                contains_fx_rate VARCHAR(3),
                price_currency VARCHAR(10),
                inferred_listing_currency VARCHAR(10),
                listing_evidence VARCHAR,
                ticker_id BIGINT,
                resolution_method VARCHAR,
                resolution_status VARCHAR NOT NULL DEFAULT 'pending',
                raw_payload JSON NOT NULL,
                normalized_payload JSON NOT NULL,
                FOREIGN KEY (staged_file_id) REFERENCES staged_files(staged_file_id),
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                UNIQUE (staged_file_id, record_sequence)
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
                -- Soft link to activities.activity_id, deliberately not a
                -- FOREIGN KEY: reconciliation passes bulk-UPDATE this table,
                -- and DuckDB disallows updating a table that is a live FK
                -- target from another table's column.
                superseded_by_activity_id BIGINT,
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
                checked_through_at TIMESTAMP,
                email_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("CREATE SEQUENCE email_transaction_id_sequence START 1")
        connection.execute("CREATE SEQUENCE email_message_id_sequence START 1")
        connection.execute(
            """
            CREATE TABLE email_messages (
                email_message_id BIGINT PRIMARY KEY
                    DEFAULT nextval('email_message_id_sequence'),
                source VARCHAR NOT NULL,
                source_message_id VARCHAR NOT NULL,
                received_at TIMESTAMP,
                content_hash VARCHAR NOT NULL,
                processing_status VARCHAR NOT NULL DEFAULT 'stored',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source, source_message_id)
            )
            """
        )
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
                email_message_id BIGINT,
                source_symbol VARCHAR,
                price_currency VARCHAR(10),
                ticker_resolution_status VARCHAR NOT NULL DEFAULT 'resolved',
                reconciliation_status VARCHAR NOT NULL DEFAULT 'provisional',
                -- Soft links to transactions.transaction_id / activities.activity_id,
                -- deliberately not FOREIGN KEYs: reconciliation passes bulk-UPDATE
                -- both parent tables, and DuckDB disallows updating a table that
                -- is a live FK target from another table's column.
                matched_transaction_id BIGINT,
                matched_activity_id BIGINT,
                FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                FOREIGN KEY (email_message_id) REFERENCES email_messages(email_message_id),
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
        _create_position_engine_tables(connection)
        _create_statement_balances_table(connection)
        _create_earnings_dividends_tables(connection)
        _create_financial_snapshots_table(connection)
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
            # View logic ships with code, not with the DB file, so it is
            # recreated on every startup regardless of migration state.
            _create_trade_events_view(connection)
            return False

        existing_tables = _get_table_names(connection)
        if "schema_metadata" in existing_tables:
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE component = ?",
                [SCHEMA_COMPONENT],
            ).fetchone()
            if row and row[0] == 2:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        """
                        CREATE TABLE ticker_provider_mappings (
                            ticker_id BIGINT NOT NULL,
                            provider VARCHAR NOT NULL,
                            provider_symbol VARCHAR NOT NULL,
                            verification_status VARCHAR NOT NULL,
                            verified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (ticker_id, provider),
                            UNIQUE (provider, provider_symbol),
                            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                            CHECK (provider = LOWER(TRIM(provider))),
                            CHECK (provider_symbol = UPPER(TRIM(provider_symbol)))
                        )
                        """
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [3, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 2 failed")
                    raise
                logger.info("Database migrated from schema version 2 to 3")
                row = (3,)
            if row and row[0] == 3:
                connection.execute("BEGIN TRANSACTION")
                try:
                    for definition in (
                        "mapping_source VARCHAR DEFAULT 'automatic'",
                        "effective_from DATE",
                        "effective_to DATE",
                        "reason VARCHAR",
                        "created_by VARCHAR DEFAULT 'pipeline'",
                    ):
                        connection.execute(f"ALTER TABLE ticker_provider_mappings ADD COLUMN {definition}")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS ticker_symbol_history (
                            ticker_id BIGINT NOT NULL, source_symbol VARCHAR NOT NULL,
                            provider_symbol VARCHAR NOT NULL, currency VARCHAR(10), exchange VARCHAR,
                            effective_from DATE, effective_to DATE, reason VARCHAR NOT NULL,
                            mapping_source VARCHAR NOT NULL, created_by VARCHAR NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                            UNIQUE (source_symbol, currency, effective_from)
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO ticker_symbol_history (
                            ticker_id, source_symbol, provider_symbol, currency, exchange,
                            reason, mapping_source, created_by
                        )
                        SELECT t.ticker_id, t.ticker_symbol, m.provider_symbol, t.currency,
                               t.exchange, 'migrated provider mapping', 'migration', 'schema-v4'
                        FROM ticker_provider_mappings m JOIN tickers t USING (ticker_id)
                        """
                    )
                    connection.execute("CREATE SEQUENCE IF NOT EXISTS ingestion_batch_id_sequence START 1")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS ingestion_batches (
                            batch_id BIGINT PRIMARY KEY DEFAULT nextval('ingestion_batch_id_sequence'),
                            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            completed_at TIMESTAMP, status VARCHAR NOT NULL, error_message VARCHAR
                        )
                        """
                    )
                    connection.execute("CREATE SEQUENCE IF NOT EXISTS staged_file_id_sequence START 1")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS staged_files (
                            staged_file_id BIGINT PRIMARY KEY DEFAULT nextval('staged_file_id_sequence'),
                            batch_id BIGINT NOT NULL, source_type VARCHAR NOT NULL, source_path VARCHAR,
                            source_hash VARCHAR NOT NULL, file_sequence INTEGER NOT NULL,
                            status VARCHAR NOT NULL, error_message VARCHAR,
                            staged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TIMESTAMP,
                            FOREIGN KEY (batch_id) REFERENCES ingestion_batches(batch_id),
                            UNIQUE (batch_id, file_sequence)
                        )
                        """
                    )
                    connection.execute("CREATE SEQUENCE IF NOT EXISTS staged_record_id_sequence START 1")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS staged_records (
                            staged_record_id BIGINT PRIMARY KEY DEFAULT nextval('staged_record_id_sequence'),
                            staged_file_id BIGINT NOT NULL, record_sequence INTEGER NOT NULL,
                            transaction_date DATE, transaction_type VARCHAR, source_symbol VARCHAR,
                            security_name VARCHAR, fx_rate DECIMAL(18,8), price_currency VARCHAR(10),
                            ticker_id BIGINT, resolution_method VARCHAR,
                            resolution_status VARCHAR NOT NULL DEFAULT 'pending',
                            raw_payload JSON NOT NULL, normalized_payload JSON NOT NULL,
                            FOREIGN KEY (staged_file_id) REFERENCES staged_files(staged_file_id),
                            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id),
                            UNIQUE (staged_file_id, record_sequence)
                        )
                        """
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [4, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 3 failed")
                    raise
                logger.info("Database migrated from schema version 3 to 4")
                row = (4,)
            if row and row[0] == 4:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute("ALTER TABLE tickers ADD COLUMN IF NOT EXISTS financial_currency VARCHAR(10)")
                    connection.execute("ALTER TABLE staged_records ADD COLUMN IF NOT EXISTS inferred_listing_currency VARCHAR(10)")
                    connection.execute("ALTER TABLE staged_records ADD COLUMN IF NOT EXISTS listing_evidence VARCHAR")
                    connection.execute(
                        """
                        UPDATE tickers SET currency = 'CAD'
                        WHERE ticker_id IN (
                            SELECT ticker_id FROM ticker_provider_mappings
                            WHERE provider_symbol LIKE '%.TO'
                               OR provider_symbol LIKE '%.V'
                               OR provider_symbol LIKE '%.CN'
                               OR provider_symbol LIKE '%.NE'
                        )
                        """
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [5, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 4 failed")
                    raise
                logger.info("Database migrated from schema version 4 to 5")
                row = (5,)
            if row and row[0] == 5:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        "ALTER TABLE staged_records ADD COLUMN IF NOT EXISTS contains_fx_rate VARCHAR(3)"
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [6, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 5 failed")
                    raise
                logger.info("Database migrated from schema version 5 to 6")
                row = (6,)
            if row and row[0] == 6:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        "ALTER TABLE tickers ADD COLUMN IF NOT EXISTS contains_fx_rate VARCHAR(3)"
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [7, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 6 failed")
                    raise
                logger.info("Database migrated from schema version 6 to 7")
                row = (7,)
            if row and row[0] == 7:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute("CREATE SEQUENCE IF NOT EXISTS email_message_id_sequence START 1")
                    connection.execute(
                        "ALTER TABLE email_checkpoints ADD COLUMN IF NOT EXISTS checked_through_at TIMESTAMP"
                    )
                    connection.execute(
                        "UPDATE email_checkpoints SET checked_through_at = CAST(checked_through_date AS TIMESTAMP) "
                        "WHERE checked_through_at IS NULL"
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS email_messages (
                            email_message_id BIGINT PRIMARY KEY
                                DEFAULT nextval('email_message_id_sequence'),
                            source VARCHAR NOT NULL,
                            source_message_id VARCHAR NOT NULL,
                            received_at TIMESTAMP,
                            content_hash VARCHAR NOT NULL,
                            processing_status VARCHAR NOT NULL DEFAULT 'stored',
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE (source, source_message_id)
                        )
                        """
                    )
                    for definition in (
                        "email_message_id BIGINT",
                        "source_symbol VARCHAR",
                        "price_currency VARCHAR(10)",
                        "ticker_resolution_status VARCHAR DEFAULT 'resolved'",
                        "reconciliation_status VARCHAR DEFAULT 'provisional'",
                        "matched_transaction_id BIGINT",
                    ):
                        connection.execute(
                            f"ALTER TABLE email_transactions ADD COLUMN IF NOT EXISTS {definition}"
                        )
                    connection.execute(
                        "UPDATE email_transactions SET ticker_resolution_status = "
                        "CASE WHEN ticker_id IS NULL THEN 'pending' ELSE 'resolved' END "
                        "WHERE ticker_resolution_status IS NULL"
                    )
                    connection.execute(
                        """
                        UPDATE email_transactions SET reconciliation_status =
                            CASE
                                WHEN UPPER(transaction_type) LIKE '%BUY%'
                                  OR UPPER(transaction_type) LIKE '%SELL%'
                                THEN 'provisional'
                                ELSE 'not_applicable'
                            END
                        WHERE reconciliation_status IS NULL
                        """
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [8, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 7 failed")
                    raise
                logger.info("Database migrated from schema version 7 to 8")
                row = (8,)
            if row and row[0] == 8:
                connection.execute("BEGIN TRANSACTION")
                try:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS portfolio_classifications (
                            ticker_id BIGINT PRIMARY KEY,
                            primary_group VARCHAR NOT NULL,
                            secondary_tags JSON,
                            confidence VARCHAR,
                            reasoning VARCHAR,
                            evidence_used JSON,
                            missing_data JSON,
                            review_needed BOOLEAN NOT NULL,
                            fields JSON,
                            field_provenance JSON,
                            enrichment JSON,
                            generated_at TIMESTAMP NOT NULL,
                            FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)
                        )
                        """
                    )
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [9, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 8 failed")
                    raise
                logger.info("Database migrated from schema version 8 to 9")
                row = (9,)
            if row and row[0] == 9:
                connection.execute("BEGIN TRANSACTION")
                try:
                    _apply_v10_position_engine_schema(connection)
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [10, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 9 failed")
                    raise
                logger.info("Database migrated from schema version 9 to 10")
                row = (10,)
            if row and row[0] == 10:
                connection.execute("BEGIN TRANSACTION")
                try:
                    _create_statement_balances_table(connection)
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [11, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 10 failed")
                    raise
                logger.info("Database migrated from schema version 10 to 11")
                row = (11,)
            if row and row[0] == 11:
                connection.execute("BEGIN TRANSACTION")
                try:
                    _create_earnings_dividends_tables(connection)
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [12, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 11 failed")
                    raise
                logger.info("Database migrated from schema version 11 to 12")
                row = (12,)
            if row and row[0] == 12:
                connection.execute("BEGIN TRANSACTION")
                try:
                    _create_financial_snapshots_table(connection)
                    connection.execute(
                        "UPDATE schema_metadata SET schema_version = ? WHERE component = ?",
                        [DATABASE_SCHEMA_VERSION, SCHEMA_COMPONENT],
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    logger.exception("Database migration from version 12 failed")
                    raise
                logger.info("Database migrated from schema version 12 to %d", DATABASE_SCHEMA_VERSION)
                _create_trade_events_view(connection)
                return False
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
        _create_trade_events_view(connection)
        logger.info("Database schema creation complete")
        return True
