import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import database
from database_command import (
    ensure_tickers,
    get_email_checkpoint,
    normalize_ticker_dataframe,
    update_email_checkpoint,
    upload_email_transactions,
    reconcile_email_transactions,
    upload_security_metadata,
)


class DatabaseCommandTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

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

    def test_market_enrichment_preserves_referenced_ticker_and_mapping(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_id = int(connection.execute(
            """INSERT INTO tickers (
                   ticker_symbol, exchange, currency, financial_currency,
                   security_name, security_type
               ) VALUES ('AAPL', 'NASDAQ', 'USD', NULL, 'Original Apple', 'stock')
               RETURNING ticker_id"""
        ).fetchone()[0])
        connection.execute(
            """INSERT INTO ticker_provider_mappings (
                   ticker_id, provider, provider_symbol, verification_status, mapping_source
               ) VALUES (?, 'yahoo', 'AAPL-MANUAL', 'verified', 'manual')""",
            [ticker_id],
        )
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, transaction_date,
                   source_symbol, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Market Buy', ?, 1, ?, 'AAPL', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 4, 2)],
        )
        metadata = pd.DataFrame([{
            "ticker": "AAPL", "provider_symbol": "AAPL", "exchange": "NMS",
            "currency": "USD", "financial_currency": "USD",
            "company_name": "Apple Inc.", "sector": "Technology",
            "industry": "Consumer Electronics",
        }])

        upload_security_metadata(
            metadata, pd.DataFrame(), self.db_path, ticker_ids={"AAPL": ticker_id}
        )

        identity = connection.execute(
            """SELECT ticker_symbol, exchange, currency, financial_currency,
                      security_name, security_type
               FROM tickers WHERE ticker_id = ?""",
            [ticker_id],
        ).fetchone()
        mapping = connection.execute(
            """SELECT provider_symbol, mapping_source
               FROM ticker_provider_mappings WHERE ticker_id = ? AND provider = 'yahoo'""",
            [ticker_id],
        ).fetchone()
        details = connection.execute(
            "SELECT sector, industry FROM stock_details WHERE ticker_id = ?",
            [ticker_id],
        ).fetchone()

        self.assertEqual(identity, ("AAPL", "NASDAQ", "USD", None, "Original Apple", "stock"))
        self.assertEqual(mapping, ("AAPL-MANUAL", "manual"))
        self.assertEqual(details, ("Technology", "Consumer Electronics"))

    def test_market_enrichment_skips_unrequested_provider_metadata(self):
        metadata = pd.DataFrame([{
            "ticker": "OTHER", "provider_symbol": "OTHER", "exchange": "NYSE",
            "currency": "USD", "company_name": "Other Corp",
        }])

        with self.assertLogs("database_command", level="WARNING") as captured:
            upload_security_metadata(
                metadata, pd.DataFrame(), self.db_path, ticker_ids={"AAPL": 1}
            )

        count = database.get_shared_connection(self.db_path).execute(
            "SELECT COUNT(*) FROM tickers"
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertTrue(any("Skipping unexpected yfinance metadata" in line for line in captured.output))

    def test_email_checkpoint_round_trip_accumulates_count(self):
        update_email_checkpoint(date(2025, 4, 1), 2, self.db_path)
        update_email_checkpoint(datetime(2025, 4, 2, 15, 30), 3, self.db_path)

        checkpoint = get_email_checkpoint(self.db_path)
        count, checked_at = database.get_shared_connection(self.db_path).execute(
            "SELECT email_count, checked_through_at FROM email_checkpoints"
        ).fetchone()

        self.assertEqual(checkpoint, date(2025, 4, 2))
        self.assertEqual(count, 5)
        self.assertEqual(checked_at, datetime(2025, 4, 2, 15, 30))

    def test_email_upload_is_idempotent_by_message_and_keeps_pending_symbol(self):
        data = pd.DataFrame([{
            "account": "TFSA", "transaction": "Market Buy", "ticker_id": None,
            "ticker": "NEW", "quantity": "1", "avg_price": "10",
            "total_cost": "10", "debit": "", "date": date(2025, 4, 2),
            "price_currency": "CAD", "source_message_id": "message-1",
            "received_at": date(2025, 4, 2),
        }])

        self.assertEqual(upload_email_transactions(data, self.db_path), 1)
        self.assertEqual(upload_email_transactions(data, self.db_path), 0)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT source_symbol, price_currency, ticker_resolution_status FROM email_transactions"
        ).fetchone()
        self.assertEqual(row, ("NEW", "CAD", "pending"))

    def test_statement_match_supersedes_email_trade_once(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, execution_date)
               VALUES (?, 'BUY', ?, 1, ?)""", [date(2025, 4, 2), ticker_id, date(2025, 4, 2)]
        )
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Market Buy', ?, 1, ?, 'AAPL', 'USD', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 4, 2)],
        )

        self.assertEqual(reconcile_email_transactions(self.db_path), 1)
        self.assertEqual(reconcile_email_transactions(self.db_path), 0)
        status = connection.execute(
            "SELECT reconciliation_status, matched_transaction_id FROM email_transactions"
        ).fetchone()
        self.assertEqual(status[0], "superseded")
        self.assertIsNotNone(status[1])

    def test_ambiguous_near_date_statement_matches_require_review(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('DUAL', 'NASDAQ', 'USD', 'Dual Trade', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        for trade_date in (date(2025, 4, 2), date(2025, 4, 3)):
            connection.execute(
                """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity)
                   VALUES (?, 'BUY', ?, 1)""", [trade_date, ticker_id]
            )
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, transaction_date,
                   source_symbol, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Market Buy', ?, 1, ?, 'DUAL', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 4, 1)],
        )

        self.assertEqual(reconcile_email_transactions(self.db_path), 0)
        status = connection.execute(
            "SELECT reconciliation_status FROM email_transactions"
        ).fetchone()[0]
        self.assertEqual(status, "review_required")

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
