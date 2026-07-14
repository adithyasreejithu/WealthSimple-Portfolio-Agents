from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

# The classification workflow modules live beside the classify-portfolio skill,
# not in src/, so expose that scripts directory before importing them.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "classify-portfolio" / "scripts")
)

import duckdb

import classification_workflow as workflow
import database
import position_engine


class FakeTicker:
    def __init__(self, symbol, info=None, error=None):
        self.symbol = symbol
        self._info = info or {}
        self._error = error

    def get_info(self):
        if self._error:
            raise self._error
        return self._info


class ClassificationWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        database.close_connection()
        connection = duckdb.connect(str(self.db_path))
        connection.execute("INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple from database', 'EQUITY')")
        ticker_id = connection.execute("SELECT ticker_id FROM tickers").fetchone()[0]
        connection.execute("INSERT INTO stock_details VALUES (?, 'Technology', NULL)", [ticker_id])
        connection.execute("INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, verification_status) VALUES (?, 'yahoo', 'AAPL', 'verified')", [ticker_id])
        connection.execute("INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, execution_date, debit) VALUES (?, 'BUY', ?, 2, ?, 200)", [date(2025, 1, 2), ticker_id, date(2025, 1, 2)])
        connection.execute("INSERT INTO historical_records VALUES (?, ?, 120, 125, 119, 123, 123, 1000)", [ticker_id, date(2025, 1, 3)])
        # read_classification_data now reads position_snapshots (the average-cost
        # engine's output) rather than deriving positions live from transactions,
        # so the fixture must populate it the same way the real pipeline does.
        position_engine.recompute_positions(connection)
        connection.close()

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def test_read_is_byte_for_byte_read_only_and_derives_weight(self):
        before = self.db_path.read_bytes()
        records = workflow.read_classification_data(self.db_path)
        self.assertEqual(before, self.db_path.read_bytes())
        self.assertEqual(records[0]["ticker"], "AAPL")
        self.assertEqual(records[0]["position_market_value"], 246.0)
        self.assertEqual(records[0]["current_weight_percent"], 100.0)

    def test_confirmed_position_has_no_provisional_activity(self):
        record = workflow.read_classification_data(self.db_path)[0]
        self.assertEqual(record["provisional_quantity"], 0.0)
        self.assertIsInstance(record["data_quality_flags"], list)
        self.assertFalse(record["has_provisional_activity"])
        self.assertEqual(record["field_provenance"]["has_provisional_activity"], "derived")

    def test_email_sourced_trade_surfaces_as_provisional(self):
        connection = duckdb.connect(str(self.db_path))
        ticker_id = connection.execute("SELECT ticker_id FROM tickers").fetchone()[0]
        connection.execute(
            "INSERT INTO email_transactions (transaction_type, ticker_id, quantity, total_cost, transaction_date) "
            "VALUES ('BUY', ?, 1, 130, ?)",
            [ticker_id, date(2025, 2, 1)],
        )
        position_engine.recompute_positions(connection)
        connection.close()

        record = workflow.read_classification_data(self.db_path)[0]
        self.assertEqual(record["provisional_quantity"], 1.0)
        self.assertTrue(record["has_provisional_activity"])
        self.assertEqual(record["field_provenance"]["provisional_quantity"], "database")
        self.assertEqual(record["field_provenance"]["has_provisional_activity"], "derived")

    def test_stale_positions_raise_actionable_error(self):
        connection = duckdb.connect(str(self.db_path))
        ticker_id = connection.execute("SELECT ticker_id FROM tickers").fetchone()[0]
        connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, execution_date, debit) "
            "VALUES (?, 'BUY', ?, 1, ?, 100)",
            [date(2025, 2, 1), ticker_id, date(2025, 2, 1)],
        )
        connection.close()

        with self.assertRaisesRegex(RuntimeError, "recompute-positions"):
            workflow.read_classification_data(self.db_path)

    def test_only_missing_allowlisted_fields_are_requested(self):
        request = workflow.build_enrichment_requests(workflow.read_classification_data(self.db_path))[0]
        self.assertNotIn("company_name", request["fields"])
        self.assertNotIn("sector", request["fields"])
        self.assertIn("industry", request["fields"])
        self.assertIn("market_cap", request["fields"])
        self.assertNotIn("user_thesis", request["fields"])

    def test_database_value_wins_when_merging_enrichment(self):
        record = workflow.read_classification_data(self.db_path)[0]
        workflow.merge_enrichment([record], [{"ticker": "AAPL", "mode": "equity-classification", "attempted_fields": ["company_name", "industry"], "fields": {"company_name": "Yahoo name", "industry": "Consumer Electronics"}, "error": None}])
        self.assertEqual(record["company_name"], "Apple from database")
        self.assertEqual(record["industry"], "Consumer Electronics")
        self.assertEqual(record["field_provenance"]["company_name"], "database")
        self.assertEqual(record["field_provenance"]["industry"], "yfinance")

    def test_invalid_field_is_rejected(self):
        with self.assertRaises(ValueError):
            workflow.fetch_classification_data([{"ticker": "AAPL", "provider_symbol": "AAPL", "mode": "equity-classification", "fields": ["history"]}], ticker_factory=lambda symbol: FakeTicker(symbol))

    def test_enrichment_failure_is_captured_per_ticker(self):
        result = workflow.fetch_classification_data([{"ticker": "AAPL", "provider_symbol": "AAPL", "mode": "equity-classification", "fields": ["industry"]}], ticker_factory=lambda symbol: FakeTicker(symbol, error=TimeoutError("slow")))
        self.assertIn("TimeoutError", result[0]["error"])

    def test_complete_workflow_returns_schema_valid_json(self):
        def fetcher(requests):
            return [{"ticker": "AAPL", "mode": "equity-classification", "attempted_fields": list(requests[0]["fields"]), "fields": {"industry": "Consumer Electronics", "market_cap": 1_000_000}, "error": None}]

        payload = workflow.classify_portfolio(self.db_path, fetcher=fetcher)
        workflow.validate_output(payload)
        self.assertEqual(payload["database_mode"], "read_only")
        self.assertEqual(payload["summary"]["holding_count"], 1)
        self.assertEqual(payload["holdings"][0]["primary_group"], "Quality")


if __name__ == "__main__":
    unittest.main()
