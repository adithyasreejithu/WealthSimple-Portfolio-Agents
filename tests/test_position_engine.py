import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import database
import position_engine
from database_command import reconcile_email_transactions, reconcile_statement_activities


class PositionEngineTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _ticker(self, symbol="AAPL", exchange="NASDAQ", currency="USD"):
        return int(
            self.connection.execute(
                """
                INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
                VALUES (?, ?, ?, ?, 'stock') RETURNING ticker_id
                """,
                [symbol, exchange, currency, f"{symbol} Inc."],
            ).fetchone()[0]
        )

    def _buy(self, ticker_id, transaction_date, quantity, debit, fx_rate=None):
        self.connection.execute(
            """
            INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit, fx_rate)
            VALUES (?, 'BUY', ?, ?, ?, ?)
            """,
            [transaction_date, ticker_id, quantity, debit, fx_rate],
        )

    def _sell(self, ticker_id, transaction_date, quantity, credit, fx_rate=None):
        self.connection.execute(
            """
            INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, credit, fx_rate)
            VALUES (?, 'SELL', ?, ?, ?, ?)
            """,
            [transaction_date, ticker_id, quantity, credit, fx_rate],
        )

    def _snapshot(self, ticker_id):
        row = self.connection.execute(
            """
            SELECT quantity, book_value_cad, book_value_mkt, realized_gain_cad,
                   provisional_quantity, data_quality_flags
            FROM position_snapshots WHERE ticker_id = ?
            """,
            [ticker_id],
        ).fetchone()
        return row

    def test_partial_sell_reduces_book_value_at_average_cost(self):
        # DGRO-shaped case: buy 1 share, buy a fraction more, sell almost all
        # of it. Cost basis on the remaining sliver must reflect average
        # cost, not the full sale proceeds subtracted from total cost.
        ticker_id = self._ticker("DGRO", currency="USD")
        self._buy(ticker_id, date(2026, 3, 3), Decimal("1"), Decimal("102.66"), fx_rate=Decimal("1.4"))
        self._buy(ticker_id, date(2026, 3, 23), Decimal("0.004"), Decimal("0.39"), fx_rate=Decimal("1.4"))
        self._sell(ticker_id, date(2026, 6, 5), Decimal("1"), Decimal("102.97"), fx_rate=Decimal("1.4"))

        position_engine.recompute_positions(self.connection)
        quantity, book_cad, book_mkt, realized_cad, provisional_qty, flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("0.00400000"))
        # avg_cost = (102.66 + 0.39) / 1.004 = 102.58565...; remaining book
        # = 0.004 * avg_cost = ~0.41034, not the near-total-loss a
        # proceeds-subtracted-from-cost formula would produce.
        self.assertAlmostEqual(float(book_cad), 0.41034, places=3)
        self.assertGreater(realized_cad, Decimal("0"))
        self.assertIsNone(flags)

    def test_split_then_partial_sell_keeps_quantity_positive(self):
        # WN/ZEQT-shaped case: buy shares, a 2-for-1-ish split arrives via an
        # activities CorporateAction row, then more is sold than the
        # pre-split share count. Without the split applied this goes
        # negative; with it applied the position stays positive.
        ticker_id = self._ticker("WN", currency="CAD")
        self._buy(ticker_id, date(2025, 4, 8), Decimal("0.2701"), Decimal("64.56"))
        self._buy(ticker_id, date(2025, 4, 11), Decimal("0.2977"), Decimal("70.60"))
        import_id = int(
            self.connection.execute(
                "INSERT INTO activity_imports (source_file, file_hash, status) "
                "VALUES ('f', 'h', 'succeeded') RETURNING import_id"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'CorporateAction', 'SUBDIVISION', 'SUBDIVISION', 'LONG',
                      ?, 'CAD', 1.0992, 'fp-split', 1, ?, ?)
            """,
            [date(2025, 8, 19), ticker_id, import_id, import_id],
        )
        self._sell(ticker_id, date(2026, 6, 4), Decimal("1.6645"), Decimal("164.10"))

        position_engine.recompute_positions(self.connection)
        quantity = self._snapshot(ticker_id)[0]

        self.assertGreaterEqual(quantity, Decimal("0"))
        self.assertAlmostEqual(
            float(quantity), 0.2701 + 0.2977 + 1.0992 - 1.6645, places=6
        )

    def test_email_only_buy_contributes_quantity_and_cost(self):
        # A trade only seen via email (not yet in a statement or activities
        # export) must still carry its cost into book value, not just
        # quantity -- otherwise unrealized P/L reads as ~100% of market
        # value (the XEQT/MDA/XNDU bug from the original investigation).
        ticker_id = self._ticker("MDA", currency="CAD")
        self.connection.execute(
            """
            INSERT INTO email_transactions (
                account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                source_symbol, price_currency, ticker_resolution_status, reconciliation_status
            ) VALUES ('TFSA', 'Fractional Buy', ?, 0.5894, 34.21, ?, 'MDA', 'CAD', 'resolved', 'provisional')
            """,
            [ticker_id, date(2026, 6, 19)],
        )

        position_engine.recompute_positions(self.connection)
        quantity, book_cad, _book_mkt, _realized, provisional_qty, _flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("0.58940000"))
        self.assertEqual(book_cad, Decimal("34.2100"))
        self.assertEqual(provisional_qty, Decimal("0.58940000"))

    def test_same_trade_across_sources_is_counted_once(self):
        # A trade recorded in a statement, an activities export, and an
        # email are all the same economic event; after reconciliation only
        # the activities row (highest precedence) should reach the ledger.
        ticker_id = self._ticker("AAPL", currency="USD")
        self._buy(ticker_id, date(2025, 4, 2), Decimal("1"), Decimal("100"))
        import_id = int(
            self.connection.execute(
                "INSERT INTO activity_imports (source_file, file_hash, status) "
                "VALUES ('f', 'h', 'succeeded') RETURNING import_id"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'Trade', 'BUY', 'BUY', 'LONG', ?, 'CAD', 1, -100,
                      'fp-a', 1, ?, ?)
            """,
            [date(2025, 4, 2), ticker_id, import_id, import_id],
        )
        self.connection.execute(
            """
            INSERT INTO email_transactions (
                account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                source_symbol, price_currency, ticker_resolution_status, reconciliation_status
            ) VALUES ('TFSA', 'Market Buy', ?, 1, 100, ?, 'AAPL', 'USD', 'resolved', 'provisional')
            """,
            [ticker_id, date(2025, 4, 2)],
        )

        reconcile_statement_activities(self.db_path)
        reconcile_email_transactions(self.db_path)
        position_engine.recompute_positions(self.connection)

        quantity, book_cad, *_ = self._snapshot(ticker_id)
        self.assertEqual(quantity, Decimal("1.00000000"))
        self.assertEqual(book_cad, Decimal("100.0000"))
        ledger_rows = self.connection.execute(
            "SELECT COUNT(*) FROM position_ledger WHERE ticker_id = ?", [ticker_id]
        ).fetchone()[0]
        self.assertEqual(ledger_rows, 1)

    def test_recompute_alone_deduplicates_unreconciled_sources(self):
        # Migrated-database scenario: rows in transactions/activities/emails
        # all predate the reconciliation passes, so every link column is
        # NULL. recompute_positions must run the passes itself -- otherwise
        # the same trade is counted once per source and every quantity
        # doubles (the 2026-07-08 production incident).
        ticker_id = self._ticker("XEQT", currency="CAD")
        self._buy(ticker_id, date(2025, 4, 2), Decimal("2"), Decimal("70"))
        import_id = int(
            self.connection.execute(
                "INSERT INTO activity_imports (source_file, file_hash, status) "
                "VALUES ('f', 'h', 'succeeded') RETURNING import_id"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'Trade', 'BUY', 'BUY', 'LONG', ?, 'CAD', 2, -70,
                      'fp-x', 1, ?, ?)
            """,
            [date(2025, 4, 2), ticker_id, import_id, import_id],
        )
        self.connection.execute(
            """
            INSERT INTO email_transactions (
                account, transaction_type, ticker_id, quantity, total_cost, transaction_date,
                source_symbol, price_currency, ticker_resolution_status, reconciliation_status
            ) VALUES ('TFSA', 'Market Buy', ?, 2, 70, ?, 'XEQT', 'CAD', 'resolved', 'provisional')
            """,
            [ticker_id, date(2025, 4, 2)],
        )

        # No explicit reconcile_* calls: the engine must handle it.
        position_engine.recompute_positions(self.connection)

        quantity, book_cad, *_ = self._snapshot(ticker_id)
        self.assertEqual(quantity, Decimal("2.00000000"))
        self.assertEqual(book_cad, Decimal("70.0000"))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM position_ledger WHERE ticker_id = ?", [ticker_id]
            ).fetchone()[0],
            1,
        )

    def test_oversell_beyond_held_quantity_is_flagged_and_clamped(self):
        ticker_id = self._ticker("NVDA", currency="USD")
        self._buy(ticker_id, date(2025, 1, 1), Decimal("1"), Decimal("100"))
        self._sell(ticker_id, date(2025, 2, 1), Decimal("5"), Decimal("600"))

        position_engine.recompute_positions(self.connection)
        quantity, _book_cad, _book_mkt, _realized, _provisional, flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("0.00000000"))
        self.assertIn("oversell_clamped", flags)

    def test_missing_buy_cost_is_flagged_not_silently_zeroed_without_trace(self):
        ticker_id = self._ticker("META", currency="USD")
        self._buy(ticker_id, date(2025, 11, 4), Decimal("0.1919"), None)

        position_engine.recompute_positions(self.connection)
        quantity, book_cad, _book_mkt, _realized, _provisional, flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("0.19190000"))
        self.assertEqual(book_cad, Decimal("0.0000"))
        self.assertIn("buy_missing_cost", flags)

    def test_statement_split_without_activities_quantity_is_flagged(self):
        # A STKREORG row with no matching activities CorporateAction quantity
        # is invisible to v_trade_events (it carries no quantity itself), so
        # it must not silently produce a wrong share count.
        ticker_id = self._ticker("SCHD", currency="USD")
        self._buy(ticker_id, date(2024, 9, 1), Decimal("1"), Decimal("50"))
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity) "
            "VALUES (?, 'STKREORG', ?, NULL)",
            [date(2024, 10, 11), ticker_id],
        )

        position_engine.recompute_positions(self.connection)
        quantity, _book_cad, _book_mkt, _realized, _provisional, flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("1.00000000"))
        self.assertIn("split_without_quantity", flags)

    def test_statement_split_with_matching_activities_quantity_is_not_flagged(self):
        ticker_id = self._ticker("SCHD", currency="USD")
        self._buy(ticker_id, date(2024, 9, 1), Decimal("1"), Decimal("50"))
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity) "
            "VALUES (?, 'STKREORG', ?, NULL)",
            [date(2024, 10, 11), ticker_id],
        )
        import_id = int(
            self.connection.execute(
                "INSERT INTO activity_imports (source_file, file_hash, status) "
                "VALUES ('f', 'h', 'succeeded') RETURNING import_id"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'CorporateAction', 'SUBDIVISION', 'SUBDIVISION', 'LONG',
                      ?, 'CAD', 1.0, 'fp-schd-split', 1, ?, ?)
            """,
            [date(2024, 10, 11), ticker_id, import_id, import_id],
        )

        position_engine.recompute_positions(self.connection)
        quantity, _book_cad, _book_mkt, _realized, _provisional, flags = self._snapshot(ticker_id)

        self.assertEqual(quantity, Decimal("2.00000000"))
        self.assertNotIn("split_without_quantity", flags or ())

    def test_ensure_positions_fresh_recomputes_only_when_stale(self):
        ticker_id = self._ticker("AAPL", currency="USD")
        self._buy(ticker_id, date(2025, 1, 1), Decimal("1"), Decimal("100"))

        self.assertTrue(position_engine.ensure_positions_fresh(self.connection))
        self.assertFalse(position_engine.is_stale(self.connection))
        self.assertFalse(position_engine.ensure_positions_fresh(self.connection))

        first_computed_at = self.connection.execute(
            "SELECT computed_at FROM position_engine_meta"
        ).fetchone()[0]

        self._buy(ticker_id, date(2025, 2, 1), Decimal("1"), Decimal("110"))
        self.assertTrue(position_engine.is_stale(self.connection))
        self.assertTrue(position_engine.ensure_positions_fresh(self.connection))

        second_computed_at = self.connection.execute(
            "SELECT computed_at FROM position_engine_meta"
        ).fetchone()[0]
        self.assertGreaterEqual(second_computed_at, first_computed_at)
        quantity = self._snapshot(ticker_id)[0]
        self.assertEqual(quantity, Decimal("2.00000000"))


if __name__ == "__main__":
    unittest.main()
