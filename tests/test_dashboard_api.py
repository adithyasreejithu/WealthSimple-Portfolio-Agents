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
import subprocess
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

    def test_converts_path_to_string(self):
        path = Path("Data") / "full_exports" / "activities-export-2025-04.csv"
        self.assertEqual(dashboard_api.to_jsonable(path), str(path))


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


class EtfOverlapEndpointTests(_EndpointTestCase):
    def test_returns_overlap_payload_with_decimals_serialized(self):
        payload = {
            "available": True,
            "basis": "top_holdings",
            "caveat": "Based on reported top holdings.",
            "etf_count": 2,
            "compared_count": 2,
            "etfs": [
                {
                    "ticker_symbol": "AAA",
                    "security_name": "AAA Fund",
                    "market_value": Decimal("1000"),
                    "sleeve_weight": 0.5,
                    "reported_weight": 0.5,
                    "holdings_count": 2,
                }
            ],
            "share": {
                "overlapping_weight": 0.25,
                "unique_weight": 0.25,
                "unreported_weight": 0.5,
            },
            "pairs": [{"a": "AAA", "b": "BBB", "overlap_pct": 0.2, "shared": []}],
            "top_shared_holdings": [],
        }
        with patch.object(analytics, "get_etf_overlap", return_value=payload):
            response = self.client.get("/api/etfs/overlap")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        # Decimal market values must come back as plain JSON numbers.
        self.assertEqual(body["etfs"][0]["market_value"], 1000.0)
        self.assertAlmostEqual(sum(body["share"].values()), 1.0)

    def test_unavailable_overlap_passes_reason_through(self):
        payload = {
            "available": False,
            "reason": "Fewer than two ETFs have reported holdings to compare.",
            "basis": "top_holdings",
            "etf_count": 1,
            "etfs": [],
            "share": {"overlapping_weight": 0.0, "unique_weight": 0.0, "unreported_weight": 0.0},
            "pairs": [],
            "top_shared_holdings": [],
        }
        with patch.object(analytics, "get_etf_overlap", return_value=payload):
            response = self.client.get("/api/etfs/overlap")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])
        self.assertIn("two", response.json()["reason"])


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


