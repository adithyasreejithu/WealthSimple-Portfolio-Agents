"""Tests for the investment-analyst-resources skill.

Builds a real temp DuckDB fixture (via `database.initialize_database` +
direct inserts + `position_engine.recompute_positions`, the same pattern
`test_holdings_reconciler.py` uses) so `db_resources`/`freshness_gate` are
exercised against actual schema and constraints, not mocks. No live network
calls anywhere -- the yfinance top-up is always monkeypatched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb

import database
import position_engine

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "investment-analyst-resources"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db_resources  # noqa: E402
import derived_metrics as dm  # noqa: E402
import freshness_gate  # noqa: E402
import investment_analyst_resources as iar  # noqa: E402
import _decision_fixtures as fx  # noqa: E402

TODAY = date(2026, 8, 4)  # a Tuesday


class FixtureDatabaseTest(unittest.TestCase):
    """Base class: builds one seeded ticker (owned, verified Yahoo mapping) per test."""

    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _seed_ticker(self, symbol="PLTR", *, security_type="stock", owned=True):
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, financial_currency, security_name, security_type)
               VALUES (?, 'NASDAQ', 'USD', 'USD', ?, ?) RETURNING ticker_id""",
            [symbol, f"{symbol} Inc.", security_type],
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO ticker_provider_mappings
               (ticker_id, provider, provider_symbol, verification_status)
               VALUES (?, 'yahoo', ?, 'verified')""",
            [ticker_id, symbol],
        )
        if owned:
            self.connection.execute(
                "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
                "VALUES (?, 'BUY', ?, ?, ?)",
                [date(2025, 1, 2), ticker_id, 10, 1000],
            )
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, date(2025, 1, 3), 100.0, 100.0, 100.0, 100.0, 100.0, 1000],
            )
            position_engine.recompute_positions(self.connection)
        return ticker_id

    def _reopen_read_only(self) -> duckdb.DuckDBPyConnection:
        database.close_connection()
        return db_resources.connect_read_only(self.db_path)

    def _reopen_write(self) -> None:
        """Reacquire the read-write shared connection after a read-only detour."""
        self.connection = database.get_shared_connection(self.db_path)


class FreshnessGateCadenceTest(FixtureDatabaseTest):
    def test_prices_stale_when_no_history_row(self):
        ticker_id = self._seed_ticker()
        self.connection.execute("DELETE FROM historical_records WHERE ticker_id = ?", [ticker_id])
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY)
        self.assertTrue(result["domains"]["prices"]["stale"])
        self.assertIn("prices", result["due_domains"])

    def test_prices_fresh_when_dated_last_business_day(self):
        ticker_id = self._seed_ticker()
        # TODAY is a Tuesday; the last completed business day is Monday.
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, TODAY - timedelta(days=1), 1, 1, 1, 1, 1, 1],
        )
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY)
        self.assertFalse(result["domains"]["prices"]["stale"])
        self.assertNotIn("prices", result["due_domains"])

    def test_financials_cadence_boundary(self):
        ticker_id = self._seed_ticker()
        now = datetime.combine(TODAY, datetime.min.time())
        fresh_at = now - timedelta(days=freshness_gate.FINANCIALS_CADENCE_DAYS - 1)
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, fetched_at) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), fresh_at],
        )
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY)
        self.assertFalse(result["domains"]["financials"]["stale"])
        connection.close()

        self._reopen_write()
        stale_at = now - timedelta(days=freshness_gate.FINANCIALS_CADENCE_DAYS)
        self.connection.execute(
            "UPDATE financial_snapshots SET fetched_at = ? WHERE ticker_id = ?", [stale_at, ticker_id]
        )
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY)
        self.assertTrue(result["domains"]["financials"]["stale"])
        self.assertIn("financials", result["due_domains"])

    def test_earnings_window_forces_stale_regardless_of_cadence(self):
        ticker_id = self._seed_ticker()
        now = datetime.combine(TODAY, datetime.min.time())
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, fetched_at) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), now],
        )
        self.connection.execute(
            "INSERT INTO earnings_events (ticker_id, report_date, fetched_at) VALUES (?, ?, ?)",
            [ticker_id, TODAY + timedelta(days=1), now],
        )
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY)
        self.assertTrue(result["earnings_window"])
        self.assertTrue(result["domains"]["financials"]["stale"])
        self.assertEqual(result["domains"]["financials"]["forced_by"], "earnings_window")
        self.assertIn("financials", result["due_domains"])

    def test_not_owned_ticker_cannot_refresh(self):
        self._seed_ticker("WATCH", owned=False)
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "WATCH", run_date=TODAY)
        self.assertFalse(result["owned"])
        self.assertFalse(result["can_refresh"])
        self.assertEqual(result["due_domains"], [])
        self.assertTrue(any("not owned" in gap for gap in result["gaps"]))

    def test_classification_and_positions_are_never_in_due_domains(self):
        self._seed_ticker()
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY, force=True)
        self.assertNotIn("classification", result["due_domains"])
        self.assertNotIn("positions", result["due_domains"])
        self.assertFalse(result["domains"]["classification"]["refreshable"])
        self.assertFalse(result["domains"]["positions"]["refreshable"])

    def test_force_refresh_marks_every_refreshable_domain_due(self):
        ticker_id = self._seed_ticker()
        now = datetime.combine(TODAY, datetime.min.time())
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, fetched_at) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), now],
        )
        self.connection.execute(
            "INSERT INTO earnings_events (ticker_id, report_date, fetched_at) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 6, 1), now],
        )
        self.connection.execute(
            "INSERT INTO dividend_events (ticker_id, ex_dividend_date, declared_amount, fetched_at) VALUES (?, ?, ?, ?)",
            [ticker_id, date(2025, 6, 1), 0.5, now],
        )
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, TODAY - timedelta(days=1), 1, 1, 1, 1, 1, 1],
        )
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "PLTR", run_date=TODAY, force=True)
        self.assertEqual(set(result["due_domains"]), {"prices", "earnings", "dividends", "financials"})

    def test_unresolvable_ticker(self):
        self._seed_ticker()
        connection = self._reopen_read_only()
        result = freshness_gate.compute_freshness(connection, "NOPE", run_date=TODAY)
        self.assertFalse(result["resolved"])
        self.assertTrue(result["gaps"])


class DbResourcesReadTest(FixtureDatabaseTest):
    def test_position_none_when_not_held(self):
        self._seed_ticker("WATCH", owned=False)
        connection = self._reopen_read_only()
        identity = db_resources.resolve_ticker(connection, "WATCH")
        self.assertIsNone(db_resources.read_position(connection, identity["ticker_id"]))

    def test_price_history_aggregates_exclude_nothing_but_summarize(self):
        ticker_id = self._seed_ticker()
        for offset, close in enumerate([100.0, 110.0, 90.0, 120.0]):
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, date(2025, 2, 1) + timedelta(days=offset), close, close, close, close, close, 10],
            )
        connection = self._reopen_read_only()
        prices = db_resources.read_price_history(connection, ticker_id)
        self.assertEqual(prices["count"], 5)  # 4 new + 1 from _seed_ticker
        self.assertEqual(prices["latest_close"], 120.0)
        self.assertAlmostEqual(prices["week52_high"], 120.0)
        self.assertIn("rows", prices)
        self.assertEqual(len(prices["rows"]), 5)

    def test_financial_snapshots_extra_json_round_trips(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, revenue, extra) VALUES (?, ?, ?, ?)",
            [ticker_id, date(2025, 12, 31), 1000.0, json.dumps({"totalAssets": 500.0})],
        )
        connection = self._reopen_read_only()
        rows = db_resources.read_financial_snapshots(connection, ticker_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["extra"], {"totalAssets": 500.0})

    def test_dividends_received_aggregates_div_transactions(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, credit) VALUES (?, 'DIV', ?, ?)",
            [date(2025, 6, 1), ticker_id, 12.5],
        )
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, credit) VALUES (?, 'DIV', ?, ?)",
            [date(2025, 9, 1), ticker_id, 12.5],
        )
        connection = self._reopen_read_only()
        received = db_resources.read_dividends_received(connection, ticker_id)
        self.assertEqual(float(received["total_received_cad"]), 25.0)
        self.assertEqual(received["payment_count"], 2)
        self.assertEqual(received["last_payment_date"], date(2025, 9, 1))

    def test_portfolio_context_missing_file_and_not_held_is_a_gap_not_an_error(self):
        self._seed_ticker("WATCH", owned=False)
        connection = self._reopen_read_only()
        identity = db_resources.resolve_ticker(connection, "WATCH")
        result = db_resources.read_portfolio_context(
            connection, identity["ticker_id"], "WATCH", classification_json=Path("/nonexistent.json")
        )
        self.assertIsNone(result)

    def test_portfolio_context_role_from_export_value_and_weight_computed_live(self):
        ticker_id = self._seed_ticker()  # PLTR, 10 shares; _seed_ticker's own close is $100 on 2025-01-03
        # A later, different close -- proves value/weight come from this, not the JSON below.
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 6, 1), 150.0, 150.0, 150.0, 150.0, 150.0, 1000],
        )
        export = Path(self.temp_dir.name) / "classification.json"
        export.write_text(
            json.dumps({
                "generated_at": "2026-08-01T00:00:00",
                "holdings": [
                    {"ticker": "PLTR", "primary_group": "Growth",
                     "fields": {"current_weight_percent": 5.5, "position_market_value": 1000.0}}
                ],
            }),
            encoding="utf-8",
        )
        connection = self._reopen_read_only()
        result = db_resources.read_portfolio_context(connection, ticker_id, "PLTR", classification_json=export)
        self.assertEqual(result["role"], "Growth")
        # Only holding in the fixture -> 100% weight; value from the live $150 close
        # (10 shares, no FX pair seeded so fx_rate falls back to 1), not the export's stale 5.5%/$1000.
        self.assertEqual(result["weight_pct"], 100.0)
        self.assertEqual(result["position_market_value"], 1500.0)

    def test_portfolio_context_value_and_weight_live_even_without_export(self):
        ticker_id = self._seed_ticker()  # PLTR, 10 shares @ $100 close
        connection = self._reopen_read_only()
        result = db_resources.read_portfolio_context(
            connection, ticker_id, "PLTR", classification_json=Path("/nonexistent.json")
        )
        self.assertIsNone(result["role"])
        self.assertEqual(result["weight_pct"], 100.0)
        self.assertEqual(result["position_market_value"], 1000.0)


class DigestBundleShapeTest(FixtureDatabaseTest):
    def _gate_result(self, connection, ticker="PLTR"):
        return freshness_gate.compute_freshness(connection, ticker, run_date=TODAY)

    def test_digest_omits_raw_rows_and_stays_under_soft_limit(self):
        ticker_id = self._seed_ticker()
        for offset in range(300):
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, date(2024, 1, 1) + timedelta(days=offset), 1, 1, 1, 1, 1, 1],
            )
        for offset in range(20):
            self.connection.execute(
                "INSERT INTO position_ledger "
                "(ticker_id, event_date, event_type, source, source_id, quantity_delta, running_quantity, running_book_cad, running_book_mkt) "
                "VALUES (?, ?, 'BUY', 'activities', ?, 1, ?, 100, 100)",
                [ticker_id, date(2024, 1, 1) + timedelta(days=offset), offset + 1, offset + 1],
            )
        connection = self._reopen_read_only()
        gate_result = self._gate_result(connection)
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        derived = {**dm.compute_live_metrics({}), **dm.compute_db_metrics(db_bundle)}
        digest = iar.build_digest(
            "PLTR", gate_result, {}, db_bundle, {}, {}, derived, None, no_live=True, no_quote=True, today=TODAY,
        )
        digest["trace"] = iar.compute_completeness_trace(digest, gate_result)
        digest_text = json.dumps(db_resources.to_json_safe(digest))
        self.assertNotIn("rows", digest["prices"])
        self.assertLess(len(digest_text.encode("utf-8")), iar.DIGEST_SOFT_LIMIT_BYTES)

    def test_bundle_includes_full_ledger_and_price_rows(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        gate_result = self._gate_result(connection)
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        derived = {**dm.compute_live_metrics({}), **dm.compute_db_metrics(db_bundle)}
        bundle = iar.build_bundle("PLTR", gate_result, {}, db_bundle, {}, {}, derived, None, no_quote=True, today=TODAY)
        self.assertIn("rows", bundle["db"]["prices"])
        self.assertIsInstance(bundle["db"]["ledger"], list)


class OrchestratorModeTest(FixtureDatabaseTest):
    def test_mode_gate_writes_nothing(self):
        self._seed_ticker()
        database.close_connection()
        results = iar._run_gate(["PLTR"], self.db_path, force=False, run_date=TODAY)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["resolved"])
        # Gate mode has no bundle-writing code path at all -- nothing to assert
        # beyond the freshness verdict itself (no file I/O happens in _run_gate).

    def test_process_ticker_not_owned_falls_back_to_live_only(self):
        self._seed_ticker("WATCH", owned=False)
        database.close_connection()
        connection = db_resources.connect_read_only(self.db_path)
        gate_result = freshness_gate.compute_freshness(connection, "WATCH", run_date=TODAY)
        connection.close()

        output_path = Path(self.temp_dir.name) / "watch.json"
        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        with patch.object(iar, "fetch_stock_research_data", return_value=[
            {"ticker": "WATCH", "provider_symbol": "WATCH", "data": {"overview": {"longName": "Watch Co"}}, "errors": {}}
        ]) as mock_fetch:
            result = iar.process_ticker(
                "WATCH", gate_result, {}, self.db_path,
                no_live=False, no_bundle=False, no_trace=False, no_quote=True, output=output_path,
                classification_json=Path("/nonexistent.json"), trace_log_path=trace_log_path,
                today=TODAY, pretty=False,
            )
        mock_fetch.assert_called_once()
        self.assertIsNone(result["digest"]["position"])
        self.assertTrue(output_path.exists())
        bundle = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["live"]["overview"]["longName"], "Watch Co")
        self.assertIn("trace", result["digest"])
        self.assertTrue(trace_log_path.exists())
        self.assertIn("TRACE | ticker=WATCH", trace_log_path.read_text(encoding="utf-8"))

    def test_no_trace_skips_digest_key_and_log_write(self):
        self._seed_ticker("WATCH", owned=False)
        database.close_connection()
        connection = db_resources.connect_read_only(self.db_path)
        gate_result = freshness_gate.compute_freshness(connection, "WATCH", run_date=TODAY)
        connection.close()

        trace_log_path = Path(self.temp_dir.name) / "no-trace.txt"
        with patch.object(iar, "fetch_stock_research_data", return_value=[
            {"ticker": "WATCH", "provider_symbol": "WATCH", "data": {}, "errors": {}}
        ]):
            result = iar.process_ticker(
                "WATCH", gate_result, {}, self.db_path,
                no_live=False, no_bundle=True, no_trace=True, no_quote=True, output=None,
                classification_json=Path("/nonexistent.json"), trace_log_path=trace_log_path,
                today=TODAY, pretty=False,
            )
        self.assertNotIn("trace", result["digest"])
        self.assertFalse(trace_log_path.exists())


class DerivedLiveMetricsTest(unittest.TestCase):
    """Cross-checked against test_scoring_worksheet.py's DerivedMetricsTest --
    same fixture (`_decision_fixtures.research_payload`), same expected
    values, confirming the ported formulas agree with v1's originals."""

    def setUp(self):
        self.live_data = fx.research_payload()[0]["data"]
        self.live_metrics = dm.compute_live_metrics(self.live_data)

    def test_fcf_yield(self):
        self.assertAlmostEqual(self.live_metrics["fcf_yield"], 0.03, places=6)

    def test_put_call_oi_ratio(self):
        self.assertAlmostEqual(self.live_metrics["put_call_oi_ratio"], 500 / 1500, places=6)

    def test_put_call_volume_ratio(self):
        self.assertAlmostEqual(self.live_metrics["put_call_volume_ratio"], 120 / 240, places=6)

    def test_atm_iv_term_structure(self):
        self.assertAlmostEqual(self.live_metrics["atm_iv_near"], (0.28 + 0.29) / 2, places=6)
        self.assertAlmostEqual(self.live_metrics["atm_iv_far"], (0.24 + 0.25) / 2, places=6)

    def test_iv_skew(self):
        self.assertAlmostEqual(self.live_metrics["iv_skew"], 0.34 - 0.26, places=6)

    def test_max_oi_strikes(self):
        self.assertEqual(self.live_metrics["max_oi_call_strike"], 180.0)
        self.assertEqual(self.live_metrics["max_oi_put_strike"], 180.0)

    def test_net_insider_shares(self):
        self.assertEqual(self.live_metrics["net_insider_shares"], 1200)

    def test_revision_counts(self):
        self.assertEqual(self.live_metrics["upgrades_90d"], 2)
        self.assertEqual(self.live_metrics["downgrades_90d"], 1)
        self.assertEqual(self.live_metrics["net_revisions_365d"], 1)

    def test_missing_inputs_return_none_not_raise(self):
        empty_metrics = dm.compute_live_metrics({})
        for name, value in empty_metrics.items():
            self.assertIsNone(value, f"{name} should be None on empty input, got {value!r}")

    def test_sparse_payload_all_none(self):
        sparse = fx.research_payload_sparse()[0]["data"]
        metrics = dm.compute_live_metrics(sparse)
        self.assertIsNone(metrics["put_call_oi_ratio"])
        self.assertIsNone(metrics["net_insider_shares"])
        # valuation was fetched but has no freeCashflow -> fcf_yield still None.
        self.assertIsNone(metrics["fcf_yield"])


