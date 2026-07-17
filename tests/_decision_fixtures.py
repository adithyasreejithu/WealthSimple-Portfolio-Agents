"""Shared deterministic fixtures for the decision-support script tests.

No live yfinance: these hand-built payloads mirror the shape of
`fetch-stock-research-data` output and the classify-portfolio JSON.

Emptiness matters now: `scoring_worksheet._has_data` treats structurally-empty
groups as not-fetched, so the "full" equity payload deliberately populates every
group, and the ETF payloads deliberately leave the company groups empty (with
`errors={}`) to mirror what yfinance really returns for a fund.
"""

from __future__ import annotations

from datetime import date, timedelta


def _grade_date(days_ago: int) -> str:
    return f"{(date.today() - timedelta(days=days_ago)).isoformat()} 10:30:00"


def upgrades_table() -> dict:
    """Column-oriented like the real serialization ({column: {grade_date: value}}).

    Within 90 days: 2 "up", 1 "down" (plus 1 "main", not a revision).
    Within 365 days: 3 "up", 2 "down" -> net_revisions_365d = 1.
    One "up" at 400 days sits outside every window.
    """
    actions = {
        _grade_date(5): "main",
        _grade_date(10): "up",
        _grade_date(40): "up",
        _grade_date(80): "down",
        _grade_date(200): "up",
        _grade_date(300): "down",
        _grade_date(400): "up",
    }
    return {"Action": actions, "Firm": {key: "Some Firm" for key in actions}}


def _full_option_chain() -> dict:
    """Calls/puts as column-dicts with strike/OI/volume/IV, spot ~200.

    Chosen so: put/call OI ratio = 500/1500; put/call volume = 120/240;
    ATM (strike 200) IVs 0.28 call / 0.29 put; max-OI call strike 180, put 180.
    """
    return {
        "calls": {
            "strike": {"0": 180.0, "1": 200.0, "2": 220.0},
            "openInterest": {"0": 700, "1": 500, "2": 300},
            "volume": {"0": 100, "1": 80, "2": 60},
            "impliedVolatility": {"0": 0.30, "1": 0.28, "2": 0.26},
        },
        "puts": {
            "strike": {"0": 180.0, "1": 200.0, "2": 220.0},
            "openInterest": {"0": 200, "1": 200, "2": 100},
            "volume": {"0": 50, "1": 40, "2": 30},
            "impliedVolatility": {"0": 0.34, "1": 0.29, "2": 0.27},
        },
    }


def _far_option_chain() -> dict:
    """A later expiry with lower ATM IV, so the term structure is downward."""
    return {
        "calls": {
            "strike": {"0": 180.0, "1": 200.0, "2": 220.0},
            "openInterest": {"0": 400, "1": 300, "2": 200},
            "volume": {"0": 40, "1": 30, "2": 20},
            "impliedVolatility": {"0": 0.26, "1": 0.24, "2": 0.22},
        },
        "puts": {
            "strike": {"0": 180.0, "1": 200.0, "2": 220.0},
            "openInterest": {"0": 150, "1": 120, "2": 80},
            "volume": {"0": 20, "1": 15, "2": 10},
            "impliedVolatility": {"0": 0.27, "1": 0.25, "2": 0.23},
        },
    }


def research_payload() -> list:
    """A full 11-group yfinance-shaped pull for AAPL (equity), every group usable."""
    return [
        {
            "ticker": "AAPL",
            "provider_symbol": "AAPL",
            "groups": [
                "overview", "valuation", "financials", "earnings", "analyst",
                "options", "news", "insider", "institutional", "dividends", "history",
            ],
            "data": {
                "overview": {"longName": "Apple Inc.", "quoteType": "EQUITY", "sector": "Technology"},
                "valuation": {
                    "currentPrice": 200.0,
                    "marketCap": 3000e9,
                    "freeCashflow": 90e9,
                    "forwardPE": 28.0,
                    "trailingPE": 30.0,
                    "pegRatio": 2.4,
                    "returnOnEquity": 1.5,
                    "profitMargins": 0.25,
                    "targetMeanPrice": 230.0,
                    "dividendYield": 0.5,
                },
                "financials": {
                    "income_statement_annual": {
                        "2024-09-30T00:00:00": {"Total Revenue": 391e9, "Net Income": 94e9},
                        "2023-09-30T00:00:00": {"Total Revenue": 383e9, "Net Income": 97e9},
                    },
                    "balance_sheet_annual": {
                        "2024-09-30T00:00:00": {
                            "Total Debt": 100e9,
                            "Stockholders Equity": 57e9,
                            "Current Assets": 152e9,
                            "Current Liabilities": 176e9,
                        },
                    },
                },
                "earnings": {"calendar": {"Earnings Date": ["2026-08-01"]}},
                "analyst": {
                    "recommendations_summary": [{"period": "0m", "strongBuy": 12, "buy": 20}],
                    "upgrades_downgrades": upgrades_table(),
                },
                "options": {
                    "expirations": ["2026-08-15", "2026-09-19"],
                    "chain_2026-08-15": _full_option_chain(),
                    "chain_2026-09-19": _far_option_chain(),
                },
                "news": {"headlines": [{"title": "Apple ships record quarter"}]},
                "insider": {"purchases": {"col": {"Net Shares Purchased (Sold)": 1200}}},
                "institutional": {"major_holders": {"0": {"Value": 0.62, "Breakdown": "insiders"}}},
                "dividends": {"summary": {"payoutRatio": 0.15, "dividendRate": 1.0, "dividendYield": 0.5}},
                "history": [
                    {"Date": "2025-07-10T00:00:00", "Close": 150.0},
                    {"Date": "2026-04-14T00:00:00", "Close": 180.0},
                    {"Date": "2026-06-13T00:00:00", "Close": 190.0},
                    {"Date": "2026-07-13T00:00:00", "Close": 200.0},
                ],
            },
            "errors": {},
        }
    ]


