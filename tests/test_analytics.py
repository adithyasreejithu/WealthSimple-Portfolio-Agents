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
    get_classification_details,
    get_currency_exposure,
    get_dividend_history,
    get_etf_overlap,
    get_excluded_positions,
    get_price_history,
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
    get_trend_overlay_series,
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
            INSERT INTO statement_balances (transaction_date, transaction_type, balance)
            VALUES (?, ?, ?)
            """,
            [
                (date(2025, 1, 1), "DEPOSIT", Decimal("1000")),
                (date(2025, 1, 2), "BALANCE", Decimal("1250")),
            ],
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("1250"))
        self.assertEqual(cash.source, "explicit_balance")

    def test_cash_balance_prefers_a_later_ticker_linked_trade_over_an_earlier_cash_only_balance(self):
        """
        Reproduces a real production bug: a statement's trailing balance is
        stated on every line, but only cash-only lines (deposits, interest,
        loans) used to keep that balance -- a BUY/SELL/DIV row tied to a
        resolved ticker went to `transactions`, which has no `balance`
        column, silently discarding it. `get_cash_summary` then reported a
        stale mid-month balance ($21.73) instead of the true end-of-statement
        balance ($0.00) whenever a trade was the last line of the month.
        `statement_balances` now records every line regardless of ticker
        resolution, so the truly latest balance always wins.
        """
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            """
            INSERT INTO statement_balances (transaction_date, transaction_type, balance)
            VALUES (?, ?, ?)
            """,
            [
                (date(2026, 5, 14), "FPLINT", Decimal("21.73")),
                (date(2026, 5, 21), "BUY", Decimal("0.00")),
            ],
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("0.00"))
        self.assertEqual(cash.source, "explicit_balance")

    def test_cash_balance_rolls_forward_with_activities_after_the_statement_anchor(self):
        """
        Reproduces the gap discovered right after fixing the statement-
        balance bug: statement PDFs lag the activities CSV export by weeks,
        so anchoring cash to the latest statement alone leaves it stale
        between statements. `activities` (the deduplicated, typed CSV table)
        already reports every cash-affecting row in CAD, so summing
        `net_cash_amount` for dates strictly after the anchor and adding it
        to the anchor balance keeps cash current without waiting for the
        next statement.
        """
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO statement_balances (transaction_date, transaction_type, balance) "
            "VALUES ('2026-05-21', 'BUY', 0.00)"
        )
        import_id = connection.execute(
            "INSERT INTO activity_imports (source_file, file_hash, status) "
            "VALUES ('f', 'h-cash-rollforward', 'succeeded') RETURNING import_id"
        ).fetchone()[0]
        connection.executemany(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_code,
                transaction_currency, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', ?, 'X', 'CAD', ?, ?, 1, ?, ?)
            """,
            [
                # This trade's cash impact is already embedded in the
                # $0.00 anchor balance -- it must NOT be double-counted.
                (date(2026, 5, 21), "Trade", Decimal("-14.82"), "fp-anchor", import_id, import_id),
                (date(2026, 6, 15), "Dividend", Decimal("0.29"), "fp-div1", import_id, import_id),
                (date(2026, 7, 15), "Dividend", Decimal("0.02"), "fp-div2", import_id, import_id),
            ],
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("0.31"))
        self.assertEqual(cash.source, "explicit_balance_rolled_forward")

    def test_cash_balance_source_stays_explicit_when_no_activity_follows_the_anchor(self):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO statement_balances (transaction_date, transaction_type, balance) "
            "VALUES ('2026-05-21', 'BUY', 0.00)"
        )

        cash = get_cash_summary(self.db_path)

        self.assertEqual(cash.balance, Decimal("0.00"))
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

    def test_historical_cash_balance_rolls_forward_with_activities_after_the_statement_anchor(self):
        """
        Same statement-lags-the-CSV gap as `get_cash_summary`, but for the
        historical series: a date after the last statement anchor should
        reflect that day's cumulative activities-sourced cash flow on top of
        the anchor, not stay flat at the anchor's own balance.
        """
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO statement_balances (transaction_date, transaction_type, balance) "
            "VALUES ('2025-01-01', 'DEPOSIT', 1000.00)"
        )
        import_id = connection.execute(
            "INSERT INTO activity_imports (source_file, file_hash, status) "
            "VALUES ('f', 'h-hist-cash-rollforward', 'succeeded') RETURNING import_id"
        ).fetchone()[0]
        connection.executemany(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_code,
                transaction_currency, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', ?, 'X', 'CAD', ?, ?, 1, ?, ?)
            """,
            [
                (date(2025, 1, 1), "Deposit", Decimal("1000"), "fp-anchor", import_id, import_id),
                (date(2025, 1, 3), "Dividend", Decimal("5"), "fp-div", import_id, import_id),
            ],
        )

        values = get_historical_portfolio_values(self.db_path)
        by_date = {row["date"]: row for row in values}

        self.assertEqual(by_date[date(2025, 1, 1)]["cash_balance"], Decimal("1000"))
        self.assertEqual(by_date[date(2025, 1, 3)]["cash_balance"], Decimal("1005"))

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

    def test_realized_gain_converts_usd_activity_amounts(self):
        # Activities rows carry net_cash_amount in transaction_currency; both
        # the BUY cost basis and SELL proceeds must be FX-converted to CAD.
        connection = database.get_shared_connection(self.db_path)
        fx_id = connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES ('USDCAD=X', 'FX', 'CAD', 'USD/CAD', 'fx_rate') RETURNING ticker_id"
        ).fetchone()[0]
        for record_date, close in ((date(2025, 1, 2), 1.35), (date(2025, 6, 2), 1.40)):
            connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [fx_id, record_date, close, close, close, close, close, 0],
            )
        ticker_id = self._ticker(symbol="NVDA", name="NVIDIA Corp.")
        import_id = connection.execute(
            "INSERT INTO activity_imports (source_file, file_hash, status) "
            "VALUES ('f', 'h-usd', 'succeeded') RETURNING import_id"
        ).fetchone()[0]
        connection.executemany(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, ticker_id, transaction_currency, quantity, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'Trade', ?, ?, 'LONG', ?, 'USD', ?, ?, ?, 1, ?, ?)
            """,
            [
                (date(2025, 1, 2), "BUY", "BUY", ticker_id, Decimal("10"), Decimal("-1000"), "fp-b", import_id, import_id),
                (date(2025, 6, 2), "SELL", "SELL", ticker_id, Decimal("-10"), Decimal("1200"), "fp-s", import_id, import_id),
            ],
        )

        result = get_realized_gain_summary(self.db_path)

        # proceeds 1200 USD * 1.40 minus cost 1000 USD * 1.35
        self.assertEqual(result["total_realized_gain"], Decimal("330"))

    def test_report_holdings_carry_portfolio_weight(self):
        connection = database.get_shared_connection(self.db_path)
        a = self._ticker()
        b = self._ticker(symbol="MSFT", name="Microsoft Corp.")
        connection.executemany(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, 'BUY', ?, ?, ?, ?, NULL, NULL)
            """,
            [
                (date(2025, 1, 2), a, Decimal("1"), date(2025, 1, 2), Decimal("300")),
                (date(2025, 1, 2), b, Decimal("1"), date(2025, 1, 2), Decimal("100")),
            ],
        )
        for tid, close in ((a, 300.0), (b, 100.0)):
            connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [tid, date(2025, 1, 2), close, close, close, close, close, 1],
            )

        report = portfolio_report(self.db_path, benchmark_symbol=None)

        weights = {row["ticker_symbol"]: row["weight"] for row in report["holdings"]}
        self.assertAlmostEqual(weights["AAPL"], 0.75)
        self.assertAlmostEqual(weights["MSFT"], 0.25)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_trend_overlay_series_cumulates_deposits_and_forward_fills_benchmarks(self):
        connection = database.get_shared_connection(self.db_path)
        # Benchmark closes: only VFV (the SP500 overlay), starting mid-grid so
        # the first point has a leading null; the second close forward-fills.
        vfv_id = connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES ('VFV', 'BENCHMARK', 'CAD', 'VFV (benchmark)', 'benchmark') RETURNING ticker_id"
        ).fetchone()[0]
        for record_date, close in ((date(2025, 1, 3), 140.0), (date(2025, 1, 4), 141.0)):
            connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [vfv_id, record_date, close, close, close, close, close, 0],
            )
        # External flows: contribution then withdrawal.
        import_id = connection.execute(
            "INSERT INTO activity_imports (source_file, file_hash, status) "
            "VALUES ('f', 'h-flows', 'succeeded') RETURNING import_id"
        ).fetchone()[0]
        connection.executemany(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_type, activity_subtype,
                activity_code, direction, transaction_currency, net_cash_amount,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'Deposit', NULL, 'CONT', NULL, 'CAD', ?, ?, 1, ?, ?)
            """,
            [
                (date(2025, 1, 2), Decimal("500"), "fp-c1", import_id, import_id),
                (date(2025, 1, 4), Decimal("-200"), "fp-c2", import_id, import_id),
            ],
        )
        historical_values = [
            {"date": date(2025, 1, 2), "portfolio_value": Decimal("500")},
            {"date": date(2025, 1, 3), "portfolio_value": Decimal("510")},
            {"date": date(2025, 1, 5), "portfolio_value": Decimal("310")},
        ]

        result = get_trend_overlay_series(self.db_path, historical_values)

        self.assertTrue(result["available"])
        self.assertTrue(result["benchmarks"]["SP500"]["available"])
        self.assertFalse(result["benchmarks"]["XEQT"]["available"])
        deposits = [p["net_deposits_cum"] for p in result["points"]]
        self.assertEqual(deposits, [Decimal("500"), Decimal("500"), Decimal("300")])
        sp500 = [p["benchmarks"]["SP500"] for p in result["points"]]
        self.assertIsNone(sp500[0])
        self.assertEqual(sp500[1], Decimal("140"))
        self.assertEqual(sp500[2], Decimal("141"))

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

    def test_single_name_cap_exempts_core_etfs(self):
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", currency="CAD", security_type="etf")
        stock_id = self._ticker(symbol="AAPL")
        self._buy(etf_id, 95, 9500)
        self._buy(stock_id, 5, 500)
        self._price(etf_id, 100)
        self._price(stock_id, 100)
        self._classify(etf_id, "Core")
        self._classify(stock_id, "Growth")

        report = portfolio_report(self.db_path, benchmark_symbol=None)
        concentration = report["allocation"]["concentration"]

        self.assertEqual(concentration["exempt_tickers"], ["XEQT"])
        self.assertFalse(concentration["single_name_limit_breached"])
        self.assertEqual(concentration["max_flagged_name_ticker"], "AAPL")
        # Whole-portfolio descriptive figures are unaffected by the exemption.
        self.assertEqual(concentration["max_single_name_ticker"], "XEQT")

    def test_single_name_cap_still_flags_large_stock(self):
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", currency="CAD", security_type="etf")
        stock_id = self._ticker(symbol="AAPL")
        self._buy(etf_id, 80, 8000)
        self._buy(stock_id, 20, 2000)
        self._price(etf_id, 100)
        self._price(stock_id, 100)
        self._classify(etf_id, "Core")
        self._classify(stock_id, "Growth")

        report = portfolio_report(self.db_path, benchmark_symbol=None)
        concentration = report["allocation"]["concentration"]

        self.assertTrue(concentration["single_name_limit_breached"])
        self.assertEqual(concentration["max_flagged_name_ticker"], "AAPL")
        self.assertAlmostEqual(concentration["max_flagged_name_weight"], 0.2)

    def test_look_through_merges_snake_case_etf_sector_labels(self):
        # yfinance ETF sector keys are snake_case while stock sectors are
        # Title Case; both must land on one canonical label or a sector shows
        # up twice in the look-through split.
        etf_id = self._ticker(symbol="XEQT", exchange="TSX", security_type="etf")
        stock_id = self._ticker(symbol="MSFT", name="Microsoft Corp.")
        self._buy(etf_id, 10, 1000)
        self._buy(stock_id, 10, 1000)
        self._price(etf_id, 100)
        self._price(stock_id, 100)
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO etf_details (ticker_id, sector_weights) VALUES (?, ?)",
            [etf_id, json.dumps({"technology": 0.4, "financial_services": 0.6})],
        )
        connection.execute(
            "INSERT INTO stock_details (ticker_id, sector) VALUES (?, 'Technology')", [stock_id]
        )
        holdings = get_holdings(self.db_path)

        result = get_look_through_sector_exposure(self.db_path, holdings)

        self.assertAlmostEqual(result["weights"]["Technology"], 0.7)
        self.assertAlmostEqual(result["weights"]["Financial Services"], 0.3)
        self.assertNotIn("technology", result["weights"])
        self.assertNotIn("financial_services", result["weights"])

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

    def _insert_classification(self, ticker_id, *, primary_group="Quality", review_needed=False):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO portfolio_classifications (
                ticker_id, primary_group, secondary_tags, confidence, reasoning,
                evidence_used, missing_data, review_needed, fields, field_provenance,
                enrichment, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ticker_id,
                primary_group,
                json.dumps(["Equity", "Technology"]),
                "high",
                "Manual override.",
                json.dumps(["manual_override:AAPL"]),
                json.dumps([]),
                review_needed,
                json.dumps(
                    {
                        "sector": "Technology",
                        "expense_ratio": None,
                        # The classifier double-encodes these nested values as
                        # JSON strings inside the fields JSON.
                        "sector_weights": json.dumps({"technology": 0.5, "energy": 0.1}),
                    }
                ),
                json.dumps({"sector": "database"}),
                json.dumps({"mode": "equity"}),
                date(2026, 7, 17),
            ],
        )

    def test_get_classification_details_parses_json_and_joins_ticker(self):
        ticker_id = self._ticker()
        self._insert_classification(ticker_id, review_needed=True)

        result = get_classification_details(self.db_path)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["review_count"], 1)
        row = result["classifications"][0]
        self.assertEqual(row["ticker_symbol"], "AAPL")
        self.assertEqual(row["security_type"], "stock")
        self.assertEqual(row["primary_group"], "Quality")
        # JSON text columns come back as real Python structures.
        self.assertEqual(row["secondary_tags"], ["Equity", "Technology"])
        self.assertEqual(row["fields"]["sector"], "Technology")
        # Nested double-encoded JSON is parsed into a real object.
        self.assertEqual(row["fields"]["sector_weights"], {"technology": 0.5, "energy": 0.1})
        self.assertTrue(row["review_needed"])

    def test_get_classification_details_empty_table(self):
        result = get_classification_details(self.db_path)
        self.assertEqual(result, {"generated_at": None, "count": 0, "review_count": 0, "classifications": []})

    def test_get_price_history_orders_and_filters_by_date(self):
        ticker_id = self._ticker()
        connection = database.get_shared_connection(self.db_path)
        connection.executemany(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [ticker_id, date(2025, 1, 3), 90.0, 95.0, 88.0, 92.0, 91.5, 100],
                [ticker_id, date(2025, 1, 2), 100.0, 105.0, 99.0, 101.0, 100.5, 200],
                [ticker_id, date(2025, 1, 4), 92.0, 96.0, 90.0, 95.0, 94.5, 300],
            ],
        )

        result = get_price_history("AAPL", self.db_path)

        self.assertEqual(result["ticker_symbol"], "AAPL")
        self.assertEqual(result["count"], 3)
        # Ordered oldest-first for direct charting.
        self.assertEqual([p["date"] for p in result["points"]], ["2025-01-02", "2025-01-03", "2025-01-04"])
        self.assertEqual(result["points"][0]["close"], 101.0)
        self.assertEqual(result["points"][0]["adjusted_close"], 100.5)

        filtered = get_price_history("AAPL", self.db_path, date_from=date(2025, 1, 3))
        self.assertEqual([p["date"] for p in filtered["points"]], ["2025-01-03", "2025-01-04"])

    def test_get_price_history_symbol_is_case_insensitive(self):
        self._ticker(symbol="AAPL")
        result = get_price_history("aapl", self.db_path)
        self.assertIsNotNone(result)
        self.assertEqual(result["ticker_symbol"], "AAPL")

    def test_get_price_history_unknown_symbol_returns_none(self):
        self.assertIsNone(get_price_history("NOPE", self.db_path))


class EtfOverlapTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def _etf(self, symbol, name=None):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            """
            INSERT INTO tickers (
                ticker_symbol, exchange, currency, security_name, security_type
            )
            VALUES (?, 'NASDAQ', 'USD', ?, 'etf')
            RETURNING ticker_id
            """,
            [symbol, name or f"{symbol} Fund"],
        ).fetchone()[0]

    def _hold(self, ticker_id, quantity, debit, close):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity,
                execution_date, debit, credit, fx_rate
            ) VALUES (?, 'BUY', ?, ?, ?, ?, NULL, 1)
            """,
            [date(2025, 1, 2), ticker_id, Decimal(str(quantity)), date(2025, 1, 2), Decimal(str(debit))],
        )
        connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 1, 2), close, close, close, close, close, 100],
        )

    def _classify_with_holdings(self, ticker_id, top_holdings, double_encoded=False):
        """Store a classification row whose `fields` carries top_holdings.

        The classifier has written this nested blob both as a real structure and
        as a JSON string, so both are exercised.
        """
        payload = json.dumps(top_holdings) if double_encoded else top_holdings
        database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO portfolio_classifications (
                ticker_id, primary_group, review_needed, fields, generated_at
            ) VALUES (?, 'Core', FALSE, ?, CURRENT_TIMESTAMP)
            """,
            [ticker_id, json.dumps({"top_holdings": payload})],
        )

    def test_overlap_splits_sleeve_into_overlapping_unique_and_unreported(self):
        # Two equally-sized funds: each reports 50% of itself, sharing one name
        # at 30/20. Overlapping = 0.5*0.3 + 0.5*0.2 = 0.25 of the sleeve.
        first = self._etf("AAA")
        second = self._etf("BBB")
        self._hold(first, 10, 1000, 100)
        self._hold(second, 10, 1000, 100)
        self._classify_with_holdings(
            first,
            [
                {"Name": "Broadcom Inc", "Holding Percent": 0.3},
                {"Name": "Solo One Corp", "Holding Percent": 0.2},
            ],
        )
        self._classify_with_holdings(
            second,
            [
                {"Name": "Broadcom Inc", "Holding Percent": 0.2},
                {"Name": "Solo Two Corp", "Holding Percent": 0.3},
            ],
            double_encoded=True,
        )

        result = get_etf_overlap(self.db_path)
        share = result["share"]

        self.assertTrue(result["available"])
        self.assertEqual(result["compared_count"], 2)
        self.assertAlmostEqual(share["overlapping_weight"], 0.25)
        self.assertAlmostEqual(share["unique_weight"], 0.25)
        self.assertAlmostEqual(share["unreported_weight"], 0.5)
        # The three slices always account for the whole sleeve.
        self.assertAlmostEqual(sum(share.values()), 1.0)

    def test_overlap_pair_uses_the_smaller_side_of_each_shared_name(self):
        first = self._etf("AAA")
        second = self._etf("BBB")
        self._hold(first, 10, 1000, 100)
        self._hold(second, 10, 1000, 100)
        self._classify_with_holdings(first, [{"Name": "Broadcom Inc", "Holding Percent": 0.3}])
        self._classify_with_holdings(second, [{"Name": "Broadcom Inc", "Holding Percent": 0.2}])

        pairs = get_etf_overlap(self.db_path)["pairs"]

        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0]["a"], pairs[0]["b"]), ("AAA", "BBB"))
        self.assertAlmostEqual(pairs[0]["overlap_pct"], 0.2)
        self.assertEqual(pairs[0]["shared"][0]["name"], "Broadcom Inc")

    def test_overlap_matches_names_despite_legal_suffix_differences(self):
        first = self._etf("AAA")
        second = self._etf("BBB")
        self._hold(first, 10, 1000, 100)
        self._hold(second, 10, 1000, 100)
        self._classify_with_holdings(first, [{"Name": "The Home Depot Inc", "Holding Percent": 0.4}])
        self._classify_with_holdings(second, [{"Name": "Home Depot", "Holding Percent": 0.4}])

        result = get_etf_overlap(self.db_path)

        self.assertEqual(len(result["top_shared_holdings"]), 1)
        self.assertAlmostEqual(result["share"]["overlapping_weight"], 0.4)

    def test_overlap_unavailable_with_fewer_than_two_reporting_funds(self):
        only = self._etf("AAA")
        bare = self._etf("BBB")
        self._hold(only, 10, 1000, 100)
        self._hold(bare, 10, 1000, 100)
        self._classify_with_holdings(only, [{"Name": "Broadcom Inc", "Holding Percent": 0.4}])

        result = get_etf_overlap(self.db_path)

        self.assertFalse(result["available"])
        self.assertIn("two", result["reason"])
        # The per-fund rows still come back so the UI can explain the gap.
        self.assertEqual({row["ticker_symbol"] for row in result["etfs"]}, {"AAA", "BBB"})

    def test_overlap_unavailable_without_etf_holdings(self):
        result = get_etf_overlap(self.db_path)

        self.assertFalse(result["available"])
        self.assertEqual(result["etf_count"], 0)


if __name__ == "__main__":
    unittest.main()
