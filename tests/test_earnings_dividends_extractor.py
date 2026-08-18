import unittest
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

import pandas as pd

import earnings_dividends_extractor as ede


class FakeTicker:
    def __init__(self, earnings_dates=None, dividends=None, calendar=None, raises=False):
        self._earnings_dates = earnings_dates
        self._dividends = dividends
        self._calendar = calendar
        self._raises = raises

    @property
    def earnings_dates(self):
        if self._raises:
            raise RuntimeError("provider failure")
        return self._earnings_dates

    @property
    def dividends(self):
        if self._raises:
            raise RuntimeError("provider failure")
        return self._dividends

    @property
    def calendar(self):
        if self._raises:
            raise RuntimeError("provider failure")
        return self._calendar


class FakeYFinance:
    def __init__(self, tickers):
        self._tickers = tickers
        self.ticker_calls = []

    def Ticker(self, symbol, session=None):
        self.ticker_calls.append((symbol, session))
        return self._tickers[symbol]


def _earnings_frame():
    return pd.DataFrame(
        {
            "EPS Estimate": [1.20, 1.55],
            "Reported EPS": [1.30, float("nan")],
            "Surprise(%)": [5.54, float("nan")],
        },
        index=pd.DatetimeIndex(["2025-01-30", "2025-04-30"], name="Earnings Date"),
    )


