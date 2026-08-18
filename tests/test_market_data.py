import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import pandas as pd

import database
import market_data


class MarketDataSyncTest(unittest.TestCase):
    def setUp(self):
        # Shorten the history floor so the expected dates below stay readable;
        # the production 400-day value is exercised in MarketTargetRangeTest.
        minimum = patch.object(market_data, "MINIMUM_PRICE_HISTORY_DAYS", 30)
        minimum.start()
        self.addCleanup(minimum.stop)
        # first_owned 2025-01-02 with as_of 2025-01-10 puts the floor at
        # 2024-12-11 -- earlier than first ownership, so it governs.
        self.target = market_data.MarketTarget(
            ticker_id=12,
            symbol="VFV",
            provider_symbol="VFV.TO",
            currency="CAD",
            security_name="Vanguard S&P 500 Index ETF",
            first_owned_date=date(2025, 1, 2),
            earliest_market_date=None,
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

    def test_initial_sync_uses_history_floor_and_tomorrow_as_exclusive_end(self):
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
            ["VFV.TO"], date(2024, 12, 11), date(2025, 1, 11)
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
        # Stored history already reaches below the floor, so only the tail runs.
        target = market_data.MarketTarget(
            **{
                **self.target.__dict__,
                "earliest_market_date": date(2024, 12, 1),
                "latest_market_date": date(2025, 1, 8),
            }
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

    def test_full_sync_restarts_at_history_floor(self):
        target = market_data.MarketTarget(
            **{
                **self.target.__dict__,
                "earliest_market_date": date(2025, 1, 2),
                "latest_market_date": date(2025, 1, 8),
            }
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
            ["VFV.TO"], date(2024, 12, 11), date(2025, 1, 11)
        )

    def test_shallow_ticker_fetches_head_gap_then_tail(self):
        # Bought recently: stored history starts well after the floor, so the
        # missing head is requested before the usual incremental tail.
        target = market_data.MarketTarget(
            **{
                **self.target.__dict__,
                "earliest_market_date": date(2025, 1, 5),
                "latest_market_date": date(2025, 1, 8),
            }
        )
        history_fetcher = Mock(return_value=self._history())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[target]),
            patch.object(market_data, "get_shared_connection", return_value=Mock()),
            patch.object(market_data, "upload_security_metadata"),
            patch.object(market_data, "upload_security_history", return_value=2),
        ):
            market_data.sync_market_data(
                as_of=date(2025, 1, 10),
                metadata_fetcher=Mock(return_value=(pd.DataFrame(), pd.DataFrame())),
                history_fetcher=history_fetcher,
            )

        self.assertEqual(
            [call.args for call in history_fetcher.call_args_list],
            [
                (["VFV.TO"], date(2024, 12, 11), date(2025, 1, 5)),
                (["VFV.TO"], date(2025, 1, 9), date(2025, 1, 11)),
            ],
        )

    def test_deep_ticker_fetches_only_the_tail(self):
        # Held long enough that stored history already predates the floor.
        target = market_data.MarketTarget(
            **{
                **self.target.__dict__,
                "earliest_market_date": date(2024, 11, 1),
                "latest_market_date": date(2025, 1, 8),
            }
        )
        history_fetcher = Mock(return_value=self._history())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[target]),
            patch.object(market_data, "get_shared_connection", return_value=Mock()),
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
        # Backfilled past the floor and current through today: nothing to plan.
        target = market_data.MarketTarget(
            **{
                **self.target.__dict__,
                "earliest_market_date": date(2024, 12, 1),
                "latest_market_date": date(2025, 1, 10),
            }
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


class StaleSymbolsTest(unittest.TestCase):
    """The staleness-gate helpers app.py's market-data refresh stage uses to
    decide which owned symbols are due for an earnings/dividends/financial-
    snapshots refresh (see config.py's *_REFRESH_INTERVAL_DAYS constants)."""

    def setUp(self):
        self.today = date(2026, 8, 13)
        self.targets = [
            market_data.MarketTarget(
                ticker_id=1, symbol="AAPL", provider_symbol="AAPL", currency="USD",
                security_name="Apple Inc.", first_owned_date=date(2020, 1, 1),
                earliest_market_date=None, latest_market_date=None,
            ),
            market_data.MarketTarget(
                ticker_id=2, symbol="ENB", provider_symbol="ENB.TO", currency="CAD",
                security_name="Enbridge Inc.", first_owned_date=date(2020, 1, 1),
                earliest_market_date=None, latest_market_date=None,
            ),
            market_data.MarketTarget(
                ticker_id=3, symbol="LYTE", provider_symbol="LYTE", currency="USD",
                security_name="Roundhill Photonics & Optics ETF", first_owned_date=date(2026, 8, 10),
                earliest_market_date=None, latest_market_date=None, asset_class="etf",
            ),
        ]

    def _connection(self, rows):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = rows
        return connection

    def test_ticker_with_no_row_at_all_is_stale(self):
        # AAPL/ENB fetched today; LYTE has never been fetched (no row).
        connection = self._connection([(1, datetime(2026, 8, 13)), (2, datetime(2026, 8, 13))])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data._stale_symbols(
                "db.duckdb", self.targets, "earnings_events", 1, as_of=self.today
            )

        self.assertEqual(stale, ["LYTE"])

    def test_ticker_older_than_max_age_is_stale(self):
        connection = self._connection([
            (1, datetime(2026, 8, 13)),
            (2, datetime(2026, 8, 1)),
            (3, datetime(2026, 8, 13)),
        ])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data._stale_symbols(
                "db.duckdb", self.targets, "dividend_events", 7, as_of=self.today
            )

        self.assertEqual(stale, ["ENB"])

    def test_all_fresh_returns_empty_list(self):
        fetched_at = datetime.combine(self.today, datetime.min.time())
        connection = self._connection([(1, fetched_at), (2, fetched_at), (3, fetched_at)])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data._stale_symbols(
                "db.duckdb", self.targets, "financial_snapshots", 30, as_of=self.today
            )

        self.assertEqual(stale, [])

    def test_empty_targets_short_circuits_without_querying(self):
        with patch.object(market_data, "get_shared_connection") as get_connection:
            stale = market_data._stale_symbols("db.duckdb", [], "earnings_events", 1)

        self.assertEqual(stale, [])
        get_connection.assert_not_called()

    def test_wrapper_functions_use_the_correct_table_and_default_interval(self):
        connection = self._connection([])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            market_data.stale_earnings_symbols("db.duckdb", self.targets, as_of=self.today)
            market_data.stale_dividend_symbols("db.duckdb", self.targets, as_of=self.today)
            market_data.stale_financial_snapshot_symbols("db.duckdb", self.targets, as_of=self.today)

        queries = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("FROM earnings_events" in q for q in queries))
        self.assertTrue(any("FROM dividend_events" in q for q in queries))
        self.assertTrue(any("FROM financial_snapshots" in q for q in queries))

    def test_earnings_gate_excludes_etfs_even_when_never_fetched(self):
        """An ETF never gets an earnings_events row (yfinance has no earnings
        date for a fund) -- without this exclusion, `_stale_symbols`'s
        no-row-means-stale rule would mark every ETF permanently stale and
        re-fetch it on every single pipeline run."""
        connection = self._connection([])  # nothing fetched for anyone, ever
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data.stale_earnings_symbols("db.duckdb", self.targets, as_of=self.today)

        self.assertEqual(set(stale), {"AAPL", "ENB"})
        self.assertNotIn("LYTE", stale)

    def test_financial_snapshots_gate_excludes_etfs_even_when_never_fetched(self):
        connection = self._connection([])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data.stale_financial_snapshot_symbols(
                "db.duckdb", self.targets, as_of=self.today
            )

        self.assertEqual(set(stale), {"AAPL", "ENB"})
        self.assertNotIn("LYTE", stale)

    def test_dividend_gate_does_not_exclude_etfs(self):
        """Unlike earnings/financials, ETFs do pay dividends (DGRO, SCHD,
        XEQT, ...), so the dividend gate must not exclude them the way the
        other two do."""
        connection = self._connection([])
        with patch.object(market_data, "get_shared_connection", return_value=connection):
            stale = market_data.stale_dividend_symbols("db.duckdb", self.targets, as_of=self.today)

        self.assertIn("LYTE", stale)


