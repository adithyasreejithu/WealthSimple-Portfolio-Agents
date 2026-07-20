import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import duckdb

import config
import database
from database_test_main import DatabaseCreationMain


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"

    def test_database_config_is_shared(self):
        self.assertEqual(database.DATABASE_PATH, config.DATABASE_PATH)
        self.assertEqual(
            database.DATABASE_SCHEMA_VERSION,
            config.DATABASE_SCHEMA_VERSION,
        )

    def test_initialize_database_creates_complete_schema(self):
        created = database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)

        table_names = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                  AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        }

        self.assertTrue(created)
        self.assertEqual(table_names, set(database.REQUIRED_TABLES))
        self.assertTrue(database.is_database_active(connection))

    def test_repeated_initialization_is_idempotent_and_preserves_data(self):
        self.assertTrue(database.initialize_database(self.db_path))
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO tickers (
                ticker_symbol,
                exchange,
                currency,
                security_name,
                security_type
            )
            VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock')
            """
        )

        created = database.initialize_database(self.db_path)
        ticker_count = connection.execute(
            "SELECT COUNT(*) FROM tickers"
        ).fetchone()[0]

        self.assertFalse(created)
        self.assertEqual(ticker_count, 1)

    def test_version_seven_schema_is_upgraded_to_current_version(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 7 WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        )

        created = database.initialize_database(self.db_path)

        version = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        ).fetchone()[0]
        self.assertFalse(created)
        self.assertEqual(version, database.DATABASE_SCHEMA_VERSION)
        self.assertTrue(database.is_database_active(connection))

    def test_version_eight_schema_is_upgraded_and_adds_classifications_table(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute("DROP TABLE portfolio_classifications")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 8 WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        )

        created = database.initialize_database(self.db_path)

        version = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        ).fetchone()[0]
        table_exists = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'portfolio_classifications'"
        ).fetchone()[0]
        self.assertFalse(created)
        self.assertEqual(version, database.DATABASE_SCHEMA_VERSION)
        self.assertEqual(table_exists, 1)
        self.assertTrue(database.is_database_active(connection))

    def test_version_nine_schema_is_upgraded_to_position_engine_schema(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute("DROP VIEW v_trade_events")
        connection.execute("DROP TABLE position_ledger")
        connection.execute("DROP TABLE position_snapshots")
        connection.execute("DROP TABLE position_engine_meta")
        # The v9->v10 migration's ALTER TABLE ... ADD COLUMN IF NOT EXISTS is
        # idempotent, so a genuine v9 database (missing these columns) and
        # this simulated one (columns present, since dropping an
        # FK-referenced column requires dropping the FK first) both migrate
        # the same way.
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 9 WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        )

        created = database.initialize_database(self.db_path)

        version = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        ).fetchone()[0]
        self.assertFalse(created)
        self.assertEqual(version, database.DATABASE_SCHEMA_VERSION)
        self.assertTrue(database.is_database_active(connection))
        self.assertIn("position_ledger", database._get_table_names(connection))
        self.assertIn("position_snapshots", database._get_table_names(connection))
        self.assertIn("position_engine_meta", database._get_table_names(connection))
        transaction_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('transactions')").fetchall()
        }
        email_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('email_transactions')").fetchall()
        }
        self.assertIn("superseded_by_activity_id", transaction_columns)
        self.assertIn("matched_activity_id", email_columns)
        # The view must exist and be queryable (empty result on an empty DB).
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM v_trade_events").fetchone()[0], 0)

    def test_version_ten_schema_is_upgraded_and_adds_statement_balances_table(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute("DROP TABLE statement_balances")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 10 WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        )

        created = database.initialize_database(self.db_path)

        version = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE component = ?",
            [database.SCHEMA_COMPONENT],
        ).fetchone()[0]
        self.assertFalse(created)
        self.assertEqual(version, database.DATABASE_SCHEMA_VERSION)
        self.assertTrue(database.is_database_active(connection))
        self.assertIn("statement_balances", database._get_table_names(connection))
        connection.execute(
            "INSERT INTO statement_balances (transaction_date, transaction_type, balance) "
            "VALUES ('2026-01-01', 'BUY', 100.00)"
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM statement_balances").fetchone()[0], 1
        )

    def test_trade_events_view_is_recreated_on_every_startup(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute("CREATE OR REPLACE VIEW v_trade_events AS SELECT 1 AS stale_marker")

        database.initialize_database(self.db_path)

        columns = {
            row[0]
            for row in connection.execute("DESCRIBE v_trade_events").fetchall()
        }
        self.assertNotIn("stale_marker", columns)
        self.assertIn("event_type", columns)

    def test_tickers_owns_shared_listing_identity(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)

        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('tickers')").fetchall()
        }

        self.assertEqual(
            columns,
            {
                "ticker_id",
                "ticker_symbol",
                "exchange",
                "currency",
                "financial_currency",
                "contains_fx_rate",
                "security_name",
                "security_type",
            },
        )
        self.assertNotIn("pull_date", columns)

    def test_ticker_backed_tables_store_ticker_id_not_ticker_text(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)

        for table_name in (
            "stock_details",
            "etf_details",
            "transactions",
            "historical_records",
            "email_transactions",
            "activities",
        ):
            columns = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info('{table_name}')"
                ).fetchall()
            }
            self.assertIn("ticker_id", columns)
            self.assertNotIn("ticker", columns)
            self.assertNotIn("ticker_symbol", columns)

    def test_same_symbol_is_allowed_on_different_exchanges(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        values = [
            ("ABC", "TSX", "CAD", "ABC Canada", "stock"),
            ("ABC", "NYSE", "USD", "ABC United States", "stock"),
        ]
        connection.executemany(
            """
            INSERT INTO tickers (
                ticker_symbol,
                exchange,
                currency,
                security_name,
                security_type
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )

        with self.assertRaises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO tickers (
                    ticker_symbol,
                    exchange,
                    currency,
                    security_name,
                    security_type
                )
                VALUES ('ABC', 'TSX', 'CAD', 'Duplicate', 'stock')
                """
            )

        with self.assertRaises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO tickers (
                    ticker_symbol,
                    exchange,
                    currency,
                    security_name,
                    security_type
                )
                VALUES ('abc', 'TSX', 'CAD', 'Not normalized', 'stock')
                """
            )

    def test_detail_tables_only_contain_type_specific_fields(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)

        stock_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('stock_details')"
            ).fetchall()
        }
        etf_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('etf_details')"
            ).fetchall()
        }

        for columns in (stock_columns, etf_columns):
            self.assertNotIn("company_name", columns)
            self.assertNotIn("exchange", columns)
            self.assertNotIn("currency", columns)

    def test_incompatible_existing_schema_requires_migration(self):
        connection = duckdb.connect(str(self.db_path))
        connection.execute("CREATE TABLE legacy_table (id INTEGER)")
        connection.close()

        with self.assertRaisesRegex(RuntimeError, "migration is not implemented"):
            database.initialize_database(self.db_path)

    def test_version_two_database_migrates_without_losing_tickers(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """INSERT INTO tickers (
                ticker_id, ticker_symbol, exchange, currency, security_name, security_type
            ) VALUES (nextval('ticker_id_sequence'), 'AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock')"""
        )
        connection.execute("DROP TABLE ticker_provider_mappings")
        connection.execute("UPDATE schema_metadata SET schema_version = 2")

        self.assertFalse(database.initialize_database(self.db_path))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tickers").fetchone()[0], 1)
        self.assertIn("ticker_provider_mappings", database._get_table_names(connection))
        self.assertTrue(database.is_database_active(connection))

    def test_version_four_database_adds_currency_audit_columns(self):
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        connection.execute("UPDATE schema_metadata SET schema_version = 4")

        self.assertFalse(database.initialize_database(self.db_path))
        ticker_columns = {row[1] for row in connection.execute("PRAGMA table_info('tickers')").fetchall()}
        staged_columns = {row[1] for row in connection.execute("PRAGMA table_info('staged_records')").fetchall()}
        self.assertIn("financial_currency", ticker_columns)
        self.assertTrue({"inferred_listing_currency", "listing_evidence"}.issubset(staged_columns))
        self.assertIn("contains_fx_rate", staged_columns)
        self.assertIn("contains_fx_rate", ticker_columns)
        self.assertTrue(database.is_database_active(connection))

    def test_schema_creation_failure_rolls_back_all_tables(self):
        connection = duckdb.connect(str(self.db_path))

        class FailingConnection:
            def execute(self, query, parameters=None):
                if "CREATE TABLE tickers" in query:
                    raise RuntimeError("forced schema failure")
                if parameters is None:
                    return connection.execute(query)
                return connection.execute(query, parameters)

        with self.assertRaisesRegex(RuntimeError, "forced schema failure"):
            database._deploy_schema(FailingConnection())

        tables = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE'
            """
        ).fetchall()
        connection.close()

        self.assertEqual(tables, [])

    def test_shared_connection_rejects_a_second_database_path(self):
        database.get_shared_connection(self.db_path)
        second_path = Path(self.temp_dir.name) / "second.duckdb"

        with self.assertRaisesRegex(RuntimeError, "already open"):
            database.get_shared_connection(second_path)

    def test_temporary_main_creates_and_reports_database(self):
        output = StringIO()

        with redirect_stdout(output):
            created = DatabaseCreationMain(self.db_path).run()

        result = output.getvalue()
        self.assertTrue(created)
        self.assertIn("Status: created", result)
        self.assertIn("Schema active: True", result)
        self.assertIn("  - tickers", result)
        self.assertTrue(self.db_path.exists())

    def test_temporary_main_can_verify_existing_database(self):
        DatabaseCreationMain(self.db_path).run()
        output = StringIO()

        with redirect_stdout(output):
            created = DatabaseCreationMain(self.db_path).run()

        self.assertFalse(created)
        self.assertIn("Status: already active", output.getvalue())


if __name__ == "__main__":
    unittest.main()