class DerivedDbMetricsTest(FixtureDatabaseTest):
    def test_debt_to_equity_and_current_ratio_surfaced_from_latest_row(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, debt_to_equity, current_ratio) "
            "VALUES (?, ?, ?, ?)",
            [ticker_id, date(2025, 12, 31), 1.75, 0.86],
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertAlmostEqual(metrics["debt_to_equity"], 1.75)
        self.assertAlmostEqual(metrics["current_ratio"], 0.86)
        self.assertIsNone(metrics["net_income_latest_quarter"])  # not inserted in this fixture row

    def test_revenue_growth_yoy_matches_same_quarter_prior_year(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, revenue) VALUES (?, ?, ?)",
            [ticker_id, date(2024, 12, 31), 100.0],
        )
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, revenue) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), 120.0],
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertAlmostEqual(metrics["revenue_growth_yoy"], (120.0 - 100.0) / 100.0, places=6)

    def test_revenue_growth_yoy_none_without_a_prior_year_period(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, revenue) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), 120.0],
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertIsNone(metrics["revenue_growth_yoy"])

    def test_price_returns_from_db_rows(self):
        ticker_id = self._seed_ticker()  # seeds one row: 2025-01-03 close=100.0
        for offset, close in ((400, 150.0), (95, 180.0), (35, 190.0), (0, 200.0)):
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, TODAY - timedelta(days=offset), close, close, close, close, close, 10],
            )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertAlmostEqual(metrics["return_365d"], 200 / 150 - 1, places=6)
        self.assertAlmostEqual(metrics["return_90d"], 200 / 180 - 1, places=6)
        self.assertAlmostEqual(metrics["return_30d"], 200 / 190 - 1, places=6)

    def test_empty_bundle_returns_none_not_raise(self):
        metrics = dm.compute_db_metrics({})
        for name, value in metrics.items():
            self.assertIsNone(value, f"{name} should be None on empty input, got {value!r}")


