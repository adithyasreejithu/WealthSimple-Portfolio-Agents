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
import skill_trace  # noqa: E402
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
        digest["trace"] = iar.build_trace(digest, gate_result, "PLTR").to_dict()
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
        self.assertIn(
            "TRACE | skill=investment-analyst-resources | subject=WATCH",
            trace_log_path.read_text(encoding="utf-8"),
        )

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


class RunWorkspaceIntegrationTest(FixtureDatabaseTest):
    """`--run-id` routes the bundle into a run workspace instead of exports/.

    The point of the migration: an artifact produced for a question belongs to
    that question's run, registered as evidence, rather than accumulating in a
    shared directory with nothing tying it to why it was fetched.
    """

    def _make_run(self):
        import config as config_module
        from workspace import run as run_module

        base = Path(self.temp_dir.name) / "ws"
        runs_root = base / "runs"
        runs_root.mkdir(parents=True)
        patcher = patch.multiple(
            config_module, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        request = base / "request.yaml"
        request.write_text(
            "schema_version: '1.0'\nmode: portfolio_check\n"
            "subject: {type: security, identifiers: {ticker: PLTR}}\n"
            "request: {question: 'Synthetic run for the migration test.'}\n",
            encoding="utf-8",
        )
        return run_module.create_run(request)

    def _gate(self, ticker="PLTR"):
        database.close_connection()
        connection = db_resources.connect_read_only(self.db_path)
        try:
            return freshness_gate.compute_freshness(connection, ticker, run_date=TODAY)
        finally:
            connection.close()

    def test_run_id_writes_the_bundle_into_the_run_and_registers_evidence(self):
        from workspace import audit, evidence as evidence_module

        self._seed_ticker()
        run_id, run_dir = self._make_run()
        gate_result = self._gate()

        with patch.object(iar, "fetch_stock_research_data", return_value=[
            {"ticker": "PLTR", "provider_symbol": "PLTR", "data": {}, "errors": {}}
        ]):
            result = iar.process_ticker(
                "PLTR", gate_result, {}, self.db_path,
                no_live=True, no_bundle=False, no_trace=False, no_quote=True, output=None,
                classification_json=Path("/nonexistent.json"),
                trace_log_path=Path(self.temp_dir.name) / "trace.txt",
                today=TODAY, pretty=False, run_dir=run_dir, run_id=run_id,
            )

        bundle_path = Path(result["bundle_path"])
        self.assertEqual(bundle_path.parent, run_dir / "evidence")
        self.assertTrue(bundle_path.is_file())

        records = evidence_module.read_records(run_dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["evidence_type"], "market_data_bundle")
        self.assertEqual(records[0]["artifact_path"], f"evidence/{bundle_path.name}")
        self.assertEqual(
            records[0]["content_hash"], evidence_module.content_hash(bundle_path)
        )

        kinds = [event["event"] for event in audit.read_events(run_dir)]
        self.assertIn("trace_recorded", kinds)
        self.assertIn("evidence_registered", kinds)

    def test_run_stays_valid_after_the_skill_writes_into_it(self):
        from workspace import run as run_module, validation

        self._seed_ticker()
        run_id, run_dir = self._make_run()
        gate_result = self._gate()

        with patch.object(iar, "fetch_stock_research_data", return_value=[
            {"ticker": "PLTR", "provider_symbol": "PLTR", "data": {}, "errors": {}}
        ]):
            iar.process_ticker(
                "PLTR", gate_result, {}, self.db_path,
                no_live=True, no_bundle=False, no_trace=False, no_quote=True, output=None,
                classification_json=Path("/nonexistent.json"),
                trace_log_path=Path(self.temp_dir.name) / "trace.txt",
                today=TODAY, pretty=False, run_dir=run_dir, run_id=run_id,
            )
        run_module.rebuild_manifest(run_dir)
        result = validation.validate_run(run_dir)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["counts"]["evidence"], 1)

    def test_without_run_id_the_bundle_still_goes_to_the_supplied_output(self):
        """The no-run path must be byte-for-byte unchanged, so every existing
        agent instruction and script keeps working."""
        self._seed_ticker()
        gate_result = self._gate()
        output_path = Path(self.temp_dir.name) / "legacy.json"

        with patch.object(iar, "fetch_stock_research_data", return_value=[
            {"ticker": "PLTR", "provider_symbol": "PLTR", "data": {}, "errors": {}}
        ]):
            result = iar.process_ticker(
                "PLTR", gate_result, {}, self.db_path,
                no_live=True, no_bundle=False, no_trace=True, no_quote=True, output=output_path,
                classification_json=Path("/nonexistent.json"),
                today=TODAY, pretty=False,
            )
        self.assertEqual(Path(result["bundle_path"]), output_path)
        self.assertTrue(output_path.is_file())


