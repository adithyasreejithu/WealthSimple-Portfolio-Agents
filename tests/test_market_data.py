import unittest
from datetime import date
from unittest.mock import ANY, Mock, patch

import pandas as pd

import market_data


class MarketDataSyncTest(unittest.TestCase):
    def setUp(self):
        self.target = market_data.MarketTarget(
            ticker_id=12,
            symbol="VFV",
            provider_symbol="VFV.TO",
            currency="CAD",
            security_name="Vanguard S&P 500 Index ETF",
            first_owned_date=date(2025, 1, 2),
            latest_market_date=None,
        )

    def _history(self):
        return pd.DataFrame(
            [{
                "Date": "2025-01-02",
                "Ticker": "VFV.TO",
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.5,
                "Adj Close": 100.4,
                "Volume": 1000,
            }]
        )

    def test_initial_sync_uses_owned_date_and_tomorrow_as_exclusive_end(self):
        connection = Mock()
        metadata_fetcher = Mock(return_value=(pd.DataFrame(), pd.DataFrame()))
        history_fetcher = Mock(return_value=self._history())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "ensure_fx_history", return_value=0),
            patch.object(market_data, "ensure_benchmark_history", return_value=0),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata") as metadata_upload,
            patch.object(market_data, "upload_security_history", return_value=1) as upload,
        ):
            result = market_data.sync_market_data(
                "portfolio.duckdb",
                as_of=date(2025, 1, 10),
                metadata_fetcher=metadata_fetcher,
                history_fetcher=history_fetcher,
            )

        history_fetcher.assert_called_once_with(
            ["VFV.TO"], date(2025, 1, 2), date(2025, 1, 11)
        )
        upload.assert_called_once()
        metadata_upload.assert_called_once_with(
            ANY, ANY, "portfolio.duckdb",
            ticker_ids={"VFV.TO": 12},
        )
        self.assertEqual(connection.execute.call_args_list[0].args[0], "BEGIN TRANSACTION")
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "COMMIT")
        self.assertEqual(result.rows, 1)

    def test_incremental_sync_starts_after_latest_market_date(self):
        target = market_data.MarketTarget(
            **{**self.target.__dict__, "latest_market_date": date(2025, 1, 8)}
        )
        connection = Mock()
        history_fetcher = Mock(return_value=self._history())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata"),
            patch.object(market_data, "upload_security_history", return_value=1),
        ):
            market_data.sync_market_data(
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=history_fetcher,
            )

        history_fetcher.assert_called_once_with(
            ["VFV.TO"], date(2025, 1, 9), date(2025, 1, 11)
        )

    def test_full_sync_restarts_at_first_owned_date(self):
        target = market_data.MarketTarget(
            **{**self.target.__dict__, "latest_market_date": date(2025, 1, 8)}
        )
        connection = Mock()
        history_fetcher = Mock(return_value=self._history())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata"),
            patch.object(market_data, "upload_security_history", return_value=1),
        ):
            market_data.sync_market_data(
                full=True,
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=history_fetcher,
            )

        history_fetcher.assert_called_once_with(
            ["VFV.TO"], date(2025, 1, 2), date(2025, 1, 11)
        )

    def test_one_ticker_failure_still_publishes_successful_history(self):
        failed_target = market_data.MarketTarget(
            **{**self.target.__dict__, "ticker_id": 13, "symbol": "BAD", "provider_symbol": "BAD"}
        )
        connection = Mock()
        history_fetcher = Mock(side_effect=[self._history(), RuntimeError("provider failed")])
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target, failed_target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata"),
            patch.object(market_data, "upload_security_history", return_value=1),
        ):
            result = market_data.sync_market_data(
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=history_fetcher,
            )

        self.assertEqual(result.rows, 1)
        self.assertEqual(result.failed_symbols, ("BAD",))
        self.assertFalse(result.succeeded)
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "COMMIT")

    def test_current_ticker_is_skipped(self):
        target = market_data.MarketTarget(
            **{**self.target.__dict__, "latest_market_date": date(2025, 1, 10)}
        )
        connection = Mock()
        history_fetcher = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata"),
        ):
            result = market_data.sync_market_data(
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=history_fetcher,
            )

        history_fetcher.assert_not_called()
        self.assertEqual(result.skipped, 1)

    def test_write_failure_rolls_back(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_security_metadata"),
            patch.object(market_data, "upload_security_history", side_effect=ValueError("bad row")),
        ):
            result = market_data.sync_market_data(
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=Mock(return_value=self._history()),
            )

        self.assertFalse(result.succeeded)
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "ROLLBACK")