class CompletenessTraceTest(FixtureDatabaseTest):
    def _digest_for(
        self, connection, ticker_id, ticker, *, no_live=True, live_data=None, live_errors=None, quote=None
    ):
        gate_result = freshness_gate.compute_freshness(connection, ticker, run_date=TODAY)
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, ticker, live_quote=quote)
        live_data = live_data or {}
        derived = {
            **dm.compute_live_metrics(live_data, quote=quote),
            **dm.compute_db_metrics(db_bundle, quote=quote),
        }
        digest = iar.build_digest(
            ticker, gate_result, {}, db_bundle, live_data, live_errors or {}, derived, quote,
            no_live=no_live, no_quote=quote is None, today=TODAY,
        )
        return digest, gate_result

    def test_not_owned_ticker_excludes_position_domains_from_denominator(self):
        ticker_id = self._seed_ticker("WATCH", owned=False)
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "WATCH")
        trace = iar.compute_completeness_trace(digest, gate_result)
        self.assertNotIn("position.quantity", trace["missing_fields"])
        self.assertNotIn("ledger_summary.number_of_buys", trace["missing_fields"])
        self.assertGreater(trace["domains_empty"], 0)

    def test_owned_ticker_with_full_position_scores_full_completeness_for_those_domains(self):
        ticker_id = self._seed_ticker()  # owned, position_snapshots populated
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "PLTR")
        trace = iar.compute_completeness_trace(digest, gate_result)
        self.assertNotIn("position.quantity", trace["missing_fields"])
        self.assertNotIn("position.computed_at", trace["missing_fields"])

    def test_etf_missing_financials_is_empty_expected_not_failed(self):
        ticker_id = self._seed_ticker("VFV", security_type="etf")
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "VFV")
        trace = iar.compute_completeness_trace(digest, gate_result)
        self.assertFalse(any(m.startswith("financials.") for m in trace["missing_fields"]))
        self.assertFalse(any(m.startswith("earnings.") for m in trace["missing_fields"]))

    def test_live_group_failed_counts_against_completeness(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False,
            live_data={"overview": {"longName": "Palantir"}},
            live_errors={"valuation": "RuntimeError: blocked"},
        )
        trace = iar.compute_completeness_trace(digest, gate_result)
        self.assertIn("live.valuation", trace["missing_fields"])
        self.assertGreater(trace["domains_failed"], 0)

    def test_stock_funds_group_failure_is_empty_expected(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False,
            live_data={"overview": {"longName": "Palantir"}},
            live_errors={"funds": "TypeError: get_funds_data() raised for equity"},
        )
        trace = iar.compute_completeness_trace(digest, gate_result)
        self.assertNotIn("live.funds", trace["missing_fields"])

    def test_never_divides_by_zero_on_a_degenerate_digest(self):
        # An all-empty-expected digest (not owned, no dividends key either) must
        # still produce a valid percentage, never a ZeroDivisionError.
        trace = iar.compute_completeness_trace({"live": {"skipped": True}}, {"owned": False, "asset_class": "stock"})
        self.assertIsInstance(trace["completeness_pct"], float)
        self.assertGreaterEqual(trace["completeness_pct"], 0.0)

    def test_write_trace_log_appends_pipe_delimited_line(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "PLTR")
        trace = iar.compute_completeness_trace(digest, gate_result)
        log_path = Path(self.temp_dir.name) / "trace.txt"
        iar.write_trace_log("PLTR", gate_result, trace, log_path)
        iar.write_trace_log("PLTR", gate_result, trace, log_path)
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("| TRACE | ticker=PLTR | asset_class=stock |", lines[0])
        self.assertIn(f"completeness_pct={trace['completeness_pct']}", lines[0])

    def test_connect_read_only_reports_actionable_message_while_write_lock_held(self):
        self._seed_ticker()  # holds the read-write shared connection open
        with self.assertRaises(db_resources.DatabaseNotReady) as ctx:
            db_resources.connect_read_only(self.db_path)
        self.assertIn("write lock", str(ctx.exception))


