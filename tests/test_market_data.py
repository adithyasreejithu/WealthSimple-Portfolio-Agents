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


if __name__ == "__main__":
    unittest.main()