class MarketTargetRangeTest(unittest.TestCase):
    """Range planning against the real `MINIMUM_PRICE_HISTORY_DAYS`."""

    def _target(self, **overrides):
        base = {
            "ticker_id": 1,
            "symbol": "SOFI",
            "provider_symbol": "SOFI",
            "currency": "USD",
            "security_name": "SoFi Technologies, Inc.",
            "first_owned_date": date(2024, 12, 20),
            "earliest_market_date": None,
            "latest_market_date": None,
        }
        return market_data.MarketTarget(**{**base, **overrides})

    def test_floor_reaches_back_the_configured_minimum(self):
        self.assertEqual(
            self._target().history_floor(date(2025, 1, 10)), date(2023, 12, 7)
        )

    def test_floor_never_moves_forward_past_first_ownership(self):
        target = self._target(first_owned_date=date(2020, 5, 1))
        self.assertEqual(target.history_floor(date(2025, 1, 10)), date(2020, 5, 1))

    def test_head_gap_is_fetched_only_once(self):
        # A first sync pulls the whole floor-to-today window ...
        first = self._target().fetch_ranges(date(2025, 1, 10))
        self.assertEqual(first, [(date(2023, 12, 7), date(2025, 1, 11))])

        # ... and once stored, the floor has advanced past the stored minimum
        # so only the tail remains (2025-01-10 is a Friday, 2025-01-13 a Monday).
        second = self._target(
            earliest_market_date=date(2023, 12, 7),
            latest_market_date=date(2025, 1, 10),
        ).fetch_ranges(date(2025, 1, 13))
        self.assertEqual(second, [(date(2025, 1, 11), date(2025, 1, 14))])

    def test_weekend_only_range_is_not_requested(self):
        # Syncing on a Saturday with Friday's bar already stored leaves a
        # Sat-Sun range that cannot hold data; asking for it makes yfinance log
        # a misleading "possibly delisted" error.
        target = self._target(
            earliest_market_date=date(2023, 12, 7),
            latest_market_date=date(2025, 1, 10),
        )
        self.assertEqual(target.fetch_ranges(date(2025, 1, 11)), [])


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


