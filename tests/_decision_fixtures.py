"""Shared deterministic fixtures for the decision-support script tests.

No live yfinance: these hand-built payloads mirror the shape of
`fetch-stock-research-data` output and the classify-portfolio JSON.
"""

from __future__ import annotations


def research_payload() -> list:
    """A full 11-group yfinance-shaped pull for AAPL with clean derived metrics."""
    return [
        {
            "ticker": "AAPL",
            "provider_symbol": "AAPL",
            "groups": [
                "overview", "valuation", "financials", "earnings", "analyst",
                "options", "news", "insider", "institutional", "dividends", "history",
            ],
            "data": {
                "overview": {"longName": "Apple Inc.", "sector": "Technology"},
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
                "earnings": {"calendar": {}},
                "analyst": {
                    "recommendations_summary": [{"period": "0m", "strongBuy": 12, "buy": 20}],
                    "upgrades_downgrades": [{"firm": "X", "toGrade": "Buy"}],
                },
                "options": {
                    "expirations": ["2026-08-15"],
                    "chain_2026-08-15": {
                        "calls": {"openInterest": {"0": 1000, "1": 500}},
                        "puts": {"openInterest": {"0": 300, "1": 200}},
                    },
                },
                "news": {"headlines": []},
                "insider": {"purchases": {"col": {"Net Shares Purchased (Sold)": 1200}}},
                "institutional": {"major_holders": {}},
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
                "news": {"headlines": []},
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


def classification_payload() -> dict:
    return {
        "holdings": [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "primary_group": "Quality",
                "secondary_tags": ["Equity", "Technology", "USD"],
                "fields": {"position_weight": 3.2},
            }
        ]
    }
