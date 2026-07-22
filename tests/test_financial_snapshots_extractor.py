import unittest
from io import StringIO
from unittest.mock import patch

import pandas as pd

import financial_snapshots_extractor as fse


class FakeTicker:
    def __init__(
        self,
        quarterly_income_stmt=None,
        quarterly_balance_sheet=None,
        quarterly_cashflow=None,
        raises_on=None,
    ):
        self._income = quarterly_income_stmt
        self._balance = quarterly_balance_sheet
        self._cashflow = quarterly_cashflow
        self._raises_on = raises_on or set()

    @property
    def quarterly_income_stmt(self):
        if "quarterly_income_stmt" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._income

    @property
    def quarterly_balance_sheet(self):
        if "quarterly_balance_sheet" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._balance

    @property
    def quarterly_cashflow(self):
        if "quarterly_cashflow" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._cashflow


class FakeYFinance:
    def __init__(self, tickers):
        self._tickers = tickers

    def Ticker(self, symbol, session=None):
        return self._tickers[symbol]


def _income_frame(net_income_label="Net Income"):
    return pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): [1000.0, 200.0, 1.5, 400.0, 300.0],
            pd.Timestamp("2025-12-31"): [900.0, 180.0, 1.3, 350.0, 250.0],
        },
        index=["Total Revenue", net_income_label, "Diluted EPS", "Gross Profit", "Operating Income"],
    )


def _balance_frame():
    return pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): [500.0, 250.0, 800.0, 400.0],
            pd.Timestamp("2025-12-31"): [480.0, 240.0, 780.0, 390.0],
        },
        index=["Total Debt", "Stockholders Equity", "Current Assets", "Current Liabilities"],
    )


def _cashflow_frame_with_direct_fcf():
    return pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): [150.0, 200.0, -50.0],
            pd.Timestamp("2025-12-31"): [130.0, 170.0, -40.0],
        },
        index=["Free Cash Flow", "Operating Cash Flow", "Capital Expenditure"],
    )