class LiveQuoteValueWeightTest(FixtureDatabaseTest):
    """db_resources.read_live_market_value/read_live_portfolio_weight/
    read_portfolio_context with an injected live_quote."""

    def _quote(self, price: float) -> dict:
        return {"price": price, "as_of": datetime.combine(TODAY, datetime.min.time())}

    def test_market_value_prefers_quote_over_db_close(self):
        ticker_id = self._seed_ticker()  # 10 shares, cost basis $1000, DB close $100
        connection = self._reopen_read_only()
        value = db_resources.read_live_market_value(connection, ticker_id, live_quote=self._quote(150.0))
        self.assertEqual(value["last_price"], 150.0)
        self.assertEqual(value["market_value_mkt"], 1500.0)
        self.assertEqual(value["price_source"], "live_quote")
        self.assertEqual(value["cost_basis_cad"], 1000.0)
        self.assertEqual(value["unrealized_gain_cad"], 500.0)
        self.assertAlmostEqual(value["unrealized_gain_pct"], 50.0, places=6)

    def test_market_value_falls_back_to_db_close_without_quote(self):
        ticker_id = self._seed_ticker()  # 10 shares, cost basis $1000, DB close $100 -> no move, no gain
        connection = self._reopen_read_only()
        value = db_resources.read_live_market_value(connection, ticker_id)
        self.assertEqual(value["last_price"], 100.0)
        self.assertEqual(value["price_source"], "db_close")
        self.assertEqual(value["cost_basis_cad"], 1000.0)
        self.assertEqual(value["unrealized_gain_cad"], 0.0)
        self.assertEqual(value["unrealized_gain_pct"], 0.0)

    def test_unrealized_gain_pct_is_none_when_cost_basis_is_zero(self):
        # A fully-DRIP/adjusted-to-zero cost basis must not raise a ZeroDivisionError.
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "UPDATE position_snapshots SET book_value_cad = 0 WHERE ticker_id = ?", [ticker_id]
        )
        connection = self._reopen_read_only()
        value = db_resources.read_live_market_value(connection, ticker_id)
        self.assertEqual(value["cost_basis_cad"], 0.0)
        self.assertIsNone(value["unrealized_gain_pct"])
        self.assertEqual(value["unrealized_gain_cad"], value["market_value_cad"])

    def test_portfolio_weight_overrides_only_the_target_tickers_price(self):
        pltr_id = self._seed_ticker("PLTR")  # 10 shares @ $100 DB close = $1000
        self._seed_ticker("NVDA")  # 10 shares @ $100 DB close = $1000, untouched
        connection = self._reopen_read_only()
        weight = db_resources.read_live_portfolio_weight(connection, pltr_id, live_quote=self._quote(300.0))
        # PLTR revalued to 3000 via the quote; NVDA stays at its DB value of 1000.
        self.assertAlmostEqual(weight["position_market_value"], 3000.0)
        self.assertAlmostEqual(weight["total_portfolio_value"], 4000.0)
        self.assertAlmostEqual(weight["weight_pct"], 75.0)
        self.assertEqual(weight["price_source"], "live_quote")

    def test_portfolio_context_price_source_and_value_reflect_quote(self):
        ticker_id = self._seed_ticker()  # cost basis $1000
        connection = self._reopen_read_only()
        result = db_resources.read_portfolio_context(
            connection, ticker_id, "PLTR", classification_json=Path("/nonexistent.json"),
            live_quote=self._quote(200.0),
        )
        self.assertEqual(result["price_source"], "live_quote")
        self.assertEqual(result["position_market_value"], 2000.0)
        self.assertEqual(result["cost_basis_cad"], 1000.0)
        self.assertEqual(result["unrealized_gain_cad"], 1000.0)
        self.assertAlmostEqual(result["unrealized_gain_pct"], 100.0, places=6)

    def test_portfolio_context_price_source_db_close_without_quote(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        result = db_resources.read_portfolio_context(
            connection, ticker_id, "PLTR", classification_json=Path("/nonexistent.json"),
        )
        self.assertEqual(result["price_source"], "db_close")
        self.assertEqual(result["position_market_value"], 1000.0)
        self.assertEqual(result["cost_basis_cad"], 1000.0)
        self.assertEqual(result["unrealized_gain_cad"], 0.0)


class DerivedMetricsQuoteTest(unittest.TestCase):
    def test_spot_prefers_quote_over_valuation_current_price(self):
        live_data = {"valuation": {"currentPrice": 100.0}}
        self.assertEqual(dm._spot(live_data, {"price": 105.0}), 105.0)

    def test_spot_falls_back_to_valuation_current_price_without_quote(self):
        live_data = {"valuation": {"currentPrice": 100.0}}
        self.assertEqual(dm._spot(live_data, None), 100.0)

    def test_atm_iv_shifts_when_quote_moves_the_spot(self):
        live_data = fx.research_payload()[0]["data"]  # fixture spot (valuation.currentPrice) is 200
        default_metrics = dm.compute_live_metrics(live_data)
        quoted_metrics = dm.compute_live_metrics(live_data, quote={"price": 220.0})
        # At spot 220 the nearest strike is 220 itself (call IV 0.26, put IV 0.27)
        # instead of 200's (0.28/0.29) -- confirms the quote actually drives the ATM pick.
        self.assertAlmostEqual(quoted_metrics["atm_iv_near"], (0.26 + 0.27) / 2, places=6)
        self.assertNotEqual(default_metrics["atm_iv_near"], quoted_metrics["atm_iv_near"])

    def test_return_over_anchors_on_quote_when_at_least_as_recent_as_last_db_row(self):
        db_bundle = {"prices": {"rows": [{"record_date": date(2026, 7, 1), "close": 100.0}]}}
        quote = {"price": 110.0, "as_of": datetime(2026, 8, 4)}
        self.assertAlmostEqual(dm._return_over(db_bundle, 30, quote), 110.0 / 100.0 - 1.0, places=6)

    def test_return_over_ignores_a_quote_older_than_the_last_db_row(self):
        db_bundle = {"prices": {"rows": [
            {"record_date": date(2026, 7, 1), "close": 100.0},
            {"record_date": date(2026, 8, 1), "close": 120.0},
        ]}}
        stale_quote = {"price": 999.0, "as_of": datetime(2026, 7, 15)}
        with_stale_quote = dm._return_over(db_bundle, 30, stale_quote)
        without_quote = dm._return_over(db_bundle, 30, None)
        self.assertAlmostEqual(with_stale_quote, without_quote, places=6)

    def test_return_over_none_quote_matches_omitted_quote(self):
        db_bundle = {"prices": {"rows": [
            {"record_date": date(2026, 7, 1), "close": 100.0},
            {"record_date": date(2026, 8, 1), "close": 120.0},
        ]}}
        self.assertEqual(dm._return_over(db_bundle, 30, None), dm._return_over(db_bundle, 30))


class CompletenessTraceQuoteTest(unittest.TestCase):
    GATE_RESULT = {"owned": False, "asset_class": "stock"}

    def test_quote_ok_counts_toward_completeness(self):
        digest = {"live": {"skipped": True}, "quote": {"price": 100.0, "as_of": datetime(2026, 8, 4)}}
        trace = iar.compute_completeness_trace(digest, self.GATE_RESULT)
        self.assertNotIn("quote.price", trace["missing_fields"])
        self.assertGreater(trace["domains_ok"], 0)

    def test_quote_skipped_excluded_from_denominator(self):
        digest_with = {"live": {"skipped": True}, "quote": {"price": 100.0, "as_of": datetime(2026, 8, 4)}}
        digest_without = {"live": {"skipped": True}, "quote": {"skipped": True}}
        trace_with = iar.compute_completeness_trace(digest_with, self.GATE_RESULT)
        trace_without = iar.compute_completeness_trace(digest_without, self.GATE_RESULT)
        self.assertEqual(trace_without["fields_total"], trace_with["fields_total"] - 2)
        self.assertNotIn("quote.price", trace_without["missing_fields"])

    def test_quote_none_counts_as_a_failed_domain(self):
        digest = {"live": {"skipped": True}, "quote": None}
        trace = iar.compute_completeness_trace(digest, self.GATE_RESULT)
        self.assertIn("quote.price", trace["missing_fields"])
        self.assertIn("quote.as_of", trace["missing_fields"])
        self.assertGreater(trace["domains_failed"], 0)


class ProcessTickerQuoteTest(FixtureDatabaseTest):
    def _gate_result(self, ticker="PLTR"):
        database.close_connection()
        connection = db_resources.connect_read_only(self.db_path)
        try:
            return freshness_gate.compute_freshness(connection, ticker, run_date=TODAY)
        finally:
            connection.close()

    def test_no_quote_skips_fetch_and_keeps_db_close_behavior(self):
        self._seed_ticker()  # 10 shares @ $100 DB close
        gate_result = self._gate_result()
        with patch.object(iar, "_fetch_latest_quote") as mock_quote:
            result = iar.process_ticker(
                "PLTR", gate_result, {}, self.db_path,
                no_live=True, no_bundle=True, no_trace=True, no_quote=True, output=None,
                classification_json=Path("/nonexistent.json"),
                trace_log_path=Path(self.temp_dir.name) / "t.txt", today=TODAY, pretty=False,
            )
        mock_quote.assert_not_called()
        self.assertEqual(result["digest"]["quote"], {"skipped": True})
        self.assertEqual(result["digest"]["portfolio_context"]["price_source"], "db_close")
        self.assertEqual(result["digest"]["portfolio_context"]["position_market_value"], 1000.0)

    def test_quote_fetched_and_threaded_through_value_weight_and_derived(self):
        self._seed_ticker()  # 10 shares @ $100 DB close
        gate_result = self._gate_result()
        fake_quote = {"price": 250.0, "as_of": datetime.combine(TODAY, datetime.min.time()), "source": "fast_info"}
        with patch.object(iar, "_fetch_latest_quote", return_value=fake_quote) as mock_quote:
            result = iar.process_ticker(
                "PLTR", gate_result, {}, self.db_path,
                no_live=True, no_bundle=True, no_trace=True, no_quote=False, output=None,
                classification_json=Path("/nonexistent.json"),
                trace_log_path=Path(self.temp_dir.name) / "t2.txt", today=TODAY, pretty=False,
            )
        mock_quote.assert_called_once()
        digest = result["digest"]
        self.assertEqual(digest["quote"]["price"], 250.0)
        self.assertEqual(digest["portfolio_context"]["price_source"], "live_quote")
        self.assertEqual(digest["portfolio_context"]["position_market_value"], 2500.0)


if __name__ == "__main__":
    unittest.main()
