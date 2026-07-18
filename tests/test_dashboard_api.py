"""Tests for the read-only dashboard API (dashboard/api/main.py).

These cover the API wrapper layer only: serialization, endpoint response
shapes, empty-portfolio behavior, CORS configuration, the report cache, and
startup fail-fast. All database access is mocked — the analytics functions
themselves are already tested against real temporary DuckDB files in
tests/test_analytics.py, so hitting a database here would only re-test them.

TestClient usage rule: endpoint tests construct ``TestClient(app)`` without a
context manager, which skips the lifespan handler (the database is fully
mocked). Only the startup tests use ``with TestClient(app)`` so the lifespan
database check actually runs.
"""

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (REPO_ROOT / "src", REPO_ROOT / "dashboard" / "api"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import analytics  # noqa: E402  (same module object main.py calls into — patch target)
import config  # noqa: E402
import main as dashboard_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _make_holding(
    ticker_symbol: str = "AAPL",
    market_value: str = "100",
    **overrides,
) -> analytics.Holding:
    holding = analytics.Holding(
        ticker_id=1,
        ticker_symbol=ticker_symbol,
        exchange="NASDAQGS",
        security_name=f"{ticker_symbol} Inc.",
        security_type="stock",
        quantity=Decimal("2"),
        cost_basis=Decimal("50"),
        market_value=Decimal(market_value),
        currency="USD",
        last_price=Decimal("50"),
        last_price_date=date(2026, 7, 16),
    )
    return replace(holding, **overrides) if overrides else holding


def _make_summary(holdings: list[analytics.Holding]) -> analytics.PortfolioSummary:
    total = sum((h.market_value for h in holdings), start=Decimal("0"))
    cash = analytics.CashSummary(balance=Decimal("25.50"), source="statements")
    return analytics.PortfolioSummary(
        holdings=holdings,
        cash=cash,
        portfolio_value=total + cash.balance,
    )


class ToJsonableTests(unittest.TestCase):
    def test_converts_decimal_to_float(self):
        self.assertEqual(dashboard_api.to_jsonable(Decimal("1.25")), 1.25)

    def test_converts_date_and_datetime_to_isoformat(self):
        self.assertEqual(dashboard_api.to_jsonable(date(2026, 7, 16)), "2026-07-16")
        self.assertEqual(
            dashboard_api.to_jsonable(datetime(2026, 7, 16, 8, 30)),
            "2026-07-16T08:30:00",
        )

    def test_converts_nested_dataclass_to_plain_dict(self):
        row = dashboard_api.to_jsonable(_make_holding())
        self.assertEqual(row["ticker_symbol"], "AAPL")
        self.assertEqual(row["market_value"], 100.0)
        self.assertEqual(row["last_price_date"], "2026-07-16")
        self.assertEqual(row["data_quality_flags"], [])

    def test_passes_through_plain_values(self):
        for value in ("text", 3, None, True):
            self.assertEqual(dashboard_api.to_jsonable(value), value)


class CorsOriginsTests(unittest.TestCase):
    def test_default_when_env_unset_or_blank(self):
        self.assertEqual(dashboard_api.cors_origins(None), ["http://localhost:3000"])
        self.assertEqual(dashboard_api.cors_origins("   "), ["http://localhost:3000"])

    def test_parses_comma_separated_values_stripping_whitespace_and_empties(self):
        self.assertEqual(
            dashboard_api.cors_origins("http://a:3000, http://b:3001,,"),
            ["http://a:3000", "http://b:3001"],
        )

    def test_cors_header_present_for_default_origin(self):
        client = TestClient(dashboard_api.app)
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )


class _EndpointTestCase(unittest.TestCase):
    """Shared client plus a helper to point config.DATABASE_PATH at a temp path."""

    def setUp(self):
        self.client = TestClient(dashboard_api.app)

    def _patch_database_path(self, exists: bool) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_file = Path(temp_dir.name) / "portfolio.duckdb"
        if exists:
            db_file.touch()
        patcher = patch.object(config, "DATABASE_PATH", db_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        return db_file


class HealthEndpointTests(_EndpointTestCase):
    def test_health_reports_existing_database(self):
        db_file = self._patch_database_path(exists=True)
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "database": str(db_file), "database_exists": True},
        )

    def test_health_reports_missing_database(self):
        self._patch_database_path(exists=False)
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["database_exists"])


