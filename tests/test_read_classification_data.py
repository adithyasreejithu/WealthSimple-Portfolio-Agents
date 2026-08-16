from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "read-portfolio-classification-data" / "scripts")
)

import duckdb

import database
import position_engine
import read_classification_data as rcd


class ReadWishlistClassificationDataTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        database.close_connection()

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def _insert_ticker(self, connection, symbol: str, security_type: str = "EQUITY") -> int:
        connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES (?, 'NASDAQ', 'USD', ?, ?)",
            [symbol, f"{symbol} Inc", security_type],
        )
        return connection.execute(
            "SELECT ticker_id FROM tickers WHERE ticker_symbol = ?", [symbol]
        ).fetchone()[0]

    def _declare_wishlist(self, connection, ticker_id: int, status: str = "wishlist") -> None:
        connection.execute(
            "INSERT INTO security_status (ticker_id, declared_status, rationale, declared_at, declared_by) "
            "VALUES (?, ?, 'test', CURRENT_TIMESTAMP, 'test')",
            [ticker_id, status],
        )

    def test_returns_only_research_status_tickers(self):
        connection = duckdb.connect(str(self.db_path))
        wishlist_id = self._insert_ticker(connection, "MP")
        self._declare_wishlist(connection, wishlist_id, "wishlist")
        avoid_id = self._insert_ticker(connection, "BAD")
        self._declare_wishlist(connection, avoid_id, "avoid")
        self._insert_ticker(connection, "NOSTATUS")  # no security_status row at all
        position_engine.recompute_positions(connection)
        connection.close()

        records = rcd.read_wishlist_classification_data(self.db_path)
        tickers = {record["ticker"] for record in records}
        self.assertEqual(tickers, {"MP"})

    def test_excludes_a_ticker_that_is_both_wishlist_declared_and_owned(self):
        connection = duckdb.connect(str(self.db_path))
        ticker_id = self._insert_ticker(connection, "AAPL")
        self._declare_wishlist(connection, ticker_id, "wishlist")
        connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, execution_date, debit) "
            "VALUES (?, 'BUY', ?, 2, ?, 200)",
            [date(2025, 1, 2), ticker_id, date(2025, 1, 2)],
        )
        position_engine.recompute_positions(connection)
        connection.close()

        records = rcd.read_wishlist_classification_data(self.db_path)
        self.assertEqual(records, [])

    def test_sets_not_owned_synthetic_defaults_and_ownership_status(self):
        connection = duckdb.connect(str(self.db_path))
        ticker_id = self._insert_ticker(connection, "MP")
        self._declare_wishlist(connection, ticker_id)
        position_engine.recompute_positions(connection)
        connection.close()

        record = rcd.read_wishlist_classification_data(self.db_path)[0]
        self.assertEqual(record["ownership_status"], "wishlist")
        self.assertEqual(record["quantity"], 0)
        self.assertIsNone(record["cost_basis"])
        self.assertIsNone(record["position_market_value"])
        self.assertIsNone(record["current_weight_percent"])
        self.assertIsNone(record["unrealized_gain_loss_percent"])
        self.assertFalse(record["has_provisional_activity"])
        self.assertEqual(record["data_quality_flags"], [])
        self.assertEqual(record["number_of_buys"], 0)
        self.assertEqual(record["number_of_sells"], 0)

    def test_owned_records_are_tagged_owned(self):
        connection = duckdb.connect(str(self.db_path))
        ticker_id = self._insert_ticker(connection, "AAPL")
        connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, execution_date, debit) "
            "VALUES (?, 'BUY', ?, 2, ?, 200)",
            [date(2025, 1, 2), ticker_id, date(2025, 1, 2)],
        )
        position_engine.recompute_positions(connection)
        connection.close()

        records = rcd.read_classification_data(self.db_path)
        self.assertEqual(records[0]["ownership_status"], "owned")


if __name__ == "__main__":
    unittest.main()