class EarningsDividendsSyncTest(unittest.TestCase):
    def setUp(self):
        self.target = market_data.MarketTarget(
            ticker_id=7,
            symbol="AAPL",
            provider_symbol="AAPL",
            currency="USD",
            security_name="Apple Inc.",
            first_owned_date=date(2025, 1, 2),
            earliest_market_date=None,
            latest_market_date=None,
        )

    def _earnings_frame(self):
        return pd.DataFrame([{"Ticker": "AAPL", "ProviderSymbol": "AAPL", "ReportDate": "2025-01-30",
                              "EpsEstimate": 1.2, "EpsActual": 1.3, "SurprisePct": 5.5}])

    def _dividend_frame(self):
        return pd.DataFrame([{"Ticker": "AAPL", "ProviderSymbol": "AAPL",
                              "ExDividendDate": "2025-02-07", "DeclaredAmount": 0.24}])

    def _upcoming_frame(self):
        return pd.DataFrame([{"Ticker": "AAPL", "ProviderSymbol": "AAPL",
                              "ExDividendDate": "2025-06-01", "PayDate": "2025-06-15",
                              "DeclaredAmount": 0.26, "Frequency": "quarterly"}])

    def test_sync_fetches_owned_scope_and_writes_in_one_transaction(self):
        connection = Mock()
        earnings_fetcher = Mock(return_value=self._earnings_frame())
        dividends_fetcher = Mock(return_value=self._dividend_frame())
        upcoming_fetcher = Mock(return_value=self._upcoming_frame())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_earnings_events", return_value=1) as upload_e,
            patch.object(market_data, "upload_dividend_events", return_value=1) as upload_d,
            patch.object(market_data, "upload_upcoming_dividends", return_value=1) as upload_u,
        ):
            result = market_data.sync_earnings_dividends(
                "portfolio.duckdb",
                earnings_fetcher=earnings_fetcher,
                dividends_fetcher=dividends_fetcher,
                upcoming_dividends_fetcher=upcoming_fetcher,
            )

        earnings_fetcher.assert_called_once_with(["AAPL"])
        dividends_fetcher.assert_called_once_with(["AAPL"])
        upcoming_fetcher.assert_called_once_with(["AAPL"])
        upload_e.assert_called_once()
        upload_d.assert_called_once()
        upload_u.assert_called_once()
        self.assertEqual(connection.execute.call_args_list[0].args[0], "BEGIN TRANSACTION")
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "COMMIT")
        self.assertEqual(
            (result.earnings_rows, result.dividend_rows, result.upcoming_dividend_rows), (1, 1, 1)
        )
        self.assertTrue(result.succeeded)

    def test_sync_with_no_targets_returns_empty_without_writing(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
        ):
            result = market_data.sync_earnings_dividends(
                earnings_fetcher=Mock(),
                dividends_fetcher=Mock(),
                upcoming_dividends_fetcher=Mock(),
            )

        self.assertEqual(
            (result.tickers, result.earnings_rows, result.dividend_rows, result.upcoming_dividend_rows),
            (0, 0, 0, 0),
        )
        connection.execute.assert_not_called()

    def test_sync_skip_flags_bypass_the_respective_fetcher(self):
        connection = Mock()
        earnings_fetcher = Mock(return_value=self._earnings_frame())
        dividends_fetcher = Mock(return_value=self._dividend_frame())
        upcoming_fetcher = Mock(return_value=self._upcoming_frame())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_earnings_events", return_value=1),
            patch.object(market_data, "upload_dividend_events", return_value=1) as upload_d,
            patch.object(market_data, "upload_upcoming_dividends", return_value=1) as upload_u,
        ):
            market_data.sync_earnings_dividends(
                earnings_fetcher=earnings_fetcher,
                dividends_fetcher=dividends_fetcher,
                upcoming_dividends_fetcher=upcoming_fetcher,
                skip_dividends=True,
                skip_upcoming=True,
            )

        earnings_fetcher.assert_called_once_with(["AAPL"])
        dividends_fetcher.assert_not_called()
        upcoming_fetcher.assert_not_called()
        upload_d.assert_not_called()
        upload_u.assert_not_called()

    def test_sync_fetch_failure_reports_error(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_earnings_events"),
            patch.object(market_data, "upload_dividend_events"),
            patch.object(market_data, "upload_upcoming_dividends"),
        ):
            result = market_data.sync_earnings_dividends(
                earnings_fetcher=Mock(side_effect=RuntimeError("provider failed")),
                dividends_fetcher=Mock(return_value=self._dividend_frame()),
                upcoming_dividends_fetcher=Mock(return_value=self._upcoming_frame()),
            )

        self.assertFalse(result.succeeded)
        self.assertIn("provider failed", result.error)
        connection.execute.assert_not_called()

    def test_sync_write_failure_rolls_back(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_earnings_events", side_effect=ValueError("bad row")),
            patch.object(market_data, "upload_dividend_events", return_value=1),
            patch.object(market_data, "upload_upcoming_dividends", return_value=1),
        ):
            result = market_data.sync_earnings_dividends(
                earnings_fetcher=Mock(return_value=self._earnings_frame()),
                dividends_fetcher=Mock(return_value=self._dividend_frame()),
                upcoming_dividends_fetcher=Mock(return_value=self._upcoming_frame()),
            )

        self.assertFalse(result.succeeded)
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "ROLLBACK")


