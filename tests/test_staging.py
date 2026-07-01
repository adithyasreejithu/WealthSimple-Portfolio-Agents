import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import database
import staging
import ticker_pipeline


class StagingTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def _ticker(self, symbol, exchange, currency, name):
        return int(database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, ?, ?, ?, 'stock') RETURNING ticker_id
            """, [symbol, exchange, currency, name]
        ).fetchone()[0])

    def test_fx_trade_uses_us_and_non_fx_trade_uses_canadian_evidence(self):
        us_id = self._ticker("AAPL", "NASDAQ", "USD", "Apple Inc.")
        cad_id = self._ticker("VFV.TO", "TSX", "CAD", "Vanguard ETF")
        batch = staging.create_batch(self.db_path)
        data = pd.DataFrame([
            {"date": "2025-01-01", "transaction": "BUY", "ticker_id": "AAPL", "fx_rate": "1.4", "description": "Apple Inc.: Bought"},
            {"date": "2025-01-02", "transaction": "BUY", "ticker_id": "VFV", "fx_rate": "", "description": "Vanguard ETF: Bought"},
        ])
        staged_file = staging.stage_dataframe(batch, "statement", None, 1, data, self.db_path)

        staging.resolve_batch(batch, self.db_path)

        rows = database.get_shared_connection(self.db_path).execute(
            "SELECT source_symbol, contains_fx_rate, inferred_listing_currency, ticker_id, resolution_method FROM staged_records WHERE staged_file_id = ? ORDER BY record_sequence",
            [staged_file],
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("AAPL", "Yes", "USD", us_id, "statement_fx"),
                ("VFV", "No", "CAD", cad_id, "statement_no_fx"),
            ],
        )

    def test_resolution_uses_ticker_table_and_ignores_symbol_history(self):
        ticker_id = self._ticker("SPLG", "NYSE", "USD", "State Street SPDR Portfolio S&P 500 ETF")
        other_ticker_id = self._ticker("SPYM", "NYSE", "USD", "State Street SPDR Portfolio S&P 500 ETF")
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO ticker_symbol_history (
                ticker_id, source_symbol, provider_symbol, currency, exchange,
                effective_to, reason, mapping_source, created_by
        ) VALUES (?, 'SPLG', 'SPYM', 'USD', 'NYSE ARCA', '2025-10-30',
                      'ticker rename', 'manual', 'test')
            """, [other_ticker_id]
        )
        batch = staging.create_batch(self.db_path)
        staged_file = staging.stage_dataframe(batch, "statement", None, 1, pd.DataFrame([{
            "date": "2025-04-01", "transaction": "BUY", "ticker_id": "SPLG",
            "fx_rate": "1.4", "description": "State Street ETF: Bought",
        }]), self.db_path)
        staging.resolve_batch(batch, self.db_path)

        resolved = connection.execute(
            "SELECT ticker_id, resolution_method FROM staged_records WHERE staged_file_id = ?",
            [staged_file],
        ).fetchone()
        self.assertEqual(resolved, (ticker_id, "statement_fx"))

    def test_missing_pandas_values_do_not_become_ticker_or_fx_evidence(self):
        batch = staging.create_batch(self.db_path)
        staged_file = staging.stage_dataframe(batch, "statement", None, 1, pd.DataFrame([{
            "date": "2025-04-01", "transaction": "DIV", "ticker_id": float("nan"),
            "fx_rate": float("nan"), "description": pd.NA,
        }]), self.db_path)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT source_symbol, fx_rate, contains_fx_rate, price_currency FROM staged_records WHERE staged_file_id = ?",
            [staged_file],
        ).fetchone()
        self.assertEqual(row, (None, None, "No", None))

    def test_first_seen_ticker_is_enriched_once_and_persists_fx_classification(self):
        connection = database.get_shared_connection(self.db_path)

        def insert_metadata(hints, db_path, require_all=False):
            hint = hints[0]
            connection.execute(
                """
                INSERT INTO tickers (
                    ticker_symbol, exchange, currency, security_name, security_type
                ) VALUES (?, 'NASDAQ', ?, ?, 'stock')
                """,
                [hint.symbol, hint.currency, hint.name],
            )
            return {}

        with patch.object(ticker_pipeline, "ensure_tickers", side_effect=insert_metadata) as fetch:
            first = ticker_pipeline.resolve_or_enrich_ticker(
                "NBIS", True, "Nebius Group N.V.", "statement", self.db_path
            )
            second = ticker_pipeline.resolve_or_enrich_ticker(
                "NBIS", True, "Nebius Group N.V.", "statement", self.db_path
            )

        self.assertIsNotNone(first.ticker_id)
        self.assertEqual(second.ticker_id, first.ticker_id)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(
            connection.execute(
                "SELECT ticker_symbol, currency, contains_fx_rate FROM tickers WHERE ticker_id = ?",
                [first.ticker_id],
            ).fetchone(),
            ("NBIS", "USD", "Yes"),
        )

    def test_dividend_and_export_have_no_listing_evidence(self):
        batch = staging.create_batch(self.db_path)
        statement_file = staging.stage_dataframe(batch, "statement", None, 1, pd.DataFrame([{
            "date": "2025-04-01", "transaction": "DIV", "ticker_id": "AAPL",
            "fx_rate": None, "description": "Apple Inc.: Dividend",
        }]), self.db_path)
        export_file = staging.stage_dataframe(batch, "export", None, 2, pd.DataFrame([{
            "transaction_date": "2025-04-01", "activity_type": "Dividend",
            "symbol": "AAPL", "name": "Apple Inc.", "currency": "CAD",
        }]), self.db_path)
        rows = database.get_shared_connection(self.db_path).execute(
            """SELECT staged_file_id, inferred_listing_currency, listing_evidence
               FROM staged_records ORDER BY staged_file_id"""
        ).fetchall()
        self.assertEqual(rows, [(statement_file, None, None), (export_file, None, None)])


if __name__ == "__main__":
    unittest.main()