class SummaryEndpointTests(_EndpointTestCase):
    def test_returns_topline_numbers(self):
        holdings = [_make_holding("AAPL", "750"), _make_holding("XEQT", "250", ticker_id=2)]
        with patch.object(analytics, "get_portfolio_summary", return_value=_make_summary(holdings)):
            response = self.client.get("/api/portfolio/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "portfolio_value": 1025.5,
                "cash_balance": 25.5,
                "cash_source": "statements",
                "holdings_count": 2,
            },
        )

    def test_empty_portfolio_returns_zeros(self):
        summary = analytics.PortfolioSummary(
            holdings=[],
            cash=analytics.CashSummary(balance=Decimal("0"), source="statements"),
            portfolio_value=Decimal("0"),
        )
        with patch.object(analytics, "get_portfolio_summary", return_value=summary):
            response = self.client.get("/api/portfolio/summary")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["portfolio_value"], 0.0)
        self.assertEqual(body["holdings_count"], 0)


class HoldingsEndpointTests(_EndpointTestCase):
    def test_returns_weights_sorted_descending(self):
        holdings = [_make_holding("XEQT", "250", ticker_id=2), _make_holding("AAPL", "750")]
        with patch.object(analytics, "get_holdings", return_value=holdings):
            response = self.client.get("/api/portfolio/holdings")
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual([r["ticker_symbol"] for r in rows], ["AAPL", "XEQT"])
        self.assertEqual([r["weight"] for r in rows], [0.75, 0.25])
        self.assertEqual(rows[0]["market_value"], 750.0)

    def test_empty_portfolio_returns_empty_list(self):
        with patch.object(analytics, "get_holdings", return_value=[]):
            response = self.client.get("/api/portfolio/holdings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


class AllocationEndpointTests(_EndpointTestCase):
    def test_returns_group_breakdown(self):
        self._patch_database_path(exists=True)
        holdings = [_make_holding()]
        allocation = {
            "by_group": {"Growth": {"market_value": Decimal("100"), "weight": 1.0}},
        }
        with (
            patch.object(analytics, "get_holdings", return_value=holdings) as get_holdings,
            patch.object(analytics, "get_group_allocation", return_value=allocation) as get_alloc,
        ):
            response = self.client.get("/api/portfolio/allocation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"by_group": {"Growth": {"market_value": 100.0, "weight": 1.0}}},
        )
        get_alloc.assert_called_once_with(str(config.DATABASE_PATH), holdings)
        get_holdings.assert_called_once()

    def test_empty_portfolio_returns_empty_mapping(self):
        with (
            patch.object(analytics, "get_holdings", return_value=[]),
            patch.object(analytics, "get_group_allocation", return_value={}),
        ):
            response = self.client.get("/api/portfolio/allocation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})


class TrendEndpointTests(_EndpointTestCase):
    def test_serializes_dates_and_decimals(self):
        series = [
            {
                "date": date(2026, 7, 15),
                "securities_value": Decimal("100"),
                "cash_balance": Decimal("10"),
                "portfolio_value": Decimal("110"),
            }
        ]
        with patch.object(analytics, "get_historical_portfolio_values", return_value=series):
            response = self.client.get("/api/portfolio/trend")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "date": "2026-07-15",
                    "securities_value": 100.0,
                    "cash_balance": 10.0,
                    "portfolio_value": 110.0,
                }
            ],
        )

    def test_empty_history_returns_empty_list(self):
        with patch.object(analytics, "get_historical_portfolio_values", return_value=[]):
            response = self.client.get("/api/portfolio/trend")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


class ReportEndpointTests(_EndpointTestCase):
    def setUp(self):
        super().setUp()
        dashboard_api._report_cache.clear()
        self.addCleanup(dashboard_api._report_cache.clear)
        self.report = {"summary": {"portfolio_value": Decimal("100")}, "holdings": []}

    def test_first_call_computes_and_wraps_report(self):
        self._patch_database_path(exists=True)
        with patch.object(analytics, "portfolio_report", return_value=self.report) as build:
            response = self.client.get("/api/portfolio/report")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["report"], {"summary": {"portfolio_value": 100.0}, "holdings": []})
        self.assertIn("generated_at", body)
        self.assertIn("database_mtime", body)
        build.assert_called_once_with(str(config.DATABASE_PATH))

    def test_second_call_with_unchanged_mtime_serves_cache(self):
        self._patch_database_path(exists=True)
        with patch.object(analytics, "portfolio_report", return_value=self.report) as build:
            first = self.client.get("/api/portfolio/report").json()
            second = self.client.get("/api/portfolio/report").json()
        build.assert_called_once()
        self.assertEqual(first, second)

    def test_changed_mtime_triggers_recompute(self):
        db_file = self._patch_database_path(exists=True)
        with patch.object(analytics, "portfolio_report", return_value=self.report) as build:
            self.client.get("/api/portfolio/report")
            mtime = db_file.stat().st_mtime
            os.utime(db_file, (mtime + 10, mtime + 10))
            self.client.get("/api/portfolio/report")
        self.assertEqual(build.call_count, 2)

    def test_missing_database_returns_503_with_actionable_detail(self):
        self._patch_database_path(exists=False)
        response = self.client.get("/api/portfolio/report")
        self.assertEqual(response.status_code, 503)
        self.assertIn("DB_PATH", response.json()["detail"])

    def test_report_serializes_trend_overlays(self):
        self._patch_database_path(exists=True)
        report = {
            "performance": {
                "trend_overlays": {
                    "available": True,
                    "benchmarks": {"XEQT": {"symbol": "XEQT", "available": True}},
                    "points": [
                        {
                            "date": date(2025, 1, 2),
                            "net_deposits_cum": Decimal("500"),
                            "benchmarks": {"XEQT": None, "SP500": Decimal("140.5")},
                        }
                    ],
                }
            }
        }
        with patch.object(analytics, "portfolio_report", return_value=report):
            body = self.client.get("/api/portfolio/report").json()
        point = body["report"]["performance"]["trend_overlays"]["points"][0]
        self.assertEqual(
            point,
            {
                "date": "2025-01-02",
                "net_deposits_cum": 500.0,
                "benchmarks": {"XEQT": None, "SP500": 140.5},
            },
        )