class FinancialSnapshotsSyncTest(unittest.TestCase):
    def setUp(self):
        self.target = market_data.MarketTarget(
            ticker_id=7,
            symbol="AAPL",
            provider_symbol="AAPL",
            currency="USD",
            security_name="Apple Inc.",
            first_owned_date=date(2025, 1, 2),
            earliest_market_date=None,
            latest_market_date=None,
        )

    def _snapshots_frame(self):
        return pd.DataFrame([{
            "Ticker": "AAPL", "ProviderSymbol": "AAPL", "PeriodEndDate": "2026-03-31",
            "Revenue": 1000.0, "NetIncome": 200.0, "Eps": 1.5, "GrossMargin": 0.4,
            "OperatingMargin": 0.3, "DebtToEquity": 2.0, "CurrentRatio": 1.8,
            "FreeCashFlow": 150.0, "Extra": {},
        }])

    def test_sync_fetches_owned_scope_and_writes_in_one_transaction(self):
        connection = Mock()
        snapshots_fetcher = Mock(return_value=self._snapshots_frame())
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_financial_snapshots", return_value=1) as upload,
        ):
            result = market_data.sync_financial_snapshots(
                "portfolio.duckdb", snapshots_fetcher=snapshots_fetcher,
            )

        snapshots_fetcher.assert_called_once_with(["AAPL"])
        upload.assert_called_once()
        self.assertEqual(connection.execute.call_args_list[0].args[0], "BEGIN TRANSACTION")
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "COMMIT")
        self.assertEqual(result.snapshot_rows, 1)
        self.assertTrue(result.succeeded)

    def test_sync_with_no_targets_returns_empty_without_writing(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
        ):
            result = market_data.sync_financial_snapshots(snapshots_fetcher=Mock())

        self.assertEqual((result.tickers, result.snapshot_rows), (0, 0))
        connection.execute.assert_not_called()

    def test_sync_fetch_failure_reports_error_and_isolates_per_ticker(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_financial_snapshots"),
        ):
            result = market_data.sync_financial_snapshots(
                snapshots_fetcher=Mock(side_effect=RuntimeError("provider failed")),
            )

        self.assertFalse(result.succeeded)
        self.assertIn("provider failed", result.error)
        connection.execute.assert_not_called()

    def test_sync_write_failure_rolls_back(self):
        connection = Mock()
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[self.target]),
            patch.object(market_data, "get_shared_connection", return_value=connection),
            patch.object(market_data, "upload_financial_snapshots", side_effect=ValueError("bad row")),
        ):
            result = market_data.sync_financial_snapshots(
                snapshots_fetcher=Mock(return_value=self._snapshots_frame()),
            )

        self.assertFalse(result.succeeded)
        self.assertEqual(connection.execute.call_args_list[-1].args[0], "ROLLBACK")


