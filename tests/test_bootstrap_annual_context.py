import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# Add the bootstrap skill scripts to the path so we can import the module
bootstrap_skill_scripts = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "bootstrap-stock-research" / "scripts"
)
sys.path.insert(0, str(bootstrap_skill_scripts))

import annual_financial_context as afc


class FakeAnnualTicker:
    def __init__(self, financials=None, balance_sheet=None, cashflow=None, raises_on=None):
        self._financials = financials
        self._balance_sheet = balance_sheet
        self._cashflow = cashflow
        self._raises_on = raises_on or set()

    @property
    def financials(self):
        if "financials" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._financials

    @property
    def balance_sheet(self):
        if "balance_sheet" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._balance_sheet

    @property
    def cashflow(self):
        if "cashflow" in self._raises_on:
            raise RuntimeError("provider failure")
        return self._cashflow


class FakeYFinance:
    def __init__(self, tickers):
        self._tickers = tickers

    def Ticker(self, symbol, session=None):
        return self._tickers[symbol]


def _annual_income_frame(net_income_label="Net Income"):
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [1000.0, 200.0, 1.5, 400.0, 300.0],
            pd.Timestamp("2024-12-31"): [900.0, 180.0, 1.3, 350.0, 250.0],
        },
        index=["Total Revenue", net_income_label, "Diluted EPS", "Gross Profit", "Operating Income"],
    )


def _annual_balance_frame():
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [500.0, 250.0, 800.0, 400.0],
            pd.Timestamp("2024-12-31"): [480.0, 240.0, 780.0, 390.0],
        },
        index=["Total Debt", "Stockholders Equity", "Current Assets", "Current Liabilities"],
    )


def _annual_cashflow_frame():
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [150.0, 200.0, -50.0],
            pd.Timestamp("2024-12-31"): [130.0, 170.0, -40.0],
        },
        index=["Free Cash Flow", "Operating Cash Flow", "Capital Expenditure"],
    )


def _annual_cashflow_frame_without_direct_fcf():
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [200.0, -50.0],
            pd.Timestamp("2024-12-31"): [170.0, -40.0],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )


class AnnualFinancialContextTest(unittest.TestCase):
    def _run(self, ticker, fake_yf):
        with (
            patch.object(afc, "_require_yfinance", return_value=fake_yf),
            patch.object(afc, "_build_session", return_value=None),
        ):
            return afc.fetch_annual_financial_context(ticker)

    def test_named_column_mapping_and_envelope_shape(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeAnnualTicker(
                    financials=_annual_income_frame(),
                    balance_sheet=_annual_balance_frame(),
                    cashflow=_annual_cashflow_frame(),
                )
            }
        )
        context = self._run("nvda", fake_yf)

        self.assertEqual(context["schema"], "annual-financial-context.v1")
        self.assertEqual(context["ticker"], "NVDA")
        self.assertIsNone(context["note"])
        self.assertEqual(context["period_count"], 2)
        # Periods sorted ascending (oldest first).
        self.assertEqual(
            [p["period_end_date"] for p in context["periods"]],
            ["2024-12-31", "2025-12-31"],
        )
        latest = context["periods"][-1]
        self.assertEqual(latest["revenue"], 1000.0)
        self.assertEqual(latest["net_income"], 200.0)
        self.assertEqual(latest["eps"], 1.5)
        self.assertAlmostEqual(latest["gross_margin"], 400.0 / 1000.0)
        self.assertAlmostEqual(latest["operating_margin"], 300.0 / 1000.0)
        self.assertAlmostEqual(latest["debt_to_equity"], 500.0 / 250.0)
        self.assertAlmostEqual(latest["current_ratio"], 800.0 / 400.0)
        self.assertEqual(latest["free_cash_flow"], 150.0)

    def test_alias_fallback_label_still_maps(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeAnnualTicker(
                    financials=_annual_income_frame(net_income_label="NetIncome"),
                    balance_sheet=_annual_balance_frame(),
                    cashflow=_annual_cashflow_frame(),
                )
            }
        )
        context = self._run("NVDA", fake_yf)
        self.assertEqual(context["periods"][-1]["net_income"], 200.0)

    def test_free_cash_flow_computed_fallback(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeAnnualTicker(
                    financials=_annual_income_frame(),
                    balance_sheet=_annual_balance_frame(),
                    cashflow=_annual_cashflow_frame_without_direct_fcf(),
                )
            }
        )
        context = self._run("NVDA", fake_yf)
        self.assertEqual(context["periods"][-1]["free_cash_flow"], 150.0)

    def test_extra_captures_unmapped_line_items_only(self):
        income = _annual_income_frame()
        income.loc["Research Development"] = [50.0, 45.0]
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeAnnualTicker(
                    financials=income,
                    balance_sheet=_annual_balance_frame(),
                    cashflow=_annual_cashflow_frame(),
                )
            }
        )
        context = self._run("NVDA", fake_yf)
        extra = context["periods"][-1]["extra"]
        self.assertEqual(extra["income_statement"], {"Research Development": 50.0})
        self.assertEqual(extra["balance_sheet"], {})
        self.assertEqual(extra["cash_flow"], {})

    def test_per_statement_exception_isolation(self):
        fake_yf = FakeYFinance(
            {
                "NVDA": FakeAnnualTicker(
                    financials=_annual_income_frame(),
                    balance_sheet=_annual_balance_frame(),
                    cashflow=_annual_cashflow_frame(),
                    raises_on={"balance_sheet"},
                )
            }
        )
        context = self._run("NVDA", fake_yf)
        latest = context["periods"][-1]
        self.assertEqual(latest["revenue"], 1000.0)
        self.assertIsNone(latest["debt_to_equity"])
        self.assertIsNone(latest["current_ratio"])

    def test_empty_statements_return_zero_period_envelope_without_error(self):
        fake_yf = FakeYFinance(
            {
                "CDZ.TO": FakeAnnualTicker(
                    financials=pd.DataFrame(),
                    balance_sheet=pd.DataFrame(),
                    cashflow=pd.DataFrame(),
                )
            }
        )
        context = self._run("CDZ.TO", fake_yf)
        self.assertEqual(context["periods"], [])
        self.assertEqual(context["period_count"], 0)
        self.assertEqual(context["schema"], "annual-financial-context.v1")
        self.assertEqual(context["ticker"], "CDZ.TO")
        self.assertIn("ETFs/funds", context["note"])

    def test_fetch_failure_returns_note_without_raising(self):
        with (
            patch.object(afc, "_require_yfinance", side_effect=RuntimeError("no network")),
            patch.object(afc, "_build_session", return_value=None),
        ):
            context = afc.fetch_annual_financial_context("NVDA")
        self.assertEqual(context["periods"], [])
        self.assertEqual(context["period_count"], 0)
        self.assertEqual(context["note"], "Fetch failed; see logs.")

    def test_blank_ticker_returns_note(self):
        context = afc.fetch_annual_financial_context("   ")
        self.assertEqual(context["ticker"], "")
        self.assertEqual(context["note"], "No ticker provided.")
        self.assertEqual(context["periods"], [])

    def test_parse_args_requires_ticker(self):
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
            afc.parse_args([])

    def test_main_prints_valid_json(self):
        context = {
            "schema": "annual-financial-context.v1",
            "ticker": "NVDA",
            "generated_at": "2026-07-21T00:00:00Z",
            "period_count": 0,
            "periods": [],
            "note": None,
        }
        with (
            patch.object(afc, "configure_yfinance_cache") as cache,
            patch.object(afc, "clear_proxy_environment") as clear_proxy,
            patch.object(afc, "fetch_annual_financial_context", return_value=context) as fetch,
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            rc = afc.main(["--ticker", "NVDA", "--ignore-proxy"])

        self.assertEqual(rc, 0)
        clear_proxy.assert_called_once()
        cache.assert_called_once()
        fetch.assert_called_once_with("NVDA")
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["schema"], "annual-financial-context.v1")
        self.assertEqual(parsed["ticker"], "NVDA")


if __name__ == "__main__":
    unittest.main()