class FxHistorySyncTest(unittest.TestCase):
    """Exercise `ensure_fx_history` against a real database (not mocks)."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        import database

        self.database = database
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _fx_frame(self, dates_and_closes):
        return pd.DataFrame(
            [
                {
                    "Date": day, "Ticker": market_data.FX_PAIR_SYMBOL,
                    "Open": close, "High": close, "Low": close,
                    "Close": close, "Adj Close": close, "Volume": 0,
                }
                for day, close in dates_and_closes
            ]
        )

    def test_no_owned_activity_skips_fetch(self):
        history_fetcher = Mock(return_value=pd.DataFrame())
        rows = market_data.ensure_fx_history(self.db_path, history_fetcher=history_fetcher)
        self.assertEqual(rows, 0)
        history_fetcher.assert_not_called()

    def test_creates_fx_ticker_and_backfills_from_earliest_owned_date(self):
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, 1, 100)",
            [date(2025, 1, 2), ticker_id],
        )
        history_fetcher = Mock(
            return_value=self._fx_frame([("2025-01-02", 1.35), ("2025-01-03", 1.36)])
        )

        rows = market_data.ensure_fx_history(
            self.db_path, as_of=date(2025, 1, 10), history_fetcher=history_fetcher
        )

        self.assertEqual(rows, 2)
        history_fetcher.assert_called_once_with(
            [market_data.FX_PAIR_SYMBOL], date(2025, 1, 2), date(2025, 1, 11)
        )
        fx_ticker = self.connection.execute(
            "SELECT exchange, currency, security_type FROM tickers WHERE ticker_symbol = ?",
            [market_data.FX_PAIR_SYMBOL],
        ).fetchone()
        self.assertEqual(fx_ticker, ("FX", "CAD", "fx_rate"))

    def test_second_call_fetches_incrementally_from_latest_stored_date(self):
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, 1, 100)",
            [date(2025, 1, 2), ticker_id],
        )
        first_fetcher = Mock(return_value=self._fx_frame([("2025-01-02", 1.35)]))
        market_data.ensure_fx_history(self.db_path, as_of=date(2025, 1, 5), history_fetcher=first_fetcher)

        second_fetcher = Mock(return_value=self._fx_frame([("2025-01-03", 1.36)]))
        rows = market_data.ensure_fx_history(
            self.db_path, as_of=date(2025, 1, 5), history_fetcher=second_fetcher
        )

        self.assertEqual(rows, 1)
        second_fetcher.assert_called_once_with(
            [market_data.FX_PAIR_SYMBOL], date(2025, 1, 3), date(2025, 1, 6)
        )


class BenchmarkHistorySyncTest(unittest.TestCase):
    """Exercise `ensure_benchmark_history` against a real database (not mocks)."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        import database

        self.database = database
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _frame(self, symbol, dates_and_closes):
        return pd.DataFrame(
            [
                {
                    "Date": day, "Ticker": symbol,
                    "Open": close, "High": close, "Low": close,
                    "Close": close, "Adj Close": close, "Volume": 0,
                }
                for day, close in dates_and_closes
            ]
        )

    def _seed_owned_transaction(self, transaction_date=date(2025, 1, 2)):
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('AAPL', 'NASDAQ', 'USD', 'Apple Inc.', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, 1, 100)",
            [transaction_date, ticker_id],
        )
        return ticker_id

    def _fetcher(self, dates_and_closes):
        def fetch(symbols, start, end):
            return self._frame(symbols[0], dates_and_closes)

        return Mock(side_effect=fetch)

    def test_no_owned_activity_skips_fetch(self):
        history_fetcher = Mock(return_value=pd.DataFrame())
        rows = market_data.ensure_benchmark_history(self.db_path, history_fetcher=history_fetcher)
        self.assertEqual(rows, 0)
        history_fetcher.assert_not_called()

    def test_creates_benchmark_tickers_and_backfills_from_earliest_owned_date(self):
        self._seed_owned_transaction()
        history_fetcher = self._fetcher([("2025-01-02", 30.0), ("2025-01-03", 30.5)])

        rows = market_data.ensure_benchmark_history(
            self.db_path, as_of=date(2025, 1, 10), history_fetcher=history_fetcher
        )

        self.assertEqual(rows, 2 * len(market_data.BENCHMARK_TICKERS))
        fetched_symbols = {call.args[0][0] for call in history_fetcher.call_args_list}
        self.assertEqual(fetched_symbols, set(market_data.BENCHMARK_TICKERS.values()))
        for call in history_fetcher.call_args_list:
            self.assertEqual(call.args[1], date(2025, 1, 2))
            self.assertEqual(call.args[2], date(2025, 1, 11))
        row = self.connection.execute(
            "SELECT exchange, security_type FROM tickers WHERE ticker_symbol = 'VFV'"
        ).fetchone()
        self.assertEqual(row, ("BENCHMARK", "benchmark"))

    def test_reuses_existing_ticker_row_for_owned_benchmark(self):
        self._seed_owned_transaction()
        existing_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('XEQT', 'TSX', 'CAD', 'iShares Core Equity ETF', 'etf') RETURNING ticker_id"""
        ).fetchone()[0]
        history_fetcher = self._fetcher([("2025-01-02", 30.0)])

        market_data.ensure_benchmark_history(
            self.db_path, as_of=date(2025, 1, 10), history_fetcher=history_fetcher
        )

        count = self.connection.execute(
            "SELECT COUNT(*) FROM tickers WHERE UPPER(ticker_symbol) = 'XEQT'"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        stored = self.connection.execute(
            "SELECT COUNT(*) FROM historical_records WHERE ticker_id = ?", [existing_id]
        ).fetchone()[0]
        self.assertEqual(stored, 1)

    def test_backfills_missing_head_range_for_owned_ticker(self):
        self._seed_owned_transaction(date(2025, 1, 2))
        existing_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('XEQT', 'TSX', 'CAD', 'iShares Core Equity ETF', 'etf') RETURNING ticker_id"""
        ).fetchone()[0]
        # Owned-ticker sync started at first ownership, well after the
        # portfolio's earliest transaction.
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, 30, 30, 30, 30, 30, 0)",
            [existing_id, date(2025, 3, 1)],
        )
        history_fetcher = self._fetcher([("2025-01-02", 29.0)])

        market_data.ensure_benchmark_history(
            self.db_path, as_of=date(2025, 3, 5), history_fetcher=history_fetcher
        )

        xeqt_calls = [c for c in history_fetcher.call_args_list if c.args[0] == ["XEQT.TO"]]
        self.assertEqual(
            [(c.args[1], c.args[2]) for c in xeqt_calls],
            [(date(2025, 1, 2), date(2025, 3, 1)), (date(2025, 3, 2), date(2025, 3, 6))],
        )


if __name__ == "__main__":
    unittest.main()