class GetMarketTargetsScopeTest(unittest.TestCase):
    """Real fixture DB (not mocked) -- exercises the actual `owned_dates` /
    `security_status` LEFT JOIN in `get_market_targets`, not just callers
    that mock the function out entirely."""

    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _seed_ticker(self, symbol, *, owned=False, declared_status=None):
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES (?, 'NASDAQ', 'USD', ?, 'stock') RETURNING ticker_id""",
            [symbol, f"{symbol} Inc."],
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, verification_status)
               VALUES (?, 'yahoo', ?, 'verified')""",
            [ticker_id, symbol],
        )
        if owned:
            self.connection.execute(
                "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
                "VALUES (?, 'BUY', ?, 10, 1000)",
                [date(2025, 1, 2), ticker_id],
            )
        if declared_status:
            self.connection.execute(
                """INSERT INTO security_status (ticker_id, declared_status, rationale, declared_at, declared_by)
                   VALUES (?, ?, NULL, CURRENT_TIMESTAMP, 'test')""",
                [ticker_id, declared_status],
            )
        return ticker_id

    def test_owned_only_by_default(self):
        self._seed_ticker("OWNED", owned=True)
        self._seed_ticker("WISH", declared_status="wishlist")
        targets = market_data.get_market_targets(self.db_path)
        symbols = {t.symbol for t in targets}
        self.assertEqual(symbols, {"OWNED"})
        self.assertEqual(targets[0].scope, "portfolio")

    def test_include_research_admits_declared_wishlist(self):
        self._seed_ticker("OWNED", owned=True)
        self._seed_ticker("WISH", declared_status="wishlist")
        targets = market_data.get_market_targets(self.db_path, include_research=True)
        by_symbol = {t.symbol: t for t in targets}
        self.assertEqual(set(by_symbol), {"OWNED", "WISH"})
        self.assertEqual(by_symbol["OWNED"].scope, "portfolio")
        self.assertEqual(by_symbol["WISH"].scope, "research")
        self.assertIsNone(by_symbol["WISH"].first_owned_date)

    def test_avoid_and_retired_excluded_even_with_include_research(self):
        self._seed_ticker("AVOID", declared_status="avoid")
        self._seed_ticker("RETIRED", declared_status="retired")
        targets = market_data.get_market_targets(self.db_path, include_research=True)
        self.assertEqual(targets, [])

    def test_undeclared_unowned_ticker_excluded_even_with_include_research(self):
        self._seed_ticker("NOSTATUS")
        targets = market_data.get_market_targets(self.db_path, include_research=True)
        self.assertEqual(targets, [])

    def test_symbols_filter_still_applies_under_include_research(self):
        self._seed_ticker("OWNED", owned=True)
        self._seed_ticker("WISH", declared_status="wishlist")
        targets = market_data.get_market_targets(
            self.db_path, symbols=["WISH"], include_research=True
        )
        self.assertEqual([t.symbol for t in targets], ["WISH"])