class ActionAuthTests(_EndpointTestCase):
    """The gate in front of every state-changing endpoint."""

    def setUp(self):
        super().setUp()
        # TestClient presents 'testclient' as the host, which is not loopback,
        # so requests are treated as remote unless a test says otherwise.
        self.addCleanup(os.environ.pop, "DASHBOARD_ACTION_TOKEN", None)
        os.environ.pop("DASHBOARD_ACTION_TOKEN", None)

    def test_non_loopback_request_is_refused_without_a_token(self):
        response = self.client.post("/api/actions/classify")
        self.assertEqual(response.status_code, 403)
        self.assertIn("DASHBOARD_ACTION_TOKEN", response.json()["detail"])

    def test_loopback_request_is_allowed_without_a_token(self):
        with patch.object(dashboard_api.runner, "submit") as submit:
            submit.return_value = dashboard_api.Job(id="j1", kind="classify")
            response = TestClient(dashboard_api.app, client=("127.0.0.1", 1234)).post(
                "/api/actions/classify"
            )
        self.assertEqual(response.status_code, 202)

    def test_token_mode_requires_a_matching_bearer_token(self):
        os.environ["DASHBOARD_ACTION_TOKEN"] = "s3cret"

        missing = self.client.post("/api/actions/classify")
        wrong = self.client.post(
            "/api/actions/classify", headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

        with patch.object(dashboard_api.runner, "submit") as submit:
            submit.return_value = dashboard_api.Job(id="j1", kind="classify")
            ok = self.client.post(
                "/api/actions/classify", headers={"Authorization": "Bearer s3cret"}
            )
        self.assertEqual(ok.status_code, 202)

    def test_token_mode_applies_to_a_loopback_caller_too(self):
        os.environ["DASHBOARD_ACTION_TOKEN"] = "s3cret"
        response = TestClient(dashboard_api.app, client=("127.0.0.1", 1234)).post(
            "/api/actions/classify"
        )
        self.assertEqual(response.status_code, 401)


class ActionEndpointTests(_EndpointTestCase):
    def setUp(self):
        super().setUp()
        os.environ["DASHBOARD_ACTION_TOKEN"] = "s3cret"
        self.addCleanup(os.environ.pop, "DASHBOARD_ACTION_TOKEN", None)
        self.headers = {"Authorization": "Bearer s3cret"}

    def test_classify_action_queues_a_job(self):
        with patch.object(dashboard_api.runner, "submit") as submit:
            submit.return_value = dashboard_api.Job(id="job-1", kind="classify")
            response = self.client.post("/api/actions/classify", headers=self.headers)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["id"], "job-1")
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(submit.call_args.args[0], "classify")

    def test_a_second_job_is_rejected_with_409(self):
        busy = dashboard_api.JobBusyError(dashboard_api.Job(id="job-1", kind="refresh"))
        with patch.object(dashboard_api.runner, "submit", side_effect=busy):
            response = self.client.post("/api/actions/refresh", headers=self.headers)

        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["detail"])

    def test_override_writes_the_yaml_then_queues_reclassification(self):
        stored = {"ticker": "BN", "primary_group": "Quality"}
        payload = {"ticker": "bn", "primary_group": "Quality", "rationale": "Owner call."}
        with patch.object(dashboard_api.manual_overrides, "upsert_override", return_value=stored) as upsert:
            with patch.object(dashboard_api.runner, "submit") as submit:
                submit.return_value = dashboard_api.Job(id="job-2", kind="classify")
                response = self.client.post(
                    "/api/actions/overrides", json=payload, headers=self.headers
                )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["override"], stored)
        self.assertEqual(body["job"]["id"], "job-2")
        self.assertEqual(upsert.call_args.args, ("bn", "Quality"))
        self.assertEqual(upsert.call_args.kwargs["rationale"], "Owner call.")

    def test_override_validation_failure_is_a_422_and_queues_nothing(self):
        error = dashboard_api.manual_overrides.OverrideError("Group 'Nope' is not assignable.")
        with patch.object(dashboard_api.manual_overrides, "upsert_override", side_effect=error):
            with patch.object(dashboard_api.runner, "submit") as submit:
                response = self.client.post(
                    "/api/actions/overrides",
                    json={"ticker": "BN", "primary_group": "Nope", "rationale": "x"},
                    headers=self.headers,
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("not assignable", response.json()["detail"])
        submit.assert_not_called()

    def test_resolve_ticker_action_queues_a_job(self):
        body = {
            "source_symbol": "XYZ",
            "canonical_symbol": "XYZ",
            "yahoo_symbol": "XYZ.TO",
            "currency": "CAD",
        }
        with patch.object(dashboard_api.runner, "submit") as submit:
            submit.return_value = dashboard_api.Job(id="job-3", kind="resolve-ticker")
            response = self.client.post(
                "/api/actions/resolve-ticker", json=body, headers=self.headers
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(submit.call_args.args[0], "resolve-ticker")

    def test_resolve_ticker_action_also_retries_export_quarantine(self):
        """A successful resolve should try to un-quarantine matching export
        data in the same click, not just save the mapping."""
        body = {
            "source_symbol": "MDA",
            "canonical_symbol": "MDA",
            "yahoo_symbol": "MDA.TO",
            "currency": "CAD",
        }
        resolved = {"source_symbol": "MDA", "status": "verified"}
        retry_results = [dashboard_api.pipeline_app.SourceResult("export", None, "succeeded", 1)]

        def fake_submit(kind, work):
            return dashboard_api.Job(id="job-x", kind=kind, status="succeeded", result=work())

        with (
            patch.object(
                dashboard_api.ticker_mapping, "resolve_pending_symbol", return_value=resolved
            ),
            patch.object(
                dashboard_api.pipeline_app, "retry_quarantined_exports_for_symbol",
                return_value=retry_results,
            ) as retry,
            patch.object(dashboard_api.runner, "submit", side_effect=fake_submit),
        ):
            response = self.client.post(
                "/api/actions/resolve-ticker", json=body, headers=self.headers
            )

        self.assertEqual(response.status_code, 202)
        retry.assert_called_once_with("MDA", db_path=dashboard_api.db_path())
        result = response.json()["result"]
        self.assertEqual(result["source_symbol"], "MDA")
        self.assertEqual(result["export_retry"][0]["status"], "succeeded")

    def test_retry_ticker_action_queues_a_job(self):
        with patch.object(dashboard_api.runner, "submit") as submit:
            submit.return_value = dashboard_api.Job(id="job-5", kind="retry-ticker")
            response = self.client.post(
                "/api/actions/retry-ticker", json={"source_symbol": "MDA"}, headers=self.headers
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(submit.call_args.args[0], "retry-ticker")

    def test_pending_tickers_is_a_plain_get(self):
        rows = [{"source_symbol": "XYZ", "trade_count": 2, "sources": ["email"]}]
        with patch.object(dashboard_api.ticker_mapping, "list_pending", return_value=rows):
            response = self.client.get("/api/tickers/pending")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), rows)

    def test_job_status_lookup_and_unknown_id(self):
        job = dashboard_api.Job(id="job-4", kind="classify", status="succeeded")
        with patch.object(dashboard_api.runner, "get", return_value=job):
            found = self.client.get("/api/actions/job-4")
        with patch.object(dashboard_api.runner, "get", return_value=None):
            missing = self.client.get("/api/actions/nope")

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["status"], "succeeded")
        self.assertEqual(missing.status_code, 404)


class RunCliTests(unittest.TestCase):
    """The subprocess wrapper actions use to hand the database to a writer."""

    def test_releases_the_connection_before_running_the_writer(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="done\n", stderr="")
        with patch.object(dashboard_api.database, "close_connection") as close:
            with patch.object(dashboard_api.subprocess, "run", return_value=completed) as run:
                result = dashboard_api._run_cli("portfolio-classify")

        close.assert_called_once()
        self.assertEqual(result["command"], "portfolio-classify")
        self.assertEqual(result["output"], "done")
        self.assertIn("portfolio-classify", run.call_args.args[0])

    def test_nonzero_exit_raises_with_stderr_detail(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="boom: missing input"
        )
        with patch.object(dashboard_api.database, "close_connection"):
            with patch.object(dashboard_api.subprocess, "run", return_value=completed):
                with self.assertRaises(RuntimeError) as raised:
                    dashboard_api._run_cli("pipeline")

        self.assertIn("boom: missing input", str(raised.exception))

    def test_classify_action_runs_workflow_then_sync_in_order(self):
        with patch.object(dashboard_api, "_run_cli", side_effect=lambda *a: {"command": " ".join(a)}) as cli:
            result = dashboard_api._classify_and_sync()

        self.assertEqual(
            [call.args for call in cli.call_args_list],
            [("portfolio-classify",), ("classification-sync",)],
        )
        self.assertEqual(len(result["steps"]), 2)


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