def _cashflow_frame_without_direct_fcf():
    return pd.DataFrame(
        {
            pd.Timestamp("2026-03-31"): [200.0, -50.0],
            pd.Timestamp("2025-12-31"): [170.0, -40.0],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )


class FinancialSnapshotsExtractorTest(unittest.TestCase):
    def _run(self, tickers, fake_yf):
        with (
            patch.object(fse, "_require_yfinance", return_value=fake_yf),
            patch.object(fse, "_build_session", return_value=None),
        ):
            return fse.fetch_financial_snapshots(tickers)

    def test_named_column_mapping_direct_labels(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(),
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)

        self.assertEqual(list(data.columns), fse.FINANCIAL_SNAPSHOT_COLUMNS)
        self.assertEqual(len(data), 2)
        latest = data[data["PeriodEndDate"] == pd.Timestamp("2026-03-31")].iloc[0]
        self.assertEqual(latest["Ticker"], "NVDA")
        self.assertEqual(latest["ProviderSymbol"], "NVDA")
        self.assertEqual(latest["Revenue"], 1000.0)
        self.assertEqual(latest["NetIncome"], 200.0)
        self.assertEqual(latest["Eps"], 1.5)
        self.assertAlmostEqual(latest["GrossMargin"], 400.0 / 1000.0)
        self.assertAlmostEqual(latest["OperatingMargin"], 300.0 / 1000.0)
        self.assertAlmostEqual(latest["DebtToEquity"], 500.0 / 250.0)
        self.assertAlmostEqual(latest["CurrentRatio"], 800.0 / 400.0)
        self.assertEqual(latest["FreeCashFlow"], 150.0)

    def test_alias_fallback_label_still_maps(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(net_income_label="NetIncome"),
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)
        latest = data[data["PeriodEndDate"] == pd.Timestamp("2026-03-31")].iloc[0]
        self.assertEqual(latest["NetIncome"], 200.0)

    def test_free_cash_flow_computed_fallback_when_direct_row_absent(self):
        # Confirms the capex sign convention (negative-as-outflow, so
        # addition): OCF=200, capex=-50 -> FCF=150, matching the direct-row
        # test's value for symmetry.
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(),
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_without_direct_fcf(),
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)
        latest = data[data["PeriodEndDate"] == pd.Timestamp("2026-03-31")].iloc[0]
        self.assertEqual(latest["FreeCashFlow"], 150.0)

    def test_extra_captures_unmapped_line_items_only(self):
        income = _income_frame()
        income.loc["Research Development"] = [50.0, 45.0]
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=income,
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)
        latest = data[data["PeriodEndDate"] == pd.Timestamp("2026-03-31")].iloc[0]
        extra = latest["Extra"]
        self.assertEqual(extra["income_statement"], {"Research Development": 50.0})
        self.assertEqual(extra["balance_sheet"], {})
        self.assertEqual(extra["cash_flow"], {})

    def test_period_present_in_only_one_statement_leaves_others_null(self):
        # Balance sheet has an extra trailing quarter the income statement
        # doesn't cover -- confirmed live behavior (income's dates are a
        # subset of balance sheet's/cash flow's).
        balance = _balance_frame()
        balance[pd.Timestamp("2025-09-30")] = [460.0, 230.0, 760.0, 380.0]
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(),
                    quarterly_balance_sheet=balance,
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)
        self.assertEqual(len(data), 3)
        older = data[data["PeriodEndDate"] == pd.Timestamp("2025-09-30")].iloc[0]
        self.assertTrue(pd.isna(older["Revenue"]))
        self.assertTrue(pd.isna(older["NetIncome"]))
        self.assertTrue(pd.isna(older["FreeCashFlow"]))
        self.assertAlmostEqual(older["DebtToEquity"], 460.0 / 230.0)

    def test_all_empty_statements_produce_zero_rows_without_error(self):
        fake_yf = FakeYFinance(
            {
                "CDZ.TO": FakeTicker(
                    quarterly_income_stmt=pd.DataFrame(),
                    quarterly_balance_sheet=pd.DataFrame(),
                    quarterly_cashflow=pd.DataFrame(),
                )
            }
        )
        data = self._run(["CDZ.TO"], fake_yf)
        self.assertTrue(data.empty)
        self.assertEqual(list(data.columns), fse.FINANCIAL_SNAPSHOT_COLUMNS)

    def test_per_statement_exception_isolation(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(),
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                    raises_on={"quarterly_balance_sheet"},
                )
            }
        )
        data = self._run(["NVDA"], fake_yf)
        # Income and cashflow still produce rows; balance-derived fields are NULL.
        self.assertFalse(data.empty)
        latest = data[data["PeriodEndDate"] == pd.Timestamp("2026-03-31")].iloc[0]
        self.assertEqual(latest["Revenue"], 1000.0)
        self.assertTrue(pd.isna(latest["DebtToEquity"]))
        self.assertTrue(pd.isna(latest["CurrentRatio"]))

    def test_per_ticker_exception_isolation(self):
        fake_yf = FakeYFinance(
            {
                "BAD": FakeTicker(raises_on={"quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cashflow"}),
                "NVDA": FakeTicker(
                    quarterly_income_stmt=_income_frame(),
                    quarterly_balance_sheet=_balance_frame(),
                    quarterly_cashflow=_cashflow_frame_with_direct_fcf(),
                ),
            }
        )
        data = self._run(["BAD", "NVDA"], fake_yf)
        self.assertEqual(set(data["Ticker"]), {"NVDA"})

    def test_empty_input_returns_stable_frame(self):
        fake_yf = FakeYFinance({})
        data = self._run(["", None], fake_yf)
        self.assertTrue(data.empty)
        self.assertEqual(list(data.columns), fse.FINANCIAL_SNAPSHOT_COLUMNS)

    def test_parse_args_requires_tickers(self):
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
            fse.parse_args([])

    def test_main_prints_frame(self):
        snapshots = pd.DataFrame(
            [{
                "Ticker": "NVDA", "ProviderSymbol": "NVDA", "PeriodEndDate": "2026-03-31",
                "Revenue": 1000.0, "NetIncome": 200.0, "Eps": 1.5, "GrossMargin": 0.4,
                "OperatingMargin": 0.3, "DebtToEquity": 2.0, "CurrentRatio": 2.0,
                "FreeCashFlow": 150.0, "Extra": {},
            }],
            columns=fse.FINANCIAL_SNAPSHOT_COLUMNS,
        )
        with (
            patch.object(fse, "configure_yfinance_cache") as cache,
            patch.object(fse, "clear_proxy_environment") as clear_proxy,
            patch.object(fse, "fetch_financial_snapshots", return_value=snapshots) as fetch,
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            fse.main(["--tickers", "NVDA", "--ignore-proxy"])

        clear_proxy.assert_called_once()
        cache.assert_called_once()
        fetch.assert_called_once_with(["NVDA"])
        self.assertIn("FINANCIAL SNAPSHOTS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
