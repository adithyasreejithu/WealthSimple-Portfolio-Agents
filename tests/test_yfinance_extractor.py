import unittest
from io import StringIO
from datetime import date
from unittest.mock import patch

import pandas as pd

import yfinance_extractor


class FakeFundsData:
    def __init__(self, top_holdings=None, sector_weightings=None):
        self.top_holdings = top_holdings
        self.sector_weightings = sector_weightings


class FakeTicker:
    def __init__(self, info, funds_data=None):
        self._info = info
        self.funds_data = funds_data

    def get_info(self):
        return self._info


class FakeYFinance:
    def __init__(self, ticker_info=None, funds_data=None, history=None):
        self.ticker_info = ticker_info or {}
        self.funds_data = funds_data or {}
        self.history = history if history is not None else pd.DataFrame()
        self.ticker_calls = []
        self.download_kwargs = None

    def Ticker(self, ticker, session=None):
        self.ticker_calls.append((ticker, session))
        return FakeTicker(self.ticker_info.get(ticker, {}), self.funds_data.get(ticker))

    def download(self, **kwargs):
        self.download_kwargs = kwargs
        return self.history


class YFinanceExtractorTest(unittest.TestCase):
    def test_fetch_security_info_returns_stock_dataframe(self):
        fake_yf = FakeYFinance(
            ticker_info={
                "AAPL": {
                    "quoteType": "EQUITY",
                    "longName": "Apple Inc.",
                    "fullExchangeName": "NasdaqGS",
                    "currency": "USD",
                    "financialCurrency": "USD",
                    "sector": "Technology",
                    "industry": "Consumer Electronics",
                }
            }
        )

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, etfs = yfinance_extractor.fetch_security_info(["AAPL"])

        self.assertEqual(list(stocks.columns), yfinance_extractor.STOCK_INFO_COLUMNS)
        self.assertEqual(list(etfs.columns), yfinance_extractor.ETF_INFO_COLUMNS)
        self.assertEqual(len(stocks), 1)
        self.assertTrue(etfs.empty)
        self.assertEqual(stocks.loc[0, "ticker"], "AAPL")
        self.assertEqual(stocks.loc[0, "company_name"], "Apple Inc.")
        self.assertEqual(stocks.loc[0, "asset"], "EQUITY")
        self.assertEqual(stocks.loc[0, "currency"], "USD")
        self.assertEqual(stocks.loc[0, "financial_currency"], "USD")

    def test_trading_currency_wins_over_financial_currency_for_bn(self):
        fake_yf = FakeYFinance(ticker_info={
            "BN.TO": {
                "quoteType": "EQUITY", "longName": "Brookfield Corporation",
                "fullExchangeName": "Toronto Stock Exchange",
                "currency": "CAD", "financialCurrency": "USD",
            }
        })
        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, _ = yfinance_extractor.fetch_security_info([{
                "symbol": "BN", "currency": "CAD", "name": "Brookfield Corp."
            }])
        self.assertEqual(stocks.loc[0, "provider_symbol"], "BN.TO")
        self.assertEqual(stocks.loc[0, "currency"], "CAD")
        self.assertEqual(stocks.loc[0, "financial_currency"], "USD")

    def test_short_name_is_used_when_yahoo_omits_long_name(self):
        fake_yf = FakeYFinance(ticker_info={
            "NBIS": {
                "quoteType": "EQUITY", "shortName": "Nebius Group N.V.",
                "fullExchangeName": "NasdaqGS", "currency": "USD",
                "financialCurrency": "USD",
            }
        })
        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(
            yfinance_extractor, "curl_requests", None
        ):
            stocks, _ = yfinance_extractor.fetch_security_info([{
                "symbol": "NBIS", "currency": "USD", "name": "Nebius Group N.V."
            }])

        self.assertEqual(stocks.loc[0, "ticker"], "NBIS")
        self.assertEqual(stocks.loc[0, "company_name"], "Nebius Group N.V.")

    def test_missing_trading_currency_uses_expected_currency_only_with_full_identity(self):
        fake_yf = FakeYFinance(ticker_info={
            "BN.TO": {
                "quoteType": "EQUITY", "longName": "Brookfield Corporation",
                "fullExchangeName": "Toronto Stock Exchange", "financialCurrency": "USD",
            }
        })
        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, _ = yfinance_extractor.fetch_security_info([{
                "symbol": "BN", "currency": "CAD", "name": "Brookfield Corp."
            }])
        self.assertEqual(stocks.loc[0, "currency"], "CAD")
        self.assertEqual(stocks.loc[0, "financial_currency"], "USD")

    def test_missing_trading_currency_without_name_is_rejected(self):
        fake_yf = FakeYFinance(ticker_info={
            "BN.TO": {
                "quoteType": "EQUITY", "longName": "Brookfield Corporation",
                "fullExchangeName": "Toronto Stock Exchange", "financialCurrency": "USD",
            }
        })
        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, _ = yfinance_extractor.fetch_security_info([{"symbol": "BN", "currency": "CAD"}])
        self.assertTrue(stocks.empty)

    def test_fetch_security_info_returns_etf_dataframe(self):
        holdings = [{"symbol": "AAPL", "holdingName": "Apple Inc."}]
        sectors = {"technology": 0.35}
        fake_yf = FakeYFinance(
            ticker_info={
                "VFV.TO": {
                    "quoteType": "ETF",
                    "longName": "Vanguard S&P 500 Index ETF",
                    "currency": "CAD",
                    "fundFamily": "Vanguard",
                    "category": "US Equity",
                    "yield": 0.01,
                    "annualReportExpenseRatio": 0.0009,
                    "totalAssets": 1000,
                    "navPrice": 120.5,
                }
            },
            funds_data={"VFV.TO": FakeFundsData(top_holdings=holdings, sector_weightings=sectors)},
        )

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, etfs = yfinance_extractor.fetch_security_info(["VFV.TO"])

        self.assertTrue(stocks.empty)
        self.assertEqual(len(etfs), 1)
        self.assertEqual(etfs.loc[0, "ticker"], "VFV.TO")
        self.assertEqual(etfs.loc[0, "fund_family"], "Vanguard")
        self.assertEqual(etfs.loc[0, "asset"], "US Equity")
        self.assertEqual(etfs.loc[0, "top_holdings"], holdings)
        self.assertEqual(etfs.loc[0, "sector_weights"], sectors)

    def test_symbol_without_evidence_uses_bare_symbol_only(self):
        fake_yf = FakeYFinance(
            ticker_info={
                "SHOP": {"quoteType": None},
                "SHOP.TO": {
                    "quoteType": "EQUITY",
                    "longName": "Shopify Inc.",
                    "fullExchangeName": "Toronto",
                    "currency": "CAD",
                    "sector": "Technology",
                    "industry": "Software",
                },
            }
        )

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, etfs = yfinance_extractor.fetch_security_info(["SHOP"])

        self.assertEqual([call[0] for call in fake_yf.ticker_calls], ["SHOP"])
        self.assertTrue(etfs.empty)
        self.assertTrue(stocks.empty)

    def test_fetch_security_info_skips_unknown_security_type(self):
        fake_yf = FakeYFinance(ticker_info={"BTC-USD": {"quoteType": "CRYPTOCURRENCY"}})

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, etfs = yfinance_extractor.fetch_security_info(["BTC-USD"])

        self.assertTrue(stocks.empty)
        self.assertTrue(etfs.empty)
        self.assertEqual(list(stocks.columns), yfinance_extractor.STOCK_INFO_COLUMNS)
        self.assertEqual(list(etfs.columns), yfinance_extractor.ETF_INFO_COLUMNS)

    def test_cad_hint_rejects_us_collision_and_selects_to_listing(self):
        fake_yf = FakeYFinance(ticker_info={
            "SHOP": {"quoteType": "EQUITY", "longName": "Shopify Inc.", "currency": "USD"},
            "SHOP.TO": {
                "quoteType": "EQUITY", "longName": "Shopify Inc.",
                "fullExchangeName": "Toronto", "currency": "CAD",
            },
        })

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, _ = yfinance_extractor.fetch_security_info([
                {"symbol": "SHOP", "currency": "CAD", "name": "Shopify Inc."}
            ])

        self.assertEqual(stocks.loc[0, "ticker"], "SHOP")
        self.assertEqual(stocks.loc[0, "provider_symbol"], "SHOP.TO")
        self.assertEqual([call[0] for call in fake_yf.ticker_calls], ["SHOP.TO"])

    def test_explicit_canadian_suffixes_can_be_resolved(self):
        exchanges = {
            ".TO": "Toronto Stock Exchange", ".V": "TSX Venture",
            ".CN": "Canadian Securities Exchange", ".NE": "Cboe NEO",
        }
        for suffix, exchange in exchanges.items():
            with self.subTest(suffix=suffix):
                symbol = f"ABC{suffix}"
                fake_yf = FakeYFinance(ticker_info={symbol: {
                    "quoteType": "EQUITY", "longName": "ABC Corporation",
                    "fullExchangeName": exchange, "currency": "CAD",
                }})
                with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
                    stocks, _ = yfinance_extractor.fetch_security_info([
                        {"symbol": symbol, "currency": "CAD", "name": "ABC Corporation"}
                    ])
                self.assertEqual(stocks.loc[0, "provider_symbol"], symbol)

    def test_explicit_override_is_tried_exclusively(self):
        fake_yf = FakeYFinance(ticker_info={"ABC.V": {
            "quoteType": "EQUITY", "longName": "ABC Corporation",
            "fullExchangeName": "TSX Venture", "currency": "CAD",
        }})
        with patch.dict(yfinance_extractor.YFINANCE_SYMBOL_OVERRIDES, {("ABC", "CAD"): "ABC.V"}, clear=True), patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, _ = yfinance_extractor.fetch_security_info([
                {"symbol": "ABC", "currency": "CAD", "name": "ABC Corporation"}
            ])
        self.assertEqual([call[0] for call in fake_yf.ticker_calls], ["ABC.V"])
        self.assertEqual(stocks.loc[0, "provider_symbol"], "ABC.V")

    def test_fetch_security_info_empty_tickers_return_stable_dataframes(self):
        fake_yf = FakeYFinance()

        with patch.object(yfinance_extractor, "yf", fake_yf), patch.object(yfinance_extractor, "curl_requests", None):
            stocks, etfs = yfinance_extractor.fetch_security_info(["", None])

        self.assertTrue(stocks.empty)
        self.assertTrue(etfs.empty)
        self.assertEqual(list(stocks.columns), yfinance_extractor.STOCK_INFO_COLUMNS)
        self.assertEqual(list(etfs.columns), yfinance_extractor.ETF_INFO_COLUMNS)

    def test_fetch_security_history_normalizes_single_ticker_dataframe(self):
        history = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.5],
                "Close": [10.5],
                "Adj Close": [10.4],
                "Volume": [1000],
            },
            index=pd.DatetimeIndex(["2025-01-02"], name="Date"),
        )
        fake_yf = FakeYFinance(history=history)

        with patch.object(yfinance_extractor, "yf", fake_yf):
            data = yfinance_extractor.fetch_security_history(["AAPL"], date(2025, 1, 1), date(2025, 1, 3))

        self.assertEqual(list(data.columns), yfinance_extractor.HISTORY_COLUMNS)
        self.assertEqual(len(data), 1)
        self.assertEqual(data.loc[0, "Ticker"], "AAPL")
        self.assertEqual(data.loc[0, "Open"], 10.0)
        self.assertEqual(fake_yf.download_kwargs["tickers"], "AAPL")
        self.assertEqual(fake_yf.download_kwargs["start"], "2025-01-01")
        self.assertEqual(fake_yf.download_kwargs["end"], "2025-01-03")

    def test_fetch_security_history_normalizes_multi_ticker_dataframe(self):
        price_columns = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        columns = pd.MultiIndex.from_product([["AAPL", "MSFT"], price_columns])
        history = pd.DataFrame(
            [[10.0, 11.0, 9.5, 10.5, 10.4, 1000, 20.0, 21.0, 19.5, 20.5, 20.4, 2000]],
            index=pd.DatetimeIndex(["2025-01-02"], name="Date"),
            columns=columns,
        )
        fake_yf = FakeYFinance(history=history)

        with patch.object(yfinance_extractor, "yf", fake_yf):
            data = yfinance_extractor.fetch_security_history(["AAPL", "MSFT"], "2025-01-01", "2025-01-03")

        self.assertEqual(list(data.columns), yfinance_extractor.HISTORY_COLUMNS)
        self.assertEqual(len(data), 2)
        self.assertEqual(set(data["Ticker"]), {"AAPL", "MSFT"})
        self.assertEqual(fake_yf.download_kwargs["tickers"], ["AAPL", "MSFT"])

    def test_fetch_security_history_empty_tickers_return_stable_dataframe(self):
        fake_yf = FakeYFinance()

        with patch.object(yfinance_extractor, "yf", fake_yf):
            data = yfinance_extractor.fetch_security_history([], "2025-01-01", "2025-01-03")

        self.assertTrue(data.empty)
        self.assertEqual(list(data.columns), yfinance_extractor.HISTORY_COLUMNS)

    def test_parse_args_requires_start_date_when_history_requested(self):
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit):
            yfinance_extractor.parse_args(["--tickers", "AAPL", "--include-history"])

    def test_main_prints_metadata_and_history(self):
        stocks = pd.DataFrame(
            [{"ticker": "AAPL", "company_name": "Apple Inc.", "asset": "EQUITY"}],
            columns=yfinance_extractor.STOCK_INFO_COLUMNS,
        )
        etfs = pd.DataFrame(columns=yfinance_extractor.ETF_INFO_COLUMNS)
        history = pd.DataFrame(
            [{"Date": "2025-01-02", "Ticker": "AAPL", "Open": 10.0}],
            columns=yfinance_extractor.HISTORY_COLUMNS,
        )

        with (
            patch.object(yfinance_extractor, "configure_yfinance_cache") as cache,
            patch.object(yfinance_extractor, "clear_proxy_environment") as clear_proxy,
            patch.object(yfinance_extractor, "fetch_security_info", return_value=(stocks, etfs)) as fetch_info,
            patch.object(yfinance_extractor, "fetch_security_history", return_value=history) as fetch_history,
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            yfinance_extractor.main(
                [
                    "--tickers",
                    "AAPL",
                    "--include-history",
                    "--start-date",
                    "2025-01-01",
                    "--end-date",
                    "2025-01-03",
                    "--ignore-proxy",
                ]
            )

        clear_proxy.assert_called_once()
        cache.assert_called_once()
        fetch_info.assert_called_once_with(["AAPL"])
        fetch_history.assert_called_once_with(["AAPL"], "2025-01-01", "2025-01-03")
        self.assertIn("STOCKS", output.getvalue())
        self.assertIn("HISTORY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
