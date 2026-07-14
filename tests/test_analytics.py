import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import database
from analytics import (
    build_data_quality,
    get_benchmark_returns,
    get_cash_summary,
    get_currency_exposure,
    get_dividend_history,
    get_excluded_positions,
    get_expense_ratios,
    get_external_flow_series,
    get_group_allocation,
    get_historical_portfolio_values,
    get_holdings,
    get_look_through_sector_exposure,
    get_position,
    get_portfolio_summary,
    get_dividend_summary,
    get_fx_fee_summary,
    get_realized_gain_summary,
    get_sector_allocation,
    get_turnover_and_holding_period,
    load_allocation_targets,
    portfolio_report,
)


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

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

        # Average-cost accounting: 10 shares bought at $100/share average;
        # selling 4 reduces the book by 4 * $100 = $400 (not by the $480 sale
        # proceeds), leaving 6 shares at $600 book value.
        self.assertEqual(holding.quantity, Decimal("6"))
        self.assertEqual(holding.cost_basis, Decimal("600.0000"))
        self.assertEqual(holding.market_value, Decimal("552.0000"))

    def test_resolved_provisional_email_trade_updates_live_quantity(self):
        ticker_id = self._ticker()
        database.get_shared_connection(self.db_path).execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Fractional Buy', ?, 1.25, ?, 'AAPL', 'USD',
                         'resolved', 'provisional')""",
            [ticker_id, date(2025, 1, 2)],
        )

        holding = get_position(ticker_id, self.db_path)

        self.assertEqual(holding.quantity, Decimal("1.25000000"))
        self.assertEqual(holding.provisional_quantity, Decimal("1.25000000"))
        self.assertTrue(holding.has_provisional_activity)

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

    def test_historical_values_reflect_sells_and_provisional_email_buys(self):
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
                (date(2025, 1, 4), "SELL", ticker_id, Decimal("-6"), date(2025, 1, 4), None, Decimal("660"), None),
            ],
        )
        connection.execute(
            """INSERT INTO email_transactions (
                   account, transaction_type, ticker_id, quantity, transaction_date,
                   source_symbol, price_currency, ticker_resolution_status, reconciliation_status
               ) VALUES ('TFSA', 'Fractional Buy', ?, 2, ?, 'AAPL', 'USD', 'resolved', 'provisional')""",
            [ticker_id, date(2025, 1, 5)],
        )
        connection.executemany(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [ticker_id, date(2025, 1, 2), 100.0, 105.0, 99.0, 100.0, 100.0, 100],
                [ticker_id, date(2025, 1, 3), 100.0, 105.0, 99.0, 100.0, 100.0, 100],
                [ticker_id, date(2025, 1, 4), 105.0, 108.0, 104.0, 108.0, 108.0, 100],
                [ticker_id, date(2025, 1, 5), 108.0, 112.0, 106.0, 110.0, 110.0, 100],
            ],
        )

        values = get_historical_portfolio_values(self.db_path)
        by_date = {row["date"]: row for row in values}

        # 10 shares bought before any price -> qty 10, first known close is Jan 2's 100.
        self.assertEqual(by_date[date(2025, 1, 2)]["portfolio_value"], Decimal("1000"))
        self.assertEqual(by_date[date(2025, 1, 3)]["portfolio_value"], Decimal("1000"))
        # SELL of 6 on Jan 4 leaves qty 4 at close 108.
        self.assertEqual(by_date[date(2025, 1, 4)]["portfolio_value"], Decimal("432"))
        # Provisional email BUY of 2 on Jan 5 leaves qty 6 at close 110.
        self.assertEqual(by_date[date(2025, 1, 5)]["portfolio_value"], Decimal("660"))

    def test_historical_values_never_go_negative_from_over_sell(self):
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
                (date(2025, 1, 2), "BUY", ticker_id, Decimal("5"), date(2025, 1, 2), Decimal("500"), None, None),
                (date(2025, 1, 3), "SELL", ticker_id, Decimal("-8"), date(2025, 1, 3), None, Decimal("800"), None),
            ],
        )
        connection.executemany(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [ticker_id, date(2025, 1, 2), 100.0, 105.0, 99.0, 100.0, 100.0, 100],
                [ticker_id, date(2025, 1, 3), 100.0, 105.0, 99.0, 100.0, 100.0, 100],
            ],
        )

        values = get_historical_portfolio_values(self.db_path)
        by_date = {row["date"]: row for row in values}

        # Net quantity goes negative (-3) after the over-sell; clamp at zero
        # rather than crediting a phantom short value.
        self.assertEqual(by_date[date(2025, 1, 3)]["securities_value"], Decimal("0"))

    def test_historical_values_include_cash_balance(self):
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
                (date(2025, 1, 3), "WITHDRAWAL", date(2025, 1, 3), Decimal("200"), Decimal("0"), Decimal("0"), None),
            ],
        )

        values = get_historical_portfolio_values(self.db_path)
        by_date = {row["date"]: row for row in values}

        self.assertEqual(by_date[date(2025, 1, 1)]["cash_balance"], Decimal("1000"))
        self.assertEqual(by_date[date(2025, 1, 3)]["cash_balance"], Decimal("800"))
        self.assertEqual(by_date[date(2025, 1, 3)]["portfolio_value"], Decimal("800"))

    def test_negative_net_quantity_is_excluded_from_holdings(self):
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
                (date(2025, 1, 2), "BUY", ticker_id, Decimal("5"), date(2025, 1, 2), Decimal("500"), None, None),
                (date(2025, 1, 3), "SELL", ticker_id, Decimal("-8"), date(2025, 1, 3), None, Decimal("800"), None),
            ],
        )

        holdings = get_holdings(self.db_path)
        excluded = get_excluded_positions(self.db_path)
        summary = get_portfolio_summary(self.db_path)

        self.assertEqual(holdings, [])
        self.assertEqual(len(excluded), 1)
        # The position engine clamps an oversell at zero rather than letting
        # the running quantity go negative (see position_engine.py's SELL
        # handling), flagging it instead so the error stays visible here.
        self.assertEqual(excluded[0].quantity, Decimal("0.00000000"))
        self.assertIn("oversell_clamped", excluded[0].data_quality_flags)
        # Portfolio value must not be reduced by the excluded negative position.
        self.assertEqual(summary.portfolio_value, summary.cash.balance)

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

        report = portfolio_report(self.db_path, benchmark_symbol=None)

        self.assertIn("holdings", report)
        self.assertIn("summary", report)
        self.assertIn("portfolio_value", report["summary"])
        self.assertIn("cash", report["summary"])
        self.assertIn("allocation", report)
        self.assertIn("targets", report)
        self.assertIn("performance", report)
        self.assertIn("historical_values", report["performance"])
        self.assertIn("income", report)
        self.assertIn("fees", report)
        self.assertIn("activity", report)
        self.assertIn("realized_gains", report)
        self.assertIn("data_quality", report)
        self.assertIn("unavailable_metrics", report)
        # No live network call was made; benchmark comparison reports unavailable.
        self.assertFalse(report["performance"]["benchmark"]["available"])

    def test_fx_fee_summary_extracts_inclusive_buy_and_sell_fees(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (date(2025, 1, 2), "BUY", ticker_id, Decimal("1"), date(2025, 1, 2), Decimal("101.50"), None, Decimal("1.4")),
                (date(2025, 1, 3), "SELL", ticker_id, Decimal("-1"), date(2025, 1, 3), None, Decimal("98.50"), Decimal("1.4")),
                (date(2025, 1, 4), "BUY", ticker_id, Decimal("1"), date(2025, 1, 4), Decimal("50"), None, None),
            ],
        )

        summary = get_fx_fee_summary(self.db_path)

        self.assertEqual(summary["transaction_count"], 2)
        self.assertEqual(summary["estimated_buy_fee_cad"], Decimal("1.5000"))
        self.assertEqual(summary["estimated_sell_fee_cad"], Decimal("1.5000"))
        self.assertEqual(summary["estimated_fx_fee_cad"], Decimal("3.0000"))

    def test_fx_fee_non_statement_source_is_explicitly_unavailable(self):
        summary = get_fx_fee_summary(self.db_path, source="email")
        self.assertFalse(summary["available"])
        self.assertEqual(summary["estimated_fx_fee_cad"], Decimal("0"))

    def test_dividends_default_to_email_and_can_use_activities(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """INSERT INTO email_transactions (
                account, transaction_type, ticker_id, debit, transaction_date
            ) VALUES ('TFSA', 'Dividend', ?, 4.25, '2025-02-01')""",
            [ticker_id],
        )

        email = get_dividend_summary(self.db_path)

        self.assertEqual(email["source"], "email")
        self.assertEqual(email["totals_by_currency"], {"USD": Decimal("4.2500")})

    def test_realized_gain_uses_prior_weighted_average_cost(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            [
                (date(2024, 1, 1), "BUY", ticker_id, Decimal("10"), date(2024, 1, 1), Decimal("100"), None),
                (date(2025, 1, 1), "SELL", ticker_id, Decimal("-4"), date(2025, 1, 1), None, Decimal("60")),
            ],
        )

        result = get_realized_gain_summary(self.db_path, date_from=date(2025, 1, 1))

        self.assertEqual(result["total_realized_gain"], Decimal("20"))

    def test_external_flow_series_aggregates_same_day_contributions(self):
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO cash_transactions (
                transaction_date, transaction_type, execution_date,
                debit, credit, fx_rate, balance
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                (date(2025, 1, 2), "DEPOSIT", date(2025, 1, 2), Decimal("0"), Decimal("500"), Decimal("0")),
                (date(2025, 1, 2), "CONTRIBUTION", date(2025, 1, 2), Decimal("0"), Decimal("100"), Decimal("0")),
                (date(2025, 1, 5), "DEPOSIT", date(2025, 1, 5), Decimal("0"), Decimal("50"), Decimal("0")),
            ],
        )

        flows = get_external_flow_series(self.db_path, source="statements")

        self.assertEqual(flows, [
            {"date": date(2025, 1, 2), "amount": Decimal("600")},
            {"date": date(2025, 1, 5), "amount": Decimal("50")},
        ])


class AnalyticsAllocationTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def _ticker(
        self, symbol="AAPL", exchange="NASDAQ", currency="USD", name="Apple Inc.", security_type="stock"
    ):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            """
            INSERT INTO tickers (
                ticker_symbol, exchange, currency, security_name, security_type
            )
            VALUES (?, ?, ?, ?, ?)
            RETURNING ticker_id
            """,
            [symbol, exchange, currency, name, security_type],
        ).fetchone()[0]

    def _buy(self, ticker_id, quantity, debit, transaction_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, 'BUY', ?, ?, ?, ?, NULL, NULL)
            """,
            [transaction_date, ticker_id, Decimal(str(quantity)), transaction_date, Decimal(str(debit))],
        )

    def _price(self, ticker_id, close, record_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, record_date, close, close, close, close, close, 100],
        )

    def _classify(self, ticker_id, primary_group, secondary_tags=None, review_needed=False):
        database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO portfolio_classifications (
                ticker_id, primary_group, secondary_tags, review_needed, generated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [ticker_id, primary_group, json.dumps(secondary_tags or []), review_needed],
        )

    def test_group_allocation_buckets_unclassified_when_table_empty(self):
        ticker_id = self._ticker()
        self._buy(ticker_id, 10, 1000)
        self._price(ticker_id, 100)
        holdings = get_holdings(self.db_path)

        result = get_group_allocation(self.db_path, holdings)

        self.assertEqual(result["by_group"]["Unclassified"]["weight"], 1.0)
        self.assertIsNotNone(result["group_unavailable_reason"])
        self.assertIsNone(result["by_geography"])

    def test_group_allocation_uses_classifier_group_and_geography_tag(self):
        ticker_id = self._ticker()
        self._buy(ticker_id, 10, 1000)
        self._price(ticker_id, 100)
        self._classify(ticker_id, "Quality", secondary_tags=["Equity", "US"])
        holdings = get_holdings(self.db_path)

        result = get_group_allocation(self.db_path, holdings)

        self.assertEqual(result["by_group"]["Quality"]["weight"], 1.0)
        self.assertEqual(result["by_geography"]["US"]["weight"], 1.0)

    def test_sector_allocation_buckets_etfs_separately_and_flags_missing_sector(self):
        stock_id = self._ticker(symbol="AAPL", security_type="stock")
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", security_type="etf")
        self._buy(stock_id, 10, 1000)
        self._buy(etf_id, 10, 1000, transaction_date=date(2025, 1, 3))
        self._price(stock_id, 100)
        self._price(etf_id, 100, record_date=date(2025, 1, 3))
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO stock_details (ticker_id, sector, industry) VALUES (?, 'Technology', 'Consumer Electronics')",
            [stock_id],
        )
        holdings = get_holdings(self.db_path)

        result = get_sector_allocation(self.db_path, holdings)

        self.assertIn("Technology", result["by_sector"])
        self.assertIn("ETF", result["by_sector"])
        self.assertEqual(result["missing_sector_tickers"], [])

    def test_sector_allocation_flags_missing_stock_sector(self):
        ticker_id = self._ticker()
        self._buy(ticker_id, 10, 1000)
        self._price(ticker_id, 100)
        holdings = get_holdings(self.db_path)

        result = get_sector_allocation(self.db_path, holdings)

        self.assertEqual(result["by_sector"]["Unknown"]["weight"], 1.0)
        self.assertEqual(result["missing_sector_tickers"], ["AAPL"])

    def test_look_through_sector_exposure_blends_etf_weights(self):
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", security_type="etf")
        self._buy(etf_id, 10, 1000)
        self._price(etf_id, 100)
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO etf_details (ticker_id, sector_weights) VALUES (?, ?)",
            [etf_id, json.dumps({"Technology": 0.6, "Financials": 0.4})],
        )
        holdings = get_holdings(self.db_path)

        result = get_look_through_sector_exposure(self.db_path, holdings)

        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["coverage_percent"], 1.0)
        self.assertAlmostEqual(result["weights"]["Technology"], 0.6)
        self.assertAlmostEqual(result["weights"]["Financials"], 0.4)

    def test_look_through_sector_exposure_unavailable_without_any_data(self):
        ticker_id = self._ticker()
        self._buy(ticker_id, 10, 1000)
        self._price(ticker_id, 100)
        holdings = get_holdings(self.db_path)

        result = get_look_through_sector_exposure(self.db_path, holdings)

        self.assertFalse(result["available"])

    def test_currency_exposure_groups_by_listing_currency(self):
        usd_id = self._ticker(symbol="AAPL", currency="USD")
        cad_id = self._ticker(symbol="RY", exchange="TSX", currency="CAD")
        self._buy(usd_id, 10, 1000)
        self._buy(cad_id, 10, 1000, transaction_date=date(2025, 1, 3))
        self._price(usd_id, 100)
        self._price(cad_id, 100, record_date=date(2025, 1, 3))
        holdings = get_holdings(self.db_path)

        result = get_currency_exposure(holdings)

        self.assertAlmostEqual(result["USD"]["weight"], 0.5)
        self.assertAlmostEqual(result["CAD"]["weight"], 0.5)

    def test_expense_ratios_only_returns_known_etfs(self):
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", security_type="etf")
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO etf_details (ticker_id, expense_ratio) VALUES (?, ?)",
            [etf_id, Decimal("0.002")],
        )

        result = get_expense_ratios(self.db_path, [etf_id])

        self.assertEqual(result[etf_id], Decimal("0.002"))

    def test_turnover_and_holding_period_distinguishes_open_and_closed(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                (date(2024, 1, 1), "BUY", ticker_id, Decimal("10"), date(2024, 1, 1), Decimal("1000"), None),
                (date(2024, 1, 11), "SELL", ticker_id, Decimal("-4"), date(2024, 1, 11), None, Decimal("440")),
            ],
        )
        self._price(ticker_id, 100, record_date=date(2024, 1, 1))

        result = get_turnover_and_holding_period(self.db_path)

        self.assertTrue(result["turnover"]["available"])
        closed = result["average_holding_period"]["closed"]
        self.assertTrue(closed["available"])
        self.assertAlmostEqual(closed["days"], 10.0)
        opened = result["average_holding_period"]["open"]
        self.assertTrue(opened["available"])
        self.assertGreater(opened["days"], 0)

    def test_dividend_history_groups_by_month_and_computes_yield(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """INSERT INTO email_transactions (
                account, transaction_type, ticker_id, debit, transaction_date
            ) VALUES ('TFSA', 'Dividend', ?, 4.00, ?)""",
            [ticker_id, date.today().replace(day=1)],
        )

        result = get_dividend_history(
            self.db_path, portfolio_value=Decimal("1000"), book_cost=Decimal("800")
        )

        self.assertEqual(result["transaction_count"], 1)
        self.assertTrue(result["yield_on_cost"]["available"])
        self.assertFalse(result["dividend_growth"]["available"])

    def test_data_quality_flags_negative_stale_and_suspicious_positions(self):
        ticker_id = self._ticker()
        self._buy(ticker_id, 10, 1000, transaction_date=date(2020, 1, 1))
        self._price(ticker_id, 1.0, record_date=date(2020, 1, 1))
        holdings = get_holdings(self.db_path)

        result = build_data_quality(holdings, [], {}, today=date.today())

        codes = {flag["code"] for flag in result["flags"]}
        self.assertIn("stale_price", codes)
        self.assertIn("suspicious_gain", codes)
        self.assertIn("missing_classification", codes)

    def test_benchmark_returns_none_on_fetch_failure(self):
        def failing_fetch(symbols, start, end):
            raise RuntimeError("offline")

        result = get_benchmark_returns(
            "XEQT.TO", date(2025, 1, 1), date(2025, 2, 1), fetch_history=failing_fetch
        )

        self.assertIsNone(result)

    def test_benchmark_returns_computed_from_injected_history(self):
        def stub_fetch(symbols, start, end):
            return [
                {"date": date(2025, 1, 2), "close": 100.0},
                {"date": date(2025, 1, 3), "close": 110.0},
                {"date": date(2025, 1, 4), "close": 99.0},
            ]

        result = get_benchmark_returns(
            "XEQT.TO", date(2025, 1, 1), date(2025, 2, 1), fetch_history=stub_fetch
        )

        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["return"], 0.1)

    def test_load_allocation_targets_reads_ported_policy_groups(self):
        targets = load_allocation_targets()
        self.assertIsNotNone(targets)
        self.assertEqual(targets["Core"]["target_percent"], 60)


if __name__ == "__main__":
    unittest.main()