class MarketTargetHistoryFloorNoOwnershipTest(unittest.TestCase):
    def test_history_floor_falls_back_to_minimum_history_when_never_owned(self):
        target = market_data.MarketTarget(
            ticker_id=1, symbol="WISH", provider_symbol="WISH", currency="USD",
            security_name="Wish Co", first_owned_date=None,
            earliest_market_date=None, latest_market_date=None, scope="research",
        )
        with patch.object(market_data, "MINIMUM_PRICE_HISTORY_DAYS", 30):
            floor = target.history_floor(date(2026, 8, 14))
        self.assertEqual(floor, date(2026, 7, 15))


class SyncFunctionsIncludeResearchForwardingTest(unittest.TestCase):
    """Every sync function defaults to owned-only and forwards `include_research`
    verbatim to `get_market_targets` -- `pipeline` never passes it, so this is
    what keeps routine syncs byte-for-byte unaffected by the research widening."""

    def test_sync_market_data_defaults_to_owned_only(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "ensure_fx_history", return_value=0),
            patch.object(market_data, "ensure_benchmark_history", return_value=0),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_market_data("portfolio.duckdb")
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=False)

    def test_sync_market_data_widens_with_include_research(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "ensure_fx_history", return_value=0),
            patch.object(market_data, "ensure_benchmark_history", return_value=0),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_market_data("portfolio.duckdb", include_research=True)
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=True)

    def test_sync_earnings_dividends_defaults_to_owned_only(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_earnings_dividends("portfolio.duckdb")
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=False)

    def test_sync_earnings_dividends_widens_with_include_research(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_earnings_dividends("portfolio.duckdb", include_research=True)
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=True)

    def test_sync_financial_snapshots_defaults_to_owned_only(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_financial_snapshots("portfolio.duckdb")
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=False)

    def test_sync_financial_snapshots_widens_with_include_research(self):
        with (
            patch.object(market_data, "initialize_database"),
            patch.object(market_data, "get_market_targets", return_value=[]) as get_targets,
        ):
            market_data.sync_financial_snapshots("portfolio.duckdb", include_research=True)
        get_targets.assert_called_once_with("portfolio.duckdb", None, include_research=True)


if __name__ == "__main__":
    unittest.main()
