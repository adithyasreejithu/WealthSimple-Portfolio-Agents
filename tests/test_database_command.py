import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

import database
from database_command import (
    ensure_tickers,
    get_email_checkpoint,
    normalize_ticker_dataframe,
    update_email_checkpoint,
)


class DatabaseCommandTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def test_first_seen_etf_is_enriched_and_normalized(self):
        calls = []

        def fetcher(tickers):
            calls.append(tickers)
            stocks = pd.DataFrame(
                columns=[
                    "ticker", "company_name", "asset", "exchange", "currency",
                    "sector", "industry",
                ]
            )
            etfs = pd.DataFrame(
                [
                    {
                        "ticker": "VFV",
                        "company_name": "Vanguard S&P 500 Index ETF",
                        "exchange": "Toronto Stock Exchange",
                        "currency": "CAD",
                        "fund_family": "Vanguard",
                        "asset": "Large Blend",
                    }
                ]
            )
            return stocks, etfs

        source = pd.DataFrame([{"ticker": "VFV", "quantity": "2"}])
        normalized = normalize_ticker_dataframe(
            source, "ticker", self.db_path, fetcher
        )
        connection = database.get_shared_connection(self.db_path)
        ticker = connection.execute(
            "SELECT ticker_symbol, exchange, security_type FROM tickers"
        ).fetchone()

        self.assertEqual(calls, [["VFV"]])
        self.assertEqual(ticker, ("VFV", "TORONTO STOCK EXCHANGE", "etf"))
        self.assertEqual(normalized.loc[0, "ticker_id"], 1)
        self.assertNotIn("ticker", normalized.columns)

        ensure_tickers(["VFV"], self.db_path, fetcher)
        self.assertEqual(calls, [["VFV"]])

    def test_email_checkpoint_round_trip_accumulates_count(self):
        update_email_checkpoint(date(2025, 4, 1), 2, self.db_path)
        update_email_checkpoint(date(2025, 4, 2), 3, self.db_path)

        checkpoint = get_email_checkpoint(self.db_path)
        count = database.get_shared_connection(self.db_path).execute(
            "SELECT email_count FROM email_checkpoints"
        ).fetchone()[0]

        self.assertEqual(checkpoint, date(2025, 4, 2))
        self.assertEqual(count, 5)

    def test_non_finite_optional_etf_metadata_is_stored_as_null(self):
        def fetcher(_tickers):
            return pd.DataFrame(), pd.DataFrame([{
                "ticker": "VFV", "provider_symbol": "VFV.TO",
                "company_name": "Vanguard S&P 500 Index ETF",
                "exchange": "Toronto Stock Exchange", "currency": "CAD",
                "financial_currency": "USD",
                "yield": float("nan"), "expense_ratio": pd.NA,
                "aum": float("inf"), "nav": 120.5,
            }])

        ensure_tickers([{"symbol": "VFV", "currency": "CAD"}], self.db_path, fetcher)
        connection = database.get_shared_connection(self.db_path)
        details = connection.execute(
            "SELECT yield, expense_ratio, aum, nav FROM etf_details"
        ).fetchone()
        mapping = connection.execute(
            "SELECT provider, provider_symbol, verification_status FROM ticker_provider_mappings"
        ).fetchone()

        self.assertEqual(details[:3], (None, None, None))
        self.assertEqual(float(details[3]), 120.5)
        self.assertEqual(mapping, ("yahoo", "VFV.TO", "verified"))
        currencies = connection.execute(
            "SELECT currency, financial_currency FROM tickers WHERE ticker_symbol = 'VFV'"
        ).fetchone()
        self.assertEqual(currencies, ("CAD", "USD"))

    def test_same_symbol_in_two_currencies_fetches_both_listings(self):
        received = []
        def fetcher(hints):
            received.extend((hint.symbol, hint.currency) for hint in hints)
            return pd.DataFrame([
                {"ticker": "ABC", "provider_symbol": "ABC.TO", "company_name": "ABC Canada", "asset": "EQUITY", "exchange": "TSX", "currency": "CAD"},
                {"ticker": "ABC", "provider_symbol": "ABC", "company_name": "ABC US", "asset": "EQUITY", "exchange": "NYSE", "currency": "USD"},
            ]), pd.DataFrame()

        ensure_tickers([
            {"symbol": "ABC", "currency": "CAD"},
            {"symbol": "ABC", "currency": "USD"},
        ], self.db_path, fetcher)

        self.assertEqual(set(received), {("ABC", "CAD"), ("ABC", "USD")})
        rows = database.get_shared_connection(self.db_path).execute(
            "SELECT currency FROM tickers WHERE ticker_symbol = 'ABC' ORDER BY currency"
        ).fetchall()
        self.assertEqual(rows, [("CAD",), ("USD",)])


if __name__ == "__main__":
    unittest.main()
