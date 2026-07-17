import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "fetch-stock-research-data" / "scripts"))

import fetch_stock_research_data as f


class FundsData:
    """Minimal stand-in for yfinance's FundsData object."""
    description = "Tracks dividend-growth equities."
    fund_overview = {"categoryName": "Large Value", "family": "iShares"}
    fund_operations = {"Annual Report Expense Ratio": 0.08}
    asset_classes = {"stockPosition": 0.99}
    top_holdings = {"0": {"Symbol": "MSFT", "Holding Percent": 0.05}}
    sector_weightings = {"technology": 0.20}
    bond_ratings = {}


class FundTicker:
    def get_info(self):
        return {"quoteType": "ETF", "longName": "iShares Core Dividend Growth ETF"}

    def get_funds_data(self):
        return FundsData()


class EquityTicker:
    def get_info(self):
        return {"quoteType": "EQUITY", "longName": "Apple Inc."}

    def get_funds_data(self):
        raise TypeError("funds data is not available for equities")


class FundsGroupTest(unittest.TestCase):
    def test_funds_in_default_groups(self):
        self.assertIn("funds", f.DEFAULT_GROUPS)
        self.assertIn("funds", f.RESEARCH_GROUPS)
        self.assertIn("funds", f.GROUP_FETCHERS)

    def test_fund_ticker_returns_funds_payload(self):
        out = f.fetch_stock_research_data(
            [{"ticker": "DGRO", "provider_symbol": "DGRO", "groups": ["funds"]}],
            ticker_factory=lambda s: FundTicker(),
            history_fetcher=lambda s, d: None,
        )
        item = out[0]
        self.assertEqual(item["errors"], {})
        funds = item["data"]["funds"]
        self.assertEqual(funds["fund_operations"], {"Annual Report Expense Ratio": 0.08})
        self.assertEqual(funds["sector_weightings"], {"technology": 0.20})
        self.assertIn("top_holdings", funds)

    def test_equity_ticker_records_clean_top_level_funds_error(self):
        out = f.fetch_stock_research_data(
            [{"ticker": "AAPL", "provider_symbol": "AAPL", "groups": ["funds"]}],
            ticker_factory=lambda s: EquityTicker(),
            history_fetcher=lambda s, d: None,
        )
        item = out[0]
        # The whole group errors at the top level (not per-subfield) and carries no data.
        self.assertIn("funds", item["errors"])
        self.assertNotIn("funds.description", item["errors"])
        self.assertIsNone(item["data"].get("funds"))


class AnalystTrimTest(unittest.TestCase):
    """upgrades_downgrades/recommendations are trimmed to ANALYST_HISTORY_DAYS."""

    def _frame(self):
        import pandas as pd

        index = pd.DatetimeIndex([pd.Timestamp.now() - pd.Timedelta(days=d) for d in (10, 100, 400, 800)])
        return pd.DataFrame({"Action": ["up", "down", "up", "down"]}, index=index)

    def test_date_indexed_frame_trimmed_to_window(self):
        trimmed = f._trim_to_recent(self._frame())
        self.assertEqual(len(trimmed), 2)  # 10d and 100d kept; 400d and 800d dropped

    def test_non_frame_passes_through(self):
        value = [{"period": "0m"}]
        self.assertIs(f._trim_to_recent(value), value)
        self.assertIsNone(f._trim_to_recent(None))

    def test_non_date_index_passes_through(self):
        import pandas as pd

        frame = pd.DataFrame({"strongBuy": [1, 2]}, index=["0m", "-1m"])
        self.assertIs(f._trim_to_recent(frame), frame)

    def test_analyst_group_serializes_trimmed_table(self):
        outer = self

        class AnalystTicker:
            recommendations = None
            recommendations_summary = {"strongBuy": {"0m": 12}}
            analyst_price_targets = {"mean": 230.0}

            @property
            def upgrades_downgrades(self):
                return outer._frame()

        out = f.fetch_stock_research_data(
            [{"ticker": "AAPL", "provider_symbol": "AAPL", "groups": ["analyst"]}],
            ticker_factory=lambda s: AnalystTicker(),
            history_fetcher=lambda s, d: None,
        )
        table = out[0]["data"]["analyst"]["upgrades_downgrades"]
        # Column-oriented dict with only the two in-window rows.
        self.assertEqual(len(table["Action"]), 2)


if __name__ == "__main__":
    unittest.main()