class DefaultOnRunCreationTest(FixtureDatabaseTest):
    """A run must exist without anyone remembering to pass --run-id.

    Before this, a run only existed if whoever composed the command line
    chose to add --run-id -- a judgment call an LLM agent could skip, and the
    old SKILL.md text ("Omit for the unchanged exports/ behavior") actively
    encouraged skipping it. An audit trail cannot tolerate that: an absent run
    looked identical to "no pull happened." These tests drive the real CLI
    entry point (`iar.main`), not `process_ticker` directly, because the
    decision under test lives in `_resolve_run`/`_dispatch`, not the plumbing.
    """

    def setUp(self):
        super().setUp()
        base = Path(self.temp_dir.name) / "ws"
        self.runs_root = base / "runs"
        self.runs_root.mkdir(parents=True)
        import config as config_module

        patcher = patch.multiple(
            config_module, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=self.runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Simulates an invocation with no --db-path override, the case that
        # matters for real usage: omitting --db-path makes argparse's default
        # equal this seeded db, so `args.db_path != DATABASE_PATH` is False
        # and the workspace default-on path is exercised rather than skipped.
        db_patcher = patch.object(iar, "DATABASE_PATH", self.db_path)
        db_patcher.start()
        self.addCleanup(db_patcher.stop)
        self._fetch_patcher = patch.object(
            iar, "fetch_stock_research_data",
            return_value=[{"ticker": "PLTR", "provider_symbol": "PLTR", "data": {}, "errors": {}}],
        )
        self._fetch_patcher.start()
        self.addCleanup(self._fetch_patcher.stop)

    def _run_main(self, argv: list[str]) -> int:
        import io
        from contextlib import redirect_stdout

        # The CLI opens its own connection(s) to self.db_path; the shared
        # read-write connection FixtureDatabaseTest.setUp already holds must
        # be released first or DuckDB refuses the second connection.
        database.close_connection()
        # Every default-on path emits a trace; without an explicit path it
        # falls back to the real logs/SkillTrace.txt, which a test must never
        # write into. Callers that care about trace content pass their own.
        if "--trace-log-path" not in argv:
            argv = argv + ["--trace-log-path", str(Path(self.temp_dir.name) / "default-trace.txt")]
        with redirect_stdout(io.StringIO()):
            return iar.main(argv)

    def test_mode_read_with_no_run_id_still_opens_a_run(self):
        self._seed_ticker()
        exit_code = self._run_main([
            "--ticker", "PLTR", "--mode", "read", "--no-live", "--no-quote",
        ])
        self.assertEqual(exit_code, 0)
        created = list(self.runs_root.iterdir())
        self.assertEqual(len(created), 1)
        bundles = list((created[0] / "evidence").glob("PLTR-*-resources.json"))
        self.assertEqual(len(bundles), 1)

    def test_default_mode_with_no_run_id_still_opens_a_run(self):
        self._seed_ticker()
        exit_code = self._run_main([
            "--ticker", "PLTR", "--no-live", "--no-quote", "--no-refresh",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(list(self.runs_root.iterdir())), 1)

    def test_gate_mode_opens_no_run(self):
        self._seed_ticker()
        self._run_main(["--ticker", "PLTR", "--mode", "gate"])
        self.assertEqual(list(self.runs_root.iterdir()), [])

    def test_refresh_mode_opens_no_run(self):
        # Deliberately unseeded: an unresolved ticker short-circuits refresh
        # before any real sync/network call, since only the workspace
        # decision is under test here, not refresh's own behavior (covered
        # elsewhere).
        self._run_main(["--ticker", "PLTR", "--mode", "refresh"])
        self.assertEqual(list(self.runs_root.iterdir()), [])

    def test_no_run_flag_opts_out_and_writes_to_exports_instead(self):
        self._seed_ticker()
        output_path = Path(self.temp_dir.name) / "opted-out.json"
        exit_code = self._run_main([
            "--ticker", "PLTR", "--mode", "read", "--no-live", "--no-quote",
            "--no-run", "--output", str(output_path),
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(list(self.runs_root.iterdir()), [])
        self.assertTrue(output_path.is_file())

    def test_no_run_opt_out_is_recorded_in_the_trace_not_silent(self):
        """The whole point of recording a skip: an auditor greps
        logs/SkillTrace.jsonl and sees exactly why no run exists for this
        pull, instead of a gap that looks like nothing happened."""
        self._seed_ticker()
        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        output_path = Path(self.temp_dir.name) / "skipped.json"
        self._run_main([
            "--ticker", "PLTR", "--mode", "read", "--no-live", "--no-quote",
            "--no-run", "--trace-log-path", str(trace_log_path), "--output", str(output_path),
        ])
        line = trace_log_path.read_text(encoding="utf-8")
        self.assertIn("run=skipped(--no-run)", line)
        record = json.loads(trace_log_path.with_suffix(".jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["workspace"], {"status": "skipped", "run_id": None, "skip_reason": "--no-run"})

    def test_non_default_db_path_opts_out_and_is_recorded(self):
        """The existing test/debug convention: pointing --db-path somewhere
        else must not populate the real workspace/runs/ during a test run."""
        other_db = Path(self.temp_dir.name) / "other.duckdb"
        database.close_connection()
        database.initialize_database(other_db)
        connection = database.get_shared_connection(other_db)
        connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, financial_currency, security_name, security_type)
               VALUES ('PLTR', 'NASDAQ', 'USD', 'USD', 'Palantir', 'stock')"""
        )
        database.close_connection()

        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        output_path = Path(self.temp_dir.name) / "other-db.json"
        self._run_main([
            "--ticker", "PLTR", "--mode", "read", "--no-live", "--no-quote",
            "--db-path", str(other_db), "--trace-log-path", str(trace_log_path),
            "--output", str(output_path),
        ])
        self.assertEqual(list(self.runs_root.iterdir()), [])
        record = json.loads(trace_log_path.with_suffix(".jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["workspace"]["skip_reason"], "non-default-db")

    def test_run_id_and_no_run_together_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            iar.parse_args(["--ticker", "PLTR", "--run-id", "x", "--no-run"])

    def test_explicit_run_id_still_names_the_run(self):
        self._seed_ticker()
        self._run_main([
            "--ticker", "PLTR", "--mode", "read", "--no-live", "--no-quote", "--run-id", "named-run",
        ])
        self.assertTrue((self.runs_root / "named-run").is_dir())


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

    def test_roe_and_peg_pass_through_from_shared_fixture(self):
        # research_payload()'s valuation group already carries these two --
        # confirms the new pass-throughs wire into the existing fixture
        # without needing a bespoke live_data dict.
        self.assertAlmostEqual(self.live_metrics["roe"], 1.5)
        self.assertAlmostEqual(self.live_metrics["peg_ratio"], 2.4)


class DerivedLiveRatioMetricsTest(unittest.TestCase):
    """Phase 2 ratio pass-throughs -- see
    docs/plans/implementation/phase-2/design-decisions.md. These read
    provider-computed fields `fetch-stock-research-data` already fetches in
    the `valuation` group (`VALUATION_INFO_KEYS`); no new live fetch."""

    def test_ev_to_ebitda_and_ev_to_revenue_pass_through(self):
        live_data = {"valuation": {"enterpriseToEbitda": 18.5, "enterpriseToRevenue": 6.2}}
        metrics = dm.compute_live_metrics(live_data)
        self.assertAlmostEqual(metrics["ev_to_ebitda"], 18.5)
        self.assertAlmostEqual(metrics["ev_to_revenue"], 6.2)

    def test_ev_to_ebitda_passes_through_negative_without_filtering(self):
        # Negative EBITDA makes for a negative ratio -- that is signal, not
        # noise to hide; "no judgment" means reporting it, not suppressing it.
        live_data = {"valuation": {"enterpriseToEbitda": -12.0}}
        metrics = dm.compute_live_metrics(live_data)
        self.assertAlmostEqual(metrics["ev_to_ebitda"], -12.0)

    def test_peg_ratio_prefers_trailing_over_forward_mix(self):
        live_data = {"valuation": {"pegRatio": 2.4, "trailingPegRatio": 2.1}}
        metrics = dm.compute_live_metrics(live_data)
        self.assertAlmostEqual(metrics["peg_ratio"], 2.1)

    def test_peg_ratio_falls_back_to_forward_mix_when_trailing_missing(self):
        live_data = {"valuation": {"pegRatio": 2.4}}
        metrics = dm.compute_live_metrics(live_data)
        self.assertAlmostEqual(metrics["peg_ratio"], 2.4)

    def test_missing_valuation_group_is_none_not_raise(self):
        metrics = dm.compute_live_metrics({})
        self.assertIsNone(metrics["ev_to_ebitda"])
        self.assertIsNone(metrics["ev_to_revenue"])
        self.assertIsNone(metrics["roe"])
        self.assertIsNone(metrics["peg_ratio"])


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

    def test_short_history_returns_none_instead_of_duplicating_the_earliest_price(self):
        # Regression for the return_30d==90d==365d defect (Phase 2, Decision
        # 3): history only reaches back 40 days, so return_90d/return_365d
        # have no point at or before their cutoffs. Previously both silently
        # fell back to the earliest available close, making them identical
        # to each other (and possibly to return_30d) instead of reporting
        # that the wider windows are simply unavailable.
        ticker_id = self._seed_ticker()  # seeds one row: 2025-01-03 close=100.0 (>1 year before TODAY)
        for offset, close in ((40, 180.0), (0, 200.0)):
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, TODAY - timedelta(days=offset), close, close, close, close, close, 10],
            )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        # Drop the seeded 2025-01-03 row from the bundle's price rows so
        # history genuinely starts 40 days back, matching the scenario the
        # defect actually manifested for (a newly-added ticker).
        db_bundle["prices"]["rows"] = [
            row for row in db_bundle["prices"]["rows"] if row["record_date"] != date(2025, 1, 3)
        ]
        metrics = dm.compute_db_metrics(db_bundle)
        # cutoff for the 30d window (TODAY-30) is still after the only
        # available past point (TODAY-40), so it correctly resolves to that
        # point rather than needing one exactly 30 days back.
        self.assertAlmostEqual(metrics["return_30d"], 200 / 180 - 1, places=6)
        self.assertIsNone(metrics["return_90d"])
        self.assertIsNone(metrics["return_365d"])

    def test_empty_bundle_returns_none_not_raise(self):
        metrics = dm.compute_db_metrics({})
        for name, value in metrics.items():
            self.assertIsNone(value, f"{name} should be None on empty input, got {value!r}")


class DerivedDbRatioMetricsTest(FixtureDatabaseTest):
    """Phase 2 ratio functions sourced from `financial_snapshots.extra` --
    see docs/plans/implementation/phase-2/design-decisions.md. The `extra`
    shape below (`Net Debt`, `EBITDA`, `EBIT`, `Tax Provision`,
    `Pretax Income`, `Invested Capital` under `balance_sheet`/
    `income_statement`) mirrors what `_extra_line_items` actually persists --
    confirmed against a real portfolio ticker's stored row, not assumed."""

    def _insert_snapshot(self, ticker_id: int, *, extra: dict) -> None:
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, extra) VALUES (?, ?, ?)",
            [ticker_id, date(2025, 12, 31), json.dumps(extra)],
        )

    def test_net_debt_to_ebitda(self):
        ticker_id = self._seed_ticker()
        self._insert_snapshot(
            ticker_id,
            extra={
                "balance_sheet": {"Net Debt": 40_000.0},
                "income_statement": {"EBITDA": 10_000.0},
            },
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertAlmostEqual(metrics["net_debt_to_ebitda"], 4.0)

    def test_net_debt_to_ebitda_none_when_ebitda_non_positive(self):
        ticker_id = self._seed_ticker()
        self._insert_snapshot(
            ticker_id,
            extra={
                "balance_sheet": {"Net Debt": 40_000.0},
                "income_statement": {"EBITDA": -5_000.0},
            },
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertIsNone(metrics["net_debt_to_ebitda"])

    def test_roic(self):
        ticker_id = self._seed_ticker()
        self._insert_snapshot(
            ticker_id,
            extra={
                "balance_sheet": {"Invested Capital": 100_000.0},
                "income_statement": {
                    "EBIT": 20_000.0,
                    "Tax Provision": 4_000.0,
                    "Pretax Income": 16_000.0,
                },
            },
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        # tax_rate = 4000/16000 = 0.25; NOPAT = 20000 * 0.75 = 15000; ROIC = 15000/100000
        self.assertAlmostEqual(metrics["roic"], 0.15)

    def test_roic_none_on_loss_quarter_pretax_income(self):
        ticker_id = self._seed_ticker()
        self._insert_snapshot(
            ticker_id,
            extra={
                "balance_sheet": {"Invested Capital": 100_000.0},
                "income_statement": {
                    "EBIT": -2_000.0,
                    "Tax Provision": -500.0,
                    "Pretax Income": -3_000.0,
                },
            },
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertIsNone(metrics["roic"])

    def test_roic_none_without_invested_capital(self):
        ticker_id = self._seed_ticker()
        self._insert_snapshot(
            ticker_id,
            extra={
                "income_statement": {
                    "EBIT": 20_000.0,
                    "Tax Provision": 4_000.0,
                    "Pretax Income": 16_000.0,
                },
            },
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertIsNone(metrics["roic"])

    def test_does_not_collide_with_existing_debt_to_equity_and_current_ratio(self):
        # Decision 2: the new ratios read Net Debt/EBITDA/EBIT/Invested
        # Capital -- none of which `_debt_to_equity`/`_current_ratio` touch
        # -- so both old and new metrics are populated independently from
        # the same row without one overwriting the other.
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO financial_snapshots (ticker_id, period_end_date, debt_to_equity, current_ratio, extra) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ticker_id,
                date(2025, 12, 31),
                1.75,
                0.86,
                json.dumps(
                    {
                        "balance_sheet": {"Net Debt": 40_000.0, "Invested Capital": 100_000.0},
                        "income_statement": {
                            "EBITDA": 10_000.0,
                            "EBIT": 20_000.0,
                            "Tax Provision": 4_000.0,
                            "Pretax Income": 16_000.0,
                        },
                    }
                ),
            ],
        )
        connection = self._reopen_read_only()
        db_bundle = db_resources.read_ticker_bundle(connection, ticker_id, "PLTR")
        metrics = dm.compute_db_metrics(db_bundle)
        self.assertAlmostEqual(metrics["debt_to_equity"], 1.75)
        self.assertAlmostEqual(metrics["current_ratio"], 0.86)
        self.assertAlmostEqual(metrics["net_debt_to_ebitda"], 4.0)
        self.assertAlmostEqual(metrics["roic"], 0.15)


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

    def test_not_owned_ticker_reports_position_domains_as_not_applicable(self):
        ticker_id = self._seed_ticker("WATCH", owned=False)
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "WATCH")
        trace = iar.build_trace(digest, gate_result, "WATCH")
        self.assertNotIn("position.quantity", trace.missing_fields())
        self.assertNotIn("ledger_summary.number_of_buys", trace.missing_fields())
        # Not a gap: an unheld ticker has no position to be missing.
        self.assertIn("position.quantity", trace.not_applicable_fields())
        self.assertGreater(trace.domains_not_applicable, 0)

    def test_owned_ticker_with_full_position_scores_full_completeness_for_those_domains(self):
        ticker_id = self._seed_ticker()  # owned, position_snapshots populated
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "PLTR")
        trace = iar.build_trace(digest, gate_result, "PLTR")
        self.assertNotIn("position.quantity", trace.missing_fields())
        self.assertNotIn("position.computed_at", trace.missing_fields())

    def test_etf_financials_and_earnings_are_not_applicable(self):
        ticker_id = self._seed_ticker("VFV", security_type="etf")
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "VFV")
        trace = iar.build_trace(digest, gate_result, "VFV")
        self.assertFalse(any(m.startswith("financials.") for m in trace.missing_fields()))
        self.assertFalse(any(m.startswith("earnings.") for m in trace.missing_fields()))
        # A fund has no balance sheet, so equity metrics must not be graded.
        self.assertFalse(any(m.startswith("derived.financials.") for m in trace.missing_fields()))
        self.assertTrue(any(n.startswith("derived.financials.") for n in trace.not_applicable_fields()))

    def test_live_group_failed_counts_against_completeness(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        live_data = {"overview": {"longName": "Palantir"}}
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False, live_data=live_data,
            live_errors={"valuation": "RuntimeError: blocked"},
        )
        trace = iar.build_trace(digest, gate_result, "PLTR", live_data)
        self.assertIn("live.valuation", trace.missing_fields())
        self.assertGreater(trace.domains_failed, 0)

    def test_stock_funds_group_failure_is_not_applicable(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        live_data = {"overview": {"longName": "Palantir"}}
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False, live_data=live_data,
            live_errors={"funds": "TypeError: get_funds_data() raised for equity"},
        )
        trace = iar.build_trace(digest, gate_result, "PLTR", live_data)
        self.assertNotIn("live.funds", trace.missing_fields())
        self.assertIn("live.funds", trace.not_applicable_fields())

    def test_derived_options_metrics_are_not_applicable_without_a_chain(self):
        """The regression this split exists for: a ticker with no options chain
        used to report eleven 'missing' derived metrics and ~81% completeness on
        a run that obtained everything obtainable."""
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        live_data = {"overview": {"longName": "Palantir"}}  # no options group
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False, live_data=live_data,
        )
        trace = iar.build_trace(digest, gate_result, "PLTR", live_data)
        self.assertFalse(any(m.startswith("derived.options.") for m in trace.missing_fields()))
        self.assertIn("derived.options.iv_skew", trace.not_applicable_fields())
        self.assertFalse(any(m.startswith("derived.analyst.") for m in trace.missing_fields()))

    def test_present_but_empty_live_groups_are_still_not_applicable(self):
        """yfinance returns an `options` group for tickers with no tradable
        chain, and an `analyst` group with no grade actions. Grading on group
        *status* misread both as real gaps -- the live-run bug that made TSX
        ticker L report 81.4% while having fetched everything available."""
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        live_data = {
            "options": {"expirations": []},          # present, no chain_* keys
            "analyst": {"upgrades_downgrades": {}},  # present, no actions
            "insider": {"purchases": {}},            # present, no rows
        }
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False, live_data=live_data,
        )
        trace = iar.build_trace(digest, gate_result, "PLTR", live_data)
        for group in ("derived.options.", "derived.analyst.", "derived.insider."):
            self.assertFalse(
                any(m.startswith(group) for m in trace.missing_fields()),
                f"{group} must not be graded when its source came back empty",
            )
        self.assertIn("derived.insider.net_insider_shares", trace.not_applicable_fields())

    def test_populated_options_chain_is_graded_not_excused(self):
        """The inverse guard: when a chain really is present, its metrics are
        graded, so N/A cannot become a way to hide genuine fetch failures."""
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        live_data = {"options": {"chain_2026-09-18": {"calls": {}, "puts": {}}}}
        digest, gate_result = self._digest_for(
            connection, ticker_id, "PLTR", no_live=False, live_data=live_data,
        )
        trace = iar.build_trace(digest, gate_result, "PLTR", live_data)
        self.assertIn("derived.options.put_call_oi_ratio", trace.missing_fields())

    def test_never_divides_by_zero_on_a_degenerate_digest(self):
        # An all-not-applicable digest must still produce a valid percentage,
        # never a ZeroDivisionError.
        trace = iar.build_trace(
            {"live": {"skipped": True}}, {"owned": False, "asset_class": "stock"}, "NONE"
        )
        self.assertIsInstance(trace.completeness_pct, float)
        self.assertGreaterEqual(trace.completeness_pct, 0.0)

    def test_emit_appends_pipe_delimited_line_and_jsonl_sidecar(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        digest, gate_result = self._digest_for(connection, ticker_id, "PLTR")
        trace = iar.build_trace(digest, gate_result, "PLTR")
        log_path = Path(self.temp_dir.name) / "trace.txt"
        jsonl_path = log_path.with_suffix(".jsonl")
        skill_trace.emit(trace, text_log_path=log_path, jsonl_log_path=jsonl_path)
        skill_trace.emit(trace, text_log_path=log_path, jsonl_log_path=jsonl_path)
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("| TRACE | skill=investment-analyst-resources | subject=PLTR |", lines[0])
        self.assertIn(f"pct={trace.completeness_pct}", lines[0])
        sidecar = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(sidecar), 2)
        self.assertEqual(sidecar[0]["subject"], "PLTR")
        self.assertIn("domains", sidecar[0])

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
        trace = iar.build_trace(digest, self.GATE_RESULT, "PLTR")
        self.assertNotIn("quote.price", trace.missing_fields())
        self.assertGreater(trace.domains_ok, 0)

    def test_quote_skipped_excluded_from_denominator(self):
        digest_with = {"live": {"skipped": True}, "quote": {"price": 100.0, "as_of": datetime(2026, 8, 4)}}
        digest_without = {"live": {"skipped": True}, "quote": {"skipped": True}}
        trace_with = iar.build_trace(digest_with, self.GATE_RESULT, "PLTR")
        trace_without = iar.build_trace(digest_without, self.GATE_RESULT, "PLTR")
        self.assertEqual(trace_without.fields_graded, trace_with.fields_graded - 2)
        self.assertNotIn("quote.price", trace_without.missing_fields())
        # A deliberate --no-quote is not-applicable, never a gap.
        self.assertIn("quote.price", trace_without.not_applicable_fields())

    def test_quote_none_counts_as_a_failed_domain(self):
        digest = {"live": {"skipped": True}, "quote": None}
        trace = iar.build_trace(digest, self.GATE_RESULT, "PLTR")
        self.assertIn("quote.price", trace.missing_fields())
        self.assertIn("quote.as_of", trace.missing_fields())
        self.assertGreater(trace.domains_failed, 0)

    def test_no_live_run_reports_live_groups_as_not_applicable(self):
        digest = {"live": {"skipped": True}, "quote": {"skipped": True}}
        trace = iar.build_trace(digest, self.GATE_RESULT, "PLTR")
        self.assertFalse(any(m.startswith("live.") for m in trace.missing_fields()))
        self.assertIn("live.valuation", trace.not_applicable_fields())


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
