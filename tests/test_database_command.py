import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

import database
from database_command import (
    ensure_tickers,
    get_email_checkpoint,
    normalize_ticker_dataframe,
    update_email_checkpoint,
    upload_email_transactions,
    upload_financial_snapshots,
    upload_portfolio_classifications,
    upload_statement_transactions,
    reconcile_email_transactions,
    reconcile_statement_activities,
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

    def test_upload_statement_transactions_records_balance_regardless_of_ticker_resolution(self):
        """
        A statement line's trailing balance used to only survive ingestion
        when the line had no resolved ticker (cash_transactions.balance);
        BUY/SELL/DIV rows tied to a security went to `transactions`, which
        has no `balance` column, silently discarding it. This exercises the
        real write path (not a direct table insert) to confirm every line's
        balance now lands in `statement_balances`, in original statement
        order, regardless of which branch (cash vs. ticker) the transaction
        itself took.
        """
        connection = database.get_shared_connection(self.db_path)
        ticker_id = connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock')
            RETURNING ticker_id
            """
        ).fetchone()[0]

        data = pd.DataFrame(
            [
                {
                    "date": date(2026, 5, 14), "transaction": "FPLINT", "ticker_id": None,
                    "quantity": None, "execDate": date(2026, 5, 14), "fx_rate": None,
                    "debit": "0.00", "credit": "0.01", "balance": "21.73",
                },
                {
                    "date": date(2026, 5, 21), "transaction": "BUY", "ticker_id": ticker_id,
                    "quantity": "1", "execDate": date(2026, 5, 21), "fx_rate": None,
                    "debit": "14.82", "credit": "0.00", "balance": "0.00",
                },
            ]
        )

        upload_statement_transactions(data, self.db_path)

        rows = connection.execute(
            "SELECT transaction_date, transaction_type, balance "
            "FROM statement_balances ORDER BY statement_balance_id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                (date(2026, 5, 14), "FPLINT", Decimal("21.73")),
                (date(2026, 5, 21), "BUY", Decimal("0.00")),
            ],
        )

        # transactions still has no balance column at all; this is the gap
        # statement_balances exists to close.
        transactions_columns = {
            row[0] for row in connection.execute("DESCRIBE transactions").fetchall()
        }
        self.assertNotIn("balance", transactions_columns)

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

    def test_interac_deposit_is_not_applicable_not_pending(self):
        data = pd.DataFrame([{
            "account": "TFSA", "transaction": "Deposit", "ticker_id": 0,
            "ticker": "EMAIL", "quantity": "", "avg_price": "",
            "total_cost": "", "debit": "225.00", "date": date(2024, 10, 23),
            "price_currency": "", "source_message_id": "",
            "received_at": date(2024, 10, 23),
        }])

        self.assertEqual(upload_email_transactions(data, self.db_path), 1)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT source_symbol, ticker_resolution_status, reconciliation_status "
            "FROM email_transactions"
        ).fetchone()
        self.assertEqual(row, ("EMAIL", "not_applicable", "not_applicable"))

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

    def _insert_activity_import(self, suffix="1"):
        return int(
            self.connection.execute(
                """
                INSERT INTO activity_imports (source_file, file_hash, status)
                VALUES (?, ?, 'succeeded') RETURNING import_id
                """,
                [f"file-{suffix}", f"hash-{suffix}"],
            ).fetchone()[0]
        )

    def _insert_trade_activity(
        self,
        ticker_id,
        import_id,
        fingerprint,
        *,
        transaction_date,
        subtype="BUY",
        quantity,
        net_cash_amount,
        activity_type="Trade",
        currency="CAD",
    ):
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity,
                net_cash_amount, row_fingerprint, duplicate_ordinal,
                first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', ?, ?, ?, 'LONG', ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [
                transaction_date, activity_type, subtype, subtype, ticker_id,
                currency, quantity, net_cash_amount, fingerprint, import_id, import_id,
            ],
        )

    def test_statement_row_exactly_matching_activities_is_superseded(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 1, NULL)""",
            [date(2025, 4, 2), ticker_id],
        )
        import_id = self._insert_activity_import()
        self._insert_trade_activity(
            ticker_id, import_id, "fp-1",
            transaction_date=date(2025, 4, 2), quantity=1, net_cash_amount=-100,
        )

        self.assertEqual(reconcile_statement_activities(self.db_path), 1)

        transaction_status = connection.execute(
            "SELECT superseded_by_activity_id FROM transactions"
        ).fetchone()[0]
        self.assertIsNotNone(transaction_status)

        # The statement row had NULL debit (a statement-extraction gap); the
        # activities row's cost is what survives into the unified event view.
        events = connection.execute(
            "SELECT source, amount_cad FROM v_trade_events WHERE ticker_id = ?", [ticker_id]
        ).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "activities")
        self.assertEqual(events[0][1], Decimal("100.0000"))

    def test_statement_activities_match_within_quantity_and_date_tolerance(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('DRAM', 'NYSE', 'USD', 'Dram Corp', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        # Statement date is 4 days after the activities date (within the
        # widened 5-day window) and quantity is off by 0.3% (within 0.5%).
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 1.003, 90.80)""",
            [date(2025, 7, 13), ticker_id],
        )
        import_id = self._insert_activity_import()
        self._insert_trade_activity(
            ticker_id, import_id, "fp-2",
            transaction_date=date(2025, 7, 9), quantity=1.000, net_cash_amount=-90.80,
        )

        self.assertEqual(reconcile_statement_activities(self.db_path), 1)
        linked = connection.execute(
            "SELECT superseded_by_activity_id FROM transactions"
        ).fetchone()[0]
        self.assertIsNotNone(linked)

    def test_statement_activities_reconciliation_is_idempotent(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 1, 100)""",
            [date(2025, 4, 2), ticker_id],
        )
        import_id = self._insert_activity_import()
        self._insert_trade_activity(
            ticker_id, import_id, "fp-3",
            transaction_date=date(2025, 4, 2), quantity=1, net_cash_amount=-100,
        )

        first = reconcile_statement_activities(self.db_path)
        link_after_first = connection.execute(
            "SELECT superseded_by_activity_id FROM transactions"
        ).fetchone()[0]
        second = reconcile_statement_activities(self.db_path)
        link_after_second = connection.execute(
            "SELECT superseded_by_activity_id FROM transactions"
        ).fetchone()[0]

        self.assertEqual(first, second)
        self.assertEqual(link_after_first, link_after_second)

    def test_duplicate_same_day_trades_pair_off_one_to_one(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('PZA', 'TSX', 'CAD', 'Pizza Pizza', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        for _ in range(2):
            connection.execute(
                """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
                   VALUES (?, 'BUY', ?, 2, 26.26)""",
                [date(2023, 7, 25), ticker_id],
            )
        import_id = self._insert_activity_import()
        for ordinal, fingerprint in enumerate(("fp-dup-a", "fp-dup-b")):
            self._insert_trade_activity(
                ticker_id, import_id, fingerprint,
                transaction_date=date(2023, 7, 25), quantity=2, net_cash_amount=-26.26,
            )

        self.assertEqual(reconcile_statement_activities(self.db_path), 2)
        linked_activity_ids = [
            row[0]
            for row in connection.execute(
                "SELECT superseded_by_activity_id FROM transactions ORDER BY transaction_id"
            ).fetchall()
        ]
        self.assertEqual(len(linked_activity_ids), 2)
        self.assertEqual(len(set(linked_activity_ids)), 2)
        self.assertNotIn(None, linked_activity_ids)

    def test_email_trade_matches_activities_before_statement(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('SPYM', 'BATS', 'USD', 'SPYM ETF', 'etf') RETURNING ticker_id"""
        ).fetchone()[0]
        # A statement row on the same day is present but unrelated to the DRIP;
        # the activities Trade row for the DRIP itself should win.
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 0.0025, 0.28)""",
            [date(2025, 10, 2), ticker_id],
        )
        import_id = self._insert_activity_import()
        self._insert_trade_activity(
            ticker_id, import_id, "fp-4",
            transaction_date=date(2025, 10, 2), quantity=0.0025, net_cash_amount=-0.28,
        )
        # This makes the statement row NOT superseded (different date/qty so
        # it survives on its own) while the email row should still prefer
        # the activities row over the leftover statement row.
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Fractional Buy', ?, 0.0025, 0.28, ?, 'SPYM', 'USD', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 10, 1)],
        )

        reconcile_statement_activities(self.db_path)
        self.assertEqual(reconcile_email_transactions(self.db_path), 1)

        status = connection.execute(
            "SELECT reconciliation_status, matched_activity_id, matched_transaction_id "
            "FROM email_transactions"
        ).fetchone()
        self.assertEqual(status[0], "superseded")
        self.assertIsNotNone(status[1])
        self.assertIsNone(status[2])

    def test_email_quantity_within_relative_tolerance_matches(self):
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('L', 'TSX', 'CAD', 'Loblaw', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 1.0, 100)""",
            [date(2025, 12, 31), ticker_id],
        )
        # 1.5% off from the statement quantity, within the 2% email tolerance.
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Fractional Buy', ?, 1.015, 100, ?, 'L', 'CAD', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 12, 31)],
        )

        self.assertEqual(reconcile_email_transactions(self.db_path), 1)
        status = connection.execute(
            "SELECT reconciliation_status, matched_transaction_id FROM email_transactions"
        ).fetchone()
        self.assertEqual(status[0], "superseded")
        self.assertIsNotNone(status[1])

    def test_email_reconciliation_rematches_after_statement_superseded(self):
        """A rerun after upstream data changes should stay idempotent.

        The email row is first matched to the live statement row; once
        activities import makes that statement row superseded, rerunning
        both passes should relink the email row to the activities row
        instead of leaving a dangling `matched_transaction_id`.
        """
        connection = database.get_shared_connection(self.db_path)
        self.connection = connection
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
               VALUES (?, 'BUY', ?, 1, 100)""",
            [date(2025, 4, 2), ticker_id],
        )
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Market Buy', ?, 1, 100, ?, 'AAPL', 'USD', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 4, 2)],
        )
        reconcile_email_transactions(self.db_path)
        first_match = connection.execute(
            "SELECT matched_transaction_id, matched_activity_id FROM email_transactions"
        ).fetchone()
        self.assertIsNotNone(first_match[0])
        self.assertIsNone(first_match[1])

        # Now an activities row for the same trade arrives later.
        import_id = self._insert_activity_import()
        self._insert_trade_activity(
            ticker_id, import_id, "fp-5",
            transaction_date=date(2025, 4, 2), quantity=1, net_cash_amount=-100,
        )
        reconcile_statement_activities(self.db_path)
        reconcile_email_transactions(self.db_path)

        second_match = connection.execute(
            "SELECT reconciliation_status, matched_transaction_id, matched_activity_id "
            "FROM email_transactions"
        ).fetchone()
        self.assertEqual(second_match[0], "superseded")
        self.assertIsNone(second_match[1])
        self.assertIsNotNone(second_match[2])

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

    def _holding(self, ticker, exchange="NASDAQ"):
        return {
            "ticker": ticker, "company_name": f"{ticker} Inc.", "primary_group": "Quality",
            "secondary_tags": ["Dividend"], "confidence": "high", "reasoning": "Strong fundamentals",
            "evidence_used": ["sector"], "missing_data": [], "review_needed": False,
            "fields": {"exchange": exchange, "sector": "Technology"},
            "field_provenance": {"sector": "database"},
            "enrichment": {"mode": "identity", "attempted_fields": [], "populated_fields": [], "errors": []},
        }

    def _write_classification_json(self, holdings, generated_at="2026-01-01T00:00:00+00:00"):
        path = Path(self.temp_dir.name) / "portfolio-classification.json"
        path.write_text(json.dumps({
            "schema_version": "1.0", "generated_at": generated_at,
            "workflow": "classify-my-portfolio", "database_mode": "read_only",
            "summary": {}, "holdings": holdings,
        }))
        return path

    def test_classification_sync_inserts_row_linked_to_ticker_id(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        path = self._write_classification_json([self._holding("AAPL")])

        written = upload_portfolio_classifications(path, self.db_path)

        row = connection.execute(
            "SELECT ticker_id, primary_group, review_needed FROM portfolio_classifications"
        ).fetchone()
        self.assertEqual(written, 1)
        self.assertEqual(row, (ticker_id, "Quality", False))

    def test_classification_sync_fully_replaces_prior_rows(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_ids = {}
        for symbol in ("AAPL", "MSFT"):
            ticker_ids[symbol] = connection.execute(
                """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
                   VALUES (?, 'NASDAQ', 'USD', ?, 'stock') RETURNING ticker_id""",
                [symbol, symbol],
            ).fetchone()[0]

        upload_portfolio_classifications(
            self._write_classification_json([self._holding("AAPL"), self._holding("MSFT")]),
            self.db_path,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM portfolio_classifications").fetchone()[0], 2
        )

        written = upload_portfolio_classifications(
            self._write_classification_json([self._holding("AAPL")]), self.db_path
        )

        remaining = connection.execute("SELECT ticker_id FROM portfolio_classifications").fetchall()
        self.assertEqual(written, 1)
        self.assertEqual(remaining, [(ticker_ids["AAPL"],)])

    def test_classification_sync_raises_for_unresolved_ticker(self):
        path = self._write_classification_json([self._holding("UNKNOWN")])

        with self.assertRaises(ValueError) as raised:
            upload_portfolio_classifications(path, self.db_path)

        self.assertIn("UNKNOWN", str(raised.exception))

    def test_classification_sync_raises_for_ambiguous_ticker_symbol(self):
        """Two tickers rows sharing a symbol (e.g. dual-listed) must not let
        the sync silently pick one -- match-by-symbol-alone (see
        upload_portfolio_classifications' docstring) requires exactly one row."""
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('DUPE', 'NYSE', 'USD', 'Dupe Co', 'stock')"""
        )
        connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('DUPE', 'TSX', 'CAD', 'Dupe Co CA', 'stock')"""
        )
        path = self._write_classification_json([self._holding("DUPE")])

        with self.assertRaises(ValueError) as raised:
            upload_portfolio_classifications(path, self.db_path)

        self.assertIn("DUPE", str(raised.exception))

    def test_classification_sync_rolls_back_fully_on_an_unresolved_ticker(self):
        """A holding the sync cannot resolve must not leave the table
        partially cleared -- the whole sync rolls back together rather than
        committing a table missing every classification after the DELETE."""
        connection = database.get_shared_connection(self.db_path)
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        upload_portfolio_classifications(
            self._write_classification_json([self._holding("AAPL")]), self.db_path
        )

        bad_path = self._write_classification_json([self._holding("AAPL"), self._holding("UNKNOWN")])
        with self.assertRaises(ValueError):
            upload_portfolio_classifications(bad_path, self.db_path)

        remaining = connection.execute("SELECT ticker_id FROM portfolio_classifications").fetchall()
        self.assertEqual(remaining, [(ticker_id,)])

    def _declare(self, connection, ticker_id, declared_at, status="wishlist"):
        connection.execute(
            "INSERT INTO security_status (ticker_id, declared_status, rationale, declared_at, declared_by) "
            "VALUES (?, ?, 'test', ?, 'test')",
            [ticker_id, status, declared_at],
        )

    def test_stale_subset_export_older_than_a_declaration_is_refused(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_ids = {}
        for symbol in ("AAPL", "MP"):
            ticker_ids[symbol] = connection.execute(
                """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
                   VALUES (?, 'NASDAQ', 'USD', ?, 'stock') RETURNING ticker_id""",
                [symbol, symbol],
            ).fetchone()[0]
        upload_portfolio_classifications(
            self._write_classification_json(
                [self._holding("AAPL"), self._holding("MP")],
                generated_at="2026-01-01T00:00:00+00:00",
            ),
            self.db_path,
        )
        self._declare(connection, ticker_ids["MP"], datetime(2026, 1, 5))

        stale_path = self._write_classification_json(
            [self._holding("AAPL")], generated_at="2026-01-02T00:00:00+00:00"
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            upload_portfolio_classifications(stale_path, self.db_path)

        # Refused sync must not have touched the table.
        remaining = {row[0] for row in connection.execute(
            "SELECT ticker_id FROM portfolio_classifications"
        ).fetchall()}
        self.assertEqual(remaining, set(ticker_ids.values()))

    def test_subset_export_newer_than_the_declaration_is_accepted(self):
        """A wishlist declaration can legitimately be removed -- a subset
        export generated *after* that removal must stay syncable."""
        connection = database.get_shared_connection(self.db_path)
        ticker_ids = {}
        for symbol in ("AAPL", "MP"):
            ticker_ids[symbol] = connection.execute(
                """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
                   VALUES (?, 'NASDAQ', 'USD', ?, 'stock') RETURNING ticker_id""",
                [symbol, symbol],
            ).fetchone()[0]
        upload_portfolio_classifications(
            self._write_classification_json(
                [self._holding("AAPL"), self._holding("MP")],
                generated_at="2026-01-01T00:00:00+00:00",
            ),
            self.db_path,
        )
        self._declare(connection, ticker_ids["MP"], datetime(2026, 1, 5))

        newer_path = self._write_classification_json(
            [self._holding("AAPL")], generated_at="2026-01-06T00:00:00+00:00"
        )
        written = upload_portfolio_classifications(newer_path, self.db_path)

        self.assertEqual(written, 1)
        remaining = connection.execute("SELECT ticker_id FROM portfolio_classifications").fetchall()
        self.assertEqual(remaining, [(ticker_ids["AAPL"],)])

    def test_full_export_is_accepted_even_if_older_than_a_declaration(self):
        """Not a strict subset -- nothing is at risk of being dropped, so the
        staleness guard must not fire regardless of timestamps."""
        connection = database.get_shared_connection(self.db_path)
        ticker_ids = {}
        for symbol in ("AAPL", "MP"):
            ticker_ids[symbol] = connection.execute(
                """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
                   VALUES (?, 'NASDAQ', 'USD', ?, 'stock') RETURNING ticker_id""",
                [symbol, symbol],
            ).fetchone()[0]
        upload_portfolio_classifications(
            self._write_classification_json(
                [self._holding("AAPL"), self._holding("MP")],
                generated_at="2026-01-01T00:00:00+00:00",
            ),
            self.db_path,
        )
        self._declare(connection, ticker_ids["MP"], datetime(2026, 1, 5))

        full_path = self._write_classification_json(
            [self._holding("AAPL"), self._holding("MP")],
            generated_at="2026-01-02T00:00:00+00:00",
        )
        written = upload_portfolio_classifications(full_path, self.db_path)
        self.assertEqual(written, 2)


class FinancialSnapshotsUploadTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        connection = database.get_shared_connection(self.db_path)
        self.ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('NVDA', 'NASDAQ', 'USD', 'NVIDIA Corporation', 'stock') RETURNING ticker_id""",
        ).fetchone()[0]
        self.ticker_ids = {"NVDA": self.ticker_id}

    def _row(self, **overrides):
        row = {
            "Ticker": "NVDA", "ProviderSymbol": "NVDA", "PeriodEndDate": "2026-03-31",
            "Revenue": 1000.0, "NetIncome": 200.0, "Eps": 1.5, "GrossMargin": 0.4,
            "OperatingMargin": 0.3, "DebtToEquity": 2.0, "CurrentRatio": 1.8,
            "FreeCashFlow": 150.0, "Extra": {"income_statement": {"Research Development": 50.0}},
        }
        row.update(overrides)
        return row

    def test_upsert_inserts_new_snapshot_row(self):
        written = upload_financial_snapshots(pd.DataFrame([self._row()]), self.ticker_ids, self.db_path)

        self.assertEqual(written, 1)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT revenue, net_income, eps, gross_margin, operating_margin, "
            "debt_to_equity, current_ratio, free_cash_flow, extra FROM financial_snapshots"
        ).fetchone()
        self.assertEqual(row[0], Decimal("1000.00"))
        self.assertEqual(row[1], Decimal("200.00"))
        self.assertAlmostEqual(float(row[2]), 1.5)
        self.assertAlmostEqual(float(row[3]), 0.4)
        self.assertAlmostEqual(float(row[4]), 0.3)
        self.assertAlmostEqual(float(row[5]), 2.0)
        self.assertAlmostEqual(float(row[6]), 1.8)
        self.assertEqual(row[7], Decimal("150.00"))
        self.assertEqual(json.loads(row[8]), {"income_statement": {"Research Development": 50.0}})

    def test_reupload_same_period_updates_in_place_simulating_restatement(self):
        upload_financial_snapshots(pd.DataFrame([self._row()]), self.ticker_ids, self.db_path)

        written = upload_financial_snapshots(
            pd.DataFrame([self._row(Revenue=1100.0, NetIncome=250.0)]), self.ticker_ids, self.db_path
        )

        self.assertEqual(written, 1)
        connection = database.get_shared_connection(self.db_path)
        count = connection.execute("SELECT COUNT(*) FROM financial_snapshots").fetchone()[0]
        revenue, net_income = connection.execute(
            "SELECT revenue, net_income FROM financial_snapshots"
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(revenue, Decimal("1100.00"))
        self.assertEqual(net_income, Decimal("250.00"))

    def test_missing_ticker_id_mapping_raises(self):
        with self.assertRaises(ValueError):
            upload_financial_snapshots(pd.DataFrame([self._row()]), {}, self.db_path)

    def test_missing_period_end_date_raises(self):
        with self.assertRaises(ValueError):
            upload_financial_snapshots(
                pd.DataFrame([self._row(PeriodEndDate=None)]), self.ticker_ids, self.db_path
            )

    def test_nullable_named_columns_accept_none(self):
        written = upload_financial_snapshots(
            pd.DataFrame([self._row(
                Revenue=None, NetIncome=None, Eps=None, GrossMargin=None,
                OperatingMargin=None, DebtToEquity=None, CurrentRatio=None,
                FreeCashFlow=None, Extra={},
            )]),
            self.ticker_ids, self.db_path,
        )

        self.assertEqual(written, 1)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT revenue, net_income, current_ratio FROM financial_snapshots"
        ).fetchone()
        self.assertEqual(row, (None, None, None))


if __name__ == "__main__":
    unittest.main()