class ClassificationsEndpointTests(_EndpointTestCase):
    def test_returns_classification_detail(self):
        payload = {
            "generated_at": "2026-07-17T00:00:00",
            "count": 1,
            "review_count": 0,
            "classifications": [
                {
                    "ticker_id": 1,
                    "ticker_symbol": "AAPL",
                    "security_type": "stock",
                    "primary_group": "Quality",
                    "secondary_tags": ["Equity", "Technology"],
                    "confidence": "high",
                    "review_needed": False,
                    "fields": {"sector": "Technology"},
                }
            ],
        }
        with patch.object(analytics, "get_classification_details", return_value=payload):
            response = self.client.get("/api/portfolio/classifications")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    def test_empty_table_returns_zero_counts(self):
        payload = {"generated_at": None, "count": 0, "review_count": 0, "classifications": []}
        with patch.object(analytics, "get_classification_details", return_value=payload):
            response = self.client.get("/api/portfolio/classifications")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)


class StockHistoryEndpointTests(_EndpointTestCase):
    def _history(self, count: int = 2) -> dict:
        return {
            "ticker_id": 1,
            "ticker_symbol": "AAPL",
            "security_name": "Apple Inc.",
            "currency": "USD",
            "count": count,
            "points": [
                {"date": "2026-07-15", "close": 210.0, "adjusted_close": 209.0, "volume": 1000}
            ]
            * count,
        }

    def test_returns_series_for_default_range(self):
        with patch.object(analytics, "get_price_history", return_value=self._history()) as fetch:
            response = self.client.get("/api/stocks/AAPL/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ticker_symbol"], "AAPL")
        # Default range 1y -> a date_from ~365 days back is passed through.
        _, kwargs = fetch.call_args
        self.assertIsNotNone(kwargs["date_from"])

    def test_max_range_passes_no_date_floor(self):
        with patch.object(analytics, "get_price_history", return_value=self._history()) as fetch:
            response = self.client.get("/api/stocks/AAPL/history?range=max")
        self.assertEqual(response.status_code, 200)
        _, kwargs = fetch.call_args
        self.assertIsNone(kwargs["date_from"])

    def test_invalid_range_returns_422(self):
        with patch.object(analytics, "get_price_history") as fetch:
            response = self.client.get("/api/stocks/AAPL/history?range=7d")
        self.assertEqual(response.status_code, 422)
        fetch.assert_not_called()

    def test_unknown_symbol_returns_404(self):
        with patch.object(analytics, "get_price_history", return_value=None):
            response = self.client.get("/api/stocks/NOPE/history")
        self.assertEqual(response.status_code, 404)
        self.assertIn("NOPE", response.json()["detail"])


class StartupTests(unittest.TestCase):
    def test_fails_fast_when_database_missing(self):
        missing = Path(tempfile.gettempdir()) / "dashboard-api-missing" / "nope.duckdb"
        with patch.object(config, "DATABASE_PATH", missing):
            with self.assertRaises(RuntimeError) as raised:
                with TestClient(dashboard_api.app):
                    pass
        self.assertIn("DB_PATH", str(raised.exception))

    def test_starts_when_database_exists(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_file = Path(temp_dir.name) / "portfolio.duckdb"
        db_file.touch()
        with patch.object(config, "DATABASE_PATH", db_file):
            with TestClient(dashboard_api.app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