def research_payload_sparse() -> list:
    """Only 4 groups fetched cleanly; the rest errored at the top level."""
    return [
        {
            "ticker": "XYZ",
            "provider_symbol": "XYZ",
            "groups": [
                "overview", "valuation", "financials", "earnings", "analyst",
                "options", "news", "insider", "institutional", "dividends", "history",
            ],
            "data": {
                "overview": {"longName": "XYZ Corp"},
                "valuation": {"currentPrice": 10.0, "marketCap": 1e9},
                "news": {"headlines": [{"title": "XYZ news"}]},
                "history": [
                    {"Date": "2026-06-13T00:00:00", "Close": 9.0},
                    {"Date": "2026-07-13T00:00:00", "Close": 10.0},
                ],
            },
            "errors": {
                "financials": "RuntimeError: blocked",
                "earnings": "RuntimeError: blocked",
                "analyst": "RuntimeError: blocked",
                "options": "RuntimeError: blocked",
                "insider": "RuntimeError: blocked",
                "institutional": "RuntimeError: blocked",
                "dividends": "RuntimeError: blocked",
            },
        }
    ]


def _empty_company_groups() -> dict:
    """The company groups yfinance returns structurally-empty for a fund."""
    return {
        "financials": {
            "income_statement_annual": {}, "income_statement_quarterly": {},
            "balance_sheet_annual": {}, "balance_sheet_quarterly": {},
            "cash_flow_annual": {}, "cash_flow_quarterly": {},
        },
        "earnings": {"calendar": {}, "earnings_dates": {}},
        "analyst": {"recommendations": {}, "recommendations_summary": {},
                    "price_targets": {}, "upgrades_downgrades": {}},
        "insider": {"transactions": {}, "purchases": {}, "roster_holders": {}},
        "institutional": {"major_holders": {}, "institutional_holders": {}, "mutualfund_holders": {}},
    }


def _funds_group() -> dict:
    return {
        "description": "Tracks dividend-growth equities.",
        "fund_overview": {"categoryName": "Large Value", "family": "iShares"},
        "fund_operations": {"Annual Report Expense Ratio": 0.08},
        "top_holdings": {"0": {"Symbol": "MSFT", "Holding Percent": 0.05}},
        "sector_weightings": {"technology": 0.20, "financials": 0.18},
        "bond_ratings": {},
    }


def etf_research_payload() -> list:
    """DGRO-shaped US-listed ETF: company groups empty (errors={}), options/news/
    funds populated. Usable groups: overview, valuation, options, news, dividends,
    history, funds = 7."""
    data = {
        "overview": {"longName": "iShares Core Dividend Growth ETF", "quoteType": "ETF",
                     "longBusinessSummary": "The fund seeks dividend growth."},
        "valuation": {"trailingPE": 23.59, "dividendYield": 1.95},
        "options": {
            "expirations": ["2026-08-15", "2026-09-19"],
            "chain_2026-08-15": _full_option_chain(),
            "chain_2026-09-19": _far_option_chain(),
        },
        "news": {"headlines": [{"title": "Dividend ETFs draw inflows"}]},
        "dividends": {"history": [{"Date": "2026-06-01T00:00:00", "Dividends": 0.29}],
                      "summary": {"dividendYield": 1.95}},
        "history": [
            {"Date": "2026-04-14T00:00:00", "Close": 60.0},
            {"Date": "2026-07-13T00:00:00", "Close": 64.0},
        ],
        "funds": _funds_group(),
    }
    data.update(_empty_company_groups())
    return [
        {
            "ticker": "DGRO",
            "provider_symbol": "DGRO",
            "groups": [
                "overview", "valuation", "financials", "earnings", "analyst",
                "options", "news", "insider", "institutional", "dividends", "history", "funds",
            ],
            "data": data,
            "errors": {},
        }
    ]


def etf_research_payload_ca() -> list:
    """ZGLD-shaped Canadian-listed ETF: additionally empty options and news.
    Usable groups: overview, valuation, dividends, history, funds = 5."""
    payload = etf_research_payload()[0]
    payload["ticker"] = "ZGLD"
    payload["provider_symbol"] = "ZGLD.TO"
    payload["data"]["options"] = {"expirations": []}
    payload["data"]["news"] = {"headlines": []}
    payload["data"]["overview"] = {"longName": "BMO Gold Bullion ETF", "quoteType": "ETF"}
    return [payload]


def classification_payload() -> dict:
    return {
        "holdings": [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "primary_group": "Quality",
                "secondary_tags": ["Equity", "Technology", "USD"],
                "fields": {"position_weight": 3.2, "current_weight_percent": 3.2, "asset_class": "stock"},
            }
        ]
    }


def etf_classification_payload(ticker: str = "DGRO", primary_group: str = "Income") -> dict:
    return {
        "holdings": [
            {
                "ticker": ticker,
                "company_name": "iShares Core Dividend Growth ETF",
                "primary_group": primary_group,
                "secondary_tags": ["ETF", "Dividend", "USD"],
                "fields": {
                    "position_weight": 4.0,
                    "asset_class": "etf",
                    "expense_ratio": 0.0008,
                    "aum": 30e9,
                    "nav": 64.0,
                    "sector_weights": {"technology": 0.20, "financials": 0.18},
                    "top_holdings": {"0": {"Symbol": "MSFT", "Holding Percent": 0.05}},
                },
            }
        ]
    }
