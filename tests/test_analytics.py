import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import database
from analytics import (
    get_cash_summary,
    get_historical_portfolio_values,
    get_holdings,
    get_position,
    get_portfolio_summary,
    portfolio_report,
)


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def _ticker(self, symbol="AAPL", exchange="NASDAQ", currency="USD", name="Apple Inc."):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            """
            INSERT INTO tickers (
                ticker_symbol, exchange, currency, security_name, security_type
            )
            VALUES (?, ?, ?, ?, 'stock')
            RETURNING ticker_id
            """,
            [symbol, exchange, currency, name],
        ).fetchone()[0]

    def test_empty_database_returns_empty_holdings_and_zero_cash(self):
        holdings = get_holdings(self.db_path)
        cash = get_cash_summary(self.db_path)
        summary = get_portfolio_summary(self.db_path)

        self.assertEqual(holdings, [])
        self.assertEqual(cash.balance, Decimal("0"))
        self.assertEqual(cash.source, "net_cash_flow")
        self.assertEqual(summary.portfolio_value, Decimal("0"))

    def test_buy_and_sell_transactions_reconstruct_current_quantity(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (date(2025, 1, 2), "BUY", ticker_id, Decimal("10"), date(2025, 1, 2), Decimal("1000"), None, None),
                (date(2025, 1, 3), "SELL", ticker_id, Decimal("-4"), date(2025, 1, 3), None, Decimal("480"), None),
            ],
        )
        connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 1, 3), 90.0, 95.0, 88.0, 92.0, 92.0, 100],
        )

        holding = get_position(ticker_id, self.db_path)

        self.assertEqual(holding.quantity, Decimal("6"))
        self.assertEqual(holding.cost_basis, Decimal("520"))
        self.assertEqual(holding.market_value, Decimal("552"))

    def test_latest_explicit_cash_balance_is_preferred(self):
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO cash_transactions (
                transaction_date, transaction_type, execution_date,
                debit, credit, fx_rate, balance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (date(2025, 1, 1), "DEPOSIT", date(2025, 1, 1), Decimal("0"), Decimal("1000"), Decimal("0"), None),
                (date(2025, 1, 2), "BALANCE", date(2025, 1, 2), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1250")),
            ],
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("1250"))
        self.assertEqual(cash.source, "explicit_balance")

    def test_net_cash_flow_is_used_when_no_balance_exists(self):
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO cash_transactions (
                transaction_date, transaction_type, execution_date,
                debit, credit, fx_rate, balance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (date(2025, 1, 1), "DEPOSIT", date(2025, 1, 1), Decimal("0"), Decimal("1000"), Decimal("0"), None),
                (date(2025, 1, 2), "WITHDRAWAL", date(2025, 1, 2), Decimal("100"), Decimal("0"), Decimal("0"), None),
            ],
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("900"))
        self.assertEqual(cash.source, "net_cash_flow")

    def test_historical_values_are_chronological(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [date(2025, 1, 2), "BUY", ticker_id, Decimal("10"), date(2025, 1, 2), Decimal("1000"), None, None],
        )
        connection.executemany(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [ticker_id, date(2025, 1, 2), 100.0, 105.0, 99.0, 101.0, 101.0, 100],
                [ticker_id, date(2025, 1, 3), 101.0, 110.0, 100.0, 109.0, 109.0, 100],
            ],
        )

        values = get_historical_portfolio_values(self.db_path)

        self.assertEqual([row["date"] for row in values], [date(2025, 1, 2), date(2025, 1, 3)])
        self.assertEqual(values[0]["portfolio_value"], Decimal("1010"))
        self.assertEqual(values[1]["portfolio_value"], Decimal("1090"))

    def test_portfolio_report_combines_expected_sections(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [date(2025, 1, 2), "BUY", ticker_id, Decimal("1"), date(2025, 1, 2), Decimal("100"), None, None],
        )
        connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 1, 2), 100.0, 100.0, 100.0, 100.0, 100.0, 1],
        )

        report = portfolio_report(self.db_path)

        self.assertIn("holdings", report)
        self.assertIn("cash", report)
        self.assertIn("portfolio_value", report)
        self.assertIn("historical_values", report)


if __name__ == "__main__":
    unittest.main()