class EarningsDividendsExtractorTest(unittest.TestCase):
    def _run(self, fetcher, tickers, fake_yf):
        with (
            patch.object(ede, "_require_yfinance", return_value=fake_yf),
            patch.object(ede, "_build_session", return_value=None),
        ):
            return fetcher(tickers)

    def test_fetch_earnings_events_maps_fields_and_columns(self):
        fake_yf = FakeYFinance({"AAPL": FakeTicker(earnings_dates=_earnings_frame())})
        data = self._run(ede.fetch_earnings_events, ["AAPL"], fake_yf)

        self.assertEqual(list(data.columns), ede.EARNINGS_EVENT_COLUMNS)
        self.assertEqual(len(data), 2)
        self.assertEqual(data.loc[0, "Ticker"], "AAPL")
        self.assertEqual(data.loc[0, "ProviderSymbol"], "AAPL")
        self.assertEqual(pd.Timestamp(data.loc[0, "ReportDate"]).date().isoformat(), "2025-01-30")
        self.assertEqual(data.loc[0, "EpsEstimate"], 1.20)
        self.assertEqual(data.loc[0, "EpsActual"], 1.30)
        # Surprise(%) is already in percentage points -- stored as returned.
        self.assertEqual(data.loc[0, "SurprisePct"], 5.54)
        # The trailing upcoming estimate keeps NaN actual/surprise.
        self.assertTrue(pd.isna(data.loc[1, "EpsActual"]))

    def test_fetch_dividend_events_maps_fields_and_columns(self):
        series = pd.Series(
            [0.22, 0.24],
            index=pd.DatetimeIndex(["2025-02-07", "2025-05-07"], name="Date"),
        )
        fake_yf = FakeYFinance({"CDZ.TO": FakeTicker(dividends=series)})
        data = self._run(ede.fetch_dividend_events, ["CDZ.TO"], fake_yf)

        self.assertEqual(list(data.columns), ede.DIVIDEND_EVENT_COLUMNS)
        self.assertEqual(len(data), 2)
        self.assertEqual(data.loc[0, "ProviderSymbol"], "CDZ.TO")
        self.assertEqual(pd.Timestamp(data.loc[0, "ExDividendDate"]).date().isoformat(), "2025-02-07")
        self.assertEqual(data.loc[0, "DeclaredAmount"], 0.22)

    def test_empty_earnings_dates_is_normal_not_a_failure(self):
        # An ETF returns an empty earnings_dates; the other ticker still maps.
        fake_yf = FakeYFinance(
            {
                "CDZ.TO": FakeTicker(earnings_dates=pd.DataFrame()),
                "AAPL": FakeTicker(earnings_dates=_earnings_frame()),
            }
        )
        data = self._run(ede.fetch_earnings_events, ["CDZ.TO", "AAPL"], fake_yf)

        self.assertEqual(set(data["Ticker"]), {"AAPL"})
        self.assertEqual(len(data), 2)

    def test_none_earnings_dates_is_handled(self):
        fake_yf = FakeYFinance({"CDZ.TO": FakeTicker(earnings_dates=None)})
        data = self._run(ede.fetch_earnings_events, ["CDZ.TO"], fake_yf)
        self.assertTrue(data.empty)
        self.assertEqual(list(data.columns), ede.EARNINGS_EVENT_COLUMNS)

    def test_per_ticker_exception_isolation(self):
        fake_yf = FakeYFinance(
            {
                "BAD": FakeTicker(raises=True),
                "AAPL": FakeTicker(earnings_dates=_earnings_frame()),
            }
        )
        data = self._run(ede.fetch_earnings_events, ["BAD", "AAPL"], fake_yf)
        self.assertEqual(set(data["Ticker"]), {"AAPL"})

    def test_empty_input_returns_stable_frames(self):
        fake_yf = FakeYFinance({})
        earnings = self._run(ede.fetch_earnings_events, ["", None], fake_yf)
        dividends = self._run(ede.fetch_dividend_events, [], fake_yf)
        self.assertTrue(earnings.empty)
        self.assertTrue(dividends.empty)
        self.assertEqual(list(earnings.columns), ede.EARNINGS_EVENT_COLUMNS)
        self.assertEqual(list(dividends.columns), ede.DIVIDEND_EVENT_COLUMNS)

    def test_parse_args_rejects_skipping_all_three(self):
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
            ede.parse_args(
                ["--tickers", "AAPL", "--skip-earnings", "--skip-dividends", "--skip-upcoming"]
            )

    def test_parse_args_allows_skipping_two_of_three(self):
        # Only skipping earnings and historical dividends is legitimate now --
        # the upcoming-dividend fetch alone is a real, standalone fetch.
        args = ede.parse_args(["--tickers", "AAPL", "--skip-earnings", "--skip-dividends"])
        self.assertTrue(args.skip_earnings)
        self.assertTrue(args.skip_dividends)
        self.assertFalse(args.skip_upcoming)

    def test_main_prints_frames(self):
        earnings = pd.DataFrame(
            [{"Ticker": "AAPL", "ProviderSymbol": "AAPL", "ReportDate": "2025-01-30",
              "EpsEstimate": 1.2, "EpsActual": 1.3, "SurprisePct": 5.54}],
            columns=ede.EARNINGS_EVENT_COLUMNS,
        )
        dividends = pd.DataFrame(columns=ede.DIVIDEND_EVENT_COLUMNS)
        upcoming = pd.DataFrame(
            [{"Ticker": "AAPL", "ProviderSymbol": "AAPL", "ExDividendDate": "2025-06-01",
              "PayDate": "2025-06-15", "DeclaredAmount": 0.24, "Frequency": "quarterly"}],
            columns=ede.UPCOMING_DIVIDEND_COLUMNS,
        )
        with (
            patch.object(ede, "configure_yfinance_cache") as cache,
            patch.object(ede, "clear_proxy_environment") as clear_proxy,
            patch.object(ede, "fetch_earnings_events", return_value=earnings) as fetch_e,
            patch.object(ede, "fetch_dividend_events", return_value=dividends) as fetch_d,
            patch.object(ede, "fetch_upcoming_dividends", return_value=upcoming) as fetch_u,
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            ede.main(["--tickers", "AAPL", "--ignore-proxy"])

        clear_proxy.assert_called_once()
        cache.assert_called_once()
        fetch_e.assert_called_once_with(["AAPL"])
        fetch_d.assert_called_once_with(["AAPL"])
        fetch_u.assert_called_once_with(["AAPL"])
        self.assertIn("EARNINGS", output.getvalue())
        self.assertIn("DIVIDENDS", output.getvalue())
        self.assertIn("UPCOMING DIVIDENDS", output.getvalue())

    def test_fetch_upcoming_dividends_projects_amount_and_infers_frequency(self):
        next_ex = date.today() + timedelta(days=30)
        pay = next_ex + timedelta(days=14)
        history = pd.Series(
            [0.20, 0.22, 0.24, 0.26],
            index=pd.DatetimeIndex(
                [
                    date.today() - timedelta(days=270),
                    date.today() - timedelta(days=180),
                    date.today() - timedelta(days=90),
                    date.today() - timedelta(days=1),
                ]
            ),
        )
        fake_yf = FakeYFinance(
            {"AAPL": FakeTicker(calendar={"Ex-Dividend Date": next_ex, "Dividend Date": pay}, dividends=history)}
        )

        data = self._run(ede.fetch_upcoming_dividends, ["AAPL"], fake_yf)

        self.assertEqual(list(data.columns), ede.UPCOMING_DIVIDEND_COLUMNS)
        self.assertEqual(len(data), 1)
        row = data.iloc[0]
        self.assertEqual(row["ProviderSymbol"], "AAPL")
        self.assertEqual(row["ExDividendDate"], next_ex)
        self.assertEqual(row["PayDate"], pay)
        # Projected from the most recent historical payment, not a newly
        # declared amount -- there is no forward amount field in `calendar`.
        self.assertEqual(row["DeclaredAmount"], 0.26)
        self.assertEqual(row["Frequency"], "quarterly")  # ~90-day spacing between the last few ex-dates

    def test_fetch_upcoming_dividends_empty_calendar_is_normal(self):
        # Expected for many ETFs/funds, same as an empty earnings_dates.
        fake_yf = FakeYFinance({"XEQT.TO": FakeTicker(calendar={})})
        data = self._run(ede.fetch_upcoming_dividends, ["XEQT.TO"], fake_yf)
        self.assertTrue(data.empty)
        self.assertEqual(list(data.columns), ede.UPCOMING_DIVIDEND_COLUMNS)

    def test_fetch_upcoming_dividends_past_calendar_date_is_skipped(self):
        fake_yf = FakeYFinance(
            {"AAPL": FakeTicker(calendar={"Ex-Dividend Date": date.today() - timedelta(days=5)})}
        )
        data = self._run(ede.fetch_upcoming_dividends, ["AAPL"], fake_yf)
        self.assertTrue(data.empty)

    def test_fetch_upcoming_dividends_no_history_to_project_from(self):
        fake_yf = FakeYFinance(
            {
                "AAPL": FakeTicker(
                    calendar={"Ex-Dividend Date": date.today() + timedelta(days=10)},
                    dividends=pd.Series([], dtype=float),
                )
            }
        )
        data = self._run(ede.fetch_upcoming_dividends, ["AAPL"], fake_yf)
        self.assertTrue(data.empty)

    def test_fetch_upcoming_dividends_per_ticker_exception_isolation(self):
        fake_yf = FakeYFinance(
            {
                "BAD": FakeTicker(raises=True),
                "AAPL": FakeTicker(
                    calendar={"Ex-Dividend Date": date.today() + timedelta(days=10)},
                    dividends=pd.Series([0.5], index=pd.DatetimeIndex([date.today() - timedelta(days=90)])),
                ),
            }
        )
        data = self._run(ede.fetch_upcoming_dividends, ["BAD", "AAPL"], fake_yf)
        self.assertEqual(set(data["Ticker"]), {"AAPL"})

    def test_infer_dividend_frequency_buckets_correctly(self):
        base = date(2025, 1, 1)
        monthly = [base, base + timedelta(days=30), base + timedelta(days=61)]
        quarterly = [base, base + timedelta(days=91), base + timedelta(days=182)]
        annual = [base, base + timedelta(days=365)]
        self.assertEqual(ede._infer_dividend_frequency(monthly), "monthly")
        self.assertEqual(ede._infer_dividend_frequency(quarterly), "quarterly")
        self.assertEqual(ede._infer_dividend_frequency(annual), "annual")
        self.assertIsNone(ede._infer_dividend_frequency([base]))
        self.assertIsNone(ede._infer_dividend_frequency([]))


if __name__ == "__main__":
    unittest.main()
