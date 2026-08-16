"""Tests for the security-technicals skill and its read-security-price-history dependency.

Builds a real temp DuckDB fixture (via `database.initialize_database` + direct
inserts, the same pattern `test_investment_analyst_resources.py` uses) so the
read boundary is exercised against actual schema and constraints, not mocks.
The technicals themselves have no fetch path to mock; the one network call
this skill makes, the optional current-price quote, is always exercised via
`patch.object(cli, "_fetch_latest_quote", ...)` -- no real network access
anywhere in this file.

The pure math is covered by `tests/test_security_technicals.py`; this file
covers the skill layer around it: the DB read, the artifact's placement in
`calculations/`, evidence/audit registration, and the trace's
missing-vs-not-applicable split.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database

SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"
sys.path.insert(0, str(SKILLS / "read-security-price-history" / "scripts"))
sys.path.insert(0, str(SKILLS / "security-technicals" / "scripts"))

import read_price_history as price_reader  # noqa: E402
import security_technicals_cli as cli  # noqa: E402

TODAY = date(2026, 8, 10)
# The Yahoo form callers hold (config.DEFAULT_BENCHMARK_SYMBOL) ...
BENCHMARK = "XEQT.TO"
# ... versus the canonical symbol `tickers` actually stores. Fixtures seed the
# canonical one so the suite exercises the real shape: the exchange suffix lives
# in ticker_provider_mappings.provider_symbol, never in tickers.ticker_symbol.
BENCHMARK_STORED = "XEQT"


class FixtureDatabaseTest(unittest.TestCase):
    """Base class: a temp DuckDB with a seeded ticker and benchmark."""

    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _seed_ticker(self, symbol="PLTR", *, exchange="NASDAQ", currency="USD") -> int:
        return self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, financial_currency,
                                    security_name, security_type)
               VALUES (?, ?, ?, ?, ?, 'stock') RETURNING ticker_id""",
            [symbol, exchange, currency, currency, f"{symbol} Inc."],
        ).fetchone()[0]

    def _seed_prices(self, ticker_id: int, closes: list[float], *, end: date = TODAY) -> None:
        """Insert one row per calendar day, ending at `end` (oldest first)."""
        start = end - timedelta(days=len(closes) - 1)
        for offset, close in enumerate(closes):
            day = start + timedelta(days=offset)
            self.connection.execute(
                "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [ticker_id, day, close, close, close, close, close, 1000],
            )

    def _seed_pair(self, *, ticker_closes: list[float], benchmark_closes: list[float]):
        ticker_id = self._seed_ticker()
        self._seed_prices(ticker_id, ticker_closes)
        benchmark_id = self._seed_ticker(BENCHMARK_STORED, exchange="TORONTO", currency="CAD")
        self._seed_prices(benchmark_id, benchmark_closes)
        return ticker_id, benchmark_id

    def _reopen_read_only(self):
        database.close_connection()
        return price_reader.connect_read_only(self.db_path)


class ReadPriceHistoryTest(FixtureDatabaseTest):
    def test_read_security_prices_returns_ascending_ohlcv(self):
        ticker_id = self._seed_ticker()
        self._seed_prices(ticker_id, [100.0, 110.0, 120.0])
        connection = self._reopen_read_only()
        try:
            rows = price_reader.read_security_prices(connection, ticker_id)
        finally:
            connection.close()
        self.assertEqual(len(rows), 3)
        self.assertEqual([float(r["close"]) for r in rows], [100.0, 110.0, 120.0])
        self.assertLess(rows[0]["record_date"], rows[-1]["record_date"])
        for column in ("record_date", "open", "high", "low", "close", "adjusted_close", "volume"):
            self.assertIn(column, rows[0])

    def test_read_benchmark_prices_returns_the_configured_series(self):
        self._seed_pair(ticker_closes=[100.0, 110.0], benchmark_closes=[50.0, 52.0])
        connection = self._reopen_read_only()
        try:
            symbol, rows = price_reader.read_benchmark_prices(connection, BENCHMARK)
        finally:
            connection.close()
        self.assertEqual(symbol, BENCHMARK)
        self.assertEqual([float(r["close"]) for r in rows], [50.0, 52.0])

    def test_unknown_benchmark_returns_empty_rows_not_a_raise(self):
        # A benchmark that was never fetched is a gap the caller reports, not
        # an error -- beta/alpha are legitimately unavailable without it.
        self._seed_ticker()
        connection = self._reopen_read_only()
        try:
            symbol, rows = price_reader.read_benchmark_prices(connection, "NOSUCH.TO")
        finally:
            connection.close()
        self.assertEqual(symbol, "NOSUCH.TO")
        self.assertEqual(rows, [])

    def test_unknown_ticker_resolves_to_none(self):
        connection = self._reopen_read_only()
        try:
            self.assertIsNone(price_reader.resolve_ticker(connection, "NOSUCH"))
        finally:
            connection.close()

    def test_resolve_ticker_is_case_insensitive(self):
        self._seed_ticker()
        connection = self._reopen_read_only()
        try:
            identity = price_reader.resolve_ticker(connection, "pltr")
        finally:
            connection.close()
        self.assertIsNotNone(identity)
        self.assertEqual(identity["ticker_symbol"], "PLTR")

    def test_yahoo_form_resolves_to_the_canonical_stored_symbol(self):
        # DEFAULT_BENCHMARK_SYMBOL is 'XEQT.TO' but tickers stores 'XEQT'.
        # Without the suffix fallback this misses and beta/alpha/relative
        # strength are silently reported as unavailable.
        self._seed_ticker(BENCHMARK_STORED, exchange="TORONTO", currency="CAD")
        connection = self._reopen_read_only()
        try:
            identity = price_reader.resolve_ticker(connection, BENCHMARK)
        finally:
            connection.close()
        self.assertIsNotNone(identity)
        self.assertEqual(identity["ticker_symbol"], BENCHMARK_STORED)

    def test_exact_match_wins_over_the_suffix_fallback(self):
        # A ticker genuinely stored with a suffix must resolve to itself rather
        # than being redirected to a different security sharing the bare symbol.
        suffixed = self._seed_ticker("FOO.TO", exchange="TORONTO", currency="CAD")
        self._seed_ticker("FOO", exchange="NASDAQ", currency="USD")
        connection = self._reopen_read_only()
        try:
            identity = price_reader.resolve_ticker(connection, "FOO.TO")
        finally:
            connection.close()
        self.assertEqual(identity["ticker_id"], suffixed)
        self.assertEqual(identity["ticker_symbol"], "FOO.TO")

    def test_resolve_provider_symbol_returns_the_verified_yahoo_symbol(self):
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, "
            "verification_status) VALUES (?, 'yahoo', 'PLTR', 'verified')",
            [ticker_id],
        )
        connection = self._reopen_read_only()
        try:
            symbol = price_reader.resolve_provider_symbol(connection, ticker_id)
        finally:
            connection.close()
        self.assertEqual(symbol, "PLTR")

    def test_resolve_provider_symbol_is_none_when_unmapped(self):
        ticker_id = self._seed_ticker()
        connection = self._reopen_read_only()
        try:
            self.assertIsNone(price_reader.resolve_provider_symbol(connection, ticker_id))
        finally:
            connection.close()

    def test_resolve_provider_symbol_ignores_unverified_mappings(self):
        # A pending/rejected mapping is not trusted for a real network call --
        # only 'verified' rows count, matching db_resources.resolve_ticker's rule.
        ticker_id = self._seed_ticker()
        self.connection.execute(
            "INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, "
            "verification_status) VALUES (?, 'yahoo', 'PLTR', 'pending')",
            [ticker_id],
        )
        connection = self._reopen_read_only()
        try:
            self.assertIsNone(price_reader.resolve_provider_symbol(connection, ticker_id))
        finally:
            connection.close()

    def test_missing_database_is_an_actionable_error(self):
        database.close_connection()
        with self.assertRaises(price_reader.DatabaseNotReady) as caught:
            price_reader.connect_read_only(Path(self.temp_dir.name) / "nope.duckdb")
        self.assertIn("does not exist", str(caught.exception))


class TraceClassificationTest(FixtureDatabaseTest):
    """The single most likely defect in this skill: grading insufficient
    history as `missing` instead of `not_applicable`."""

    def test_short_history_metrics_are_not_applicable_not_missing(self):
        # 30 days of history: SMA-200 and the 365-day relative strength cannot
        # exist. Neither is a gap in the data-collection sense.
        closes = [100.0 + i for i in range(30)]
        ticker_id, _ = self._seed_pair(ticker_closes=closes, benchmark_closes=closes)
        connection = self._reopen_read_only()
        try:
            rows = price_reader.read_security_prices(connection, ticker_id)
            _, benchmark_rows = price_reader.read_benchmark_prices(connection, BENCHMARK)
        finally:
            connection.close()

        payload = cli.build_result("PLTR", {"security_name": "x", "currency": "USD"},
                                   rows, BENCHMARK, benchmark_rows, TODAY)
        trace = cli.build_trace("PLTR", rows, BENCHMARK, benchmark_rows, payload["technicals"])
        technicals = next(d for d in trace.domains if d.name == "technicals")

        self.assertIn("sma_200d", technicals.not_applicable)
        self.assertIn("relative_strength", technicals.not_applicable)
        self.assertEqual(technicals.missing, [], "insufficient history must never grade as missing")
        # Both price series are present, so nothing anywhere is missing and
        # completeness is not dragged down by the unobtainable metrics.
        self.assertEqual(trace.fields_missing, 0)
        self.assertEqual(trace.completeness_pct, 100.0)

    def test_absent_price_history_is_missing_not_not_applicable(self):
        # The contrast case: the ticker resolves but has no stored rows. That
        # IS a real data gap and must grade as missing.
        self._seed_ticker()
        benchmark_id = self._seed_ticker(BENCHMARK_STORED, exchange="TORONTO", currency="CAD")
        self._seed_prices(benchmark_id, [50.0, 52.0])
        connection = self._reopen_read_only()
        try:
            _, benchmark_rows = price_reader.read_benchmark_prices(connection, BENCHMARK)
        finally:
            connection.close()

        payload = cli.build_result("PLTR", {"security_name": "x", "currency": "USD"},
                                   [], BENCHMARK, benchmark_rows, TODAY)
        trace = cli.build_trace("PLTR", [], BENCHMARK, benchmark_rows, payload["technicals"])
        prices = next(d for d in trace.domains if d.name == "prices")
        self.assertEqual(prices.missing, ["history"])
        self.assertLess(trace.completeness_pct, 100.0)

    def test_missing_benchmark_grades_as_missing(self):
        ticker_id = self._seed_ticker()
        self._seed_prices(ticker_id, [100.0, 110.0])
        connection = self._reopen_read_only()
        try:
            rows = price_reader.read_security_prices(connection, ticker_id)
        finally:
            connection.close()

        payload = cli.build_result("PLTR", {"security_name": "x", "currency": "USD"},
                                   rows, BENCHMARK, [], TODAY)
        trace = cli.build_trace("PLTR", rows, BENCHMARK, [], payload["technicals"])
        benchmark = next(d for d in trace.domains if d.name == "benchmark")
        self.assertEqual(benchmark.missing, [f"history:{BENCHMARK}"])

    def test_quote_skipped_by_flag_is_not_applicable(self):
        # build_trace's own default (no_quote=True) covers this same case, but
        # it is asserted explicitly here since it is the CLI's off switch.
        trace = cli.build_trace("PLTR", [], BENCHMARK, [], {}, quote=None, no_quote=True)
        quote = next(d for d in trace.domains if d.name == "quote")
        self.assertEqual(sorted(quote.not_applicable), ["as_of", "price"])
        self.assertEqual(quote.missing, [])

    def test_quote_fetch_failure_is_missing_not_not_applicable(self):
        # Attempted (no_quote=False) but came back empty -- a real gap, unlike
        # the opt-out case above.
        trace = cli.build_trace("PLTR", [], BENCHMARK, [], {}, quote=None, no_quote=False)
        quote = next(d for d in trace.domains if d.name == "quote")
        self.assertEqual(sorted(quote.missing), ["as_of", "price"])
        self.assertEqual(quote.not_applicable, [])

    def test_quote_success_grades_ok(self):
        fake_quote = {"price": 189.10, "as_of": "2026-08-10T14:32:01", "source": "fast_info"}
        trace = cli.build_trace("PLTR", [], BENCHMARK, [], {}, quote=fake_quote, no_quote=False)
        quote = next(d for d in trace.domains if d.name == "quote")
        self.assertEqual(sorted(quote.ok), ["as_of", "price"])
        self.assertEqual(quote.missing, [])


class BuildResultTest(FixtureDatabaseTest):
    def test_unresolved_ticker_short_circuits_with_a_gap(self):
        payload = cli.build_result("NOSUCH", None, [], BENCHMARK, [], TODAY)
        self.assertFalse(payload["resolved"])
        self.assertEqual(len(payload["gaps"]), 1)
        self.assertNotIn("technicals", payload)

    def test_full_history_computes_every_metric_and_reports_no_gaps(self):
        closes = [100.0 + i * 0.5 for i in range(400)]
        benchmark_closes = [50.0 + i * 0.1 for i in range(400)]
        ticker_id, _ = self._seed_pair(ticker_closes=closes, benchmark_closes=benchmark_closes)
        connection = self._reopen_read_only()
        try:
            rows = price_reader.read_security_prices(connection, ticker_id)
            _, benchmark_rows = price_reader.read_benchmark_prices(connection, BENCHMARK)
        finally:
            connection.close()

        payload = cli.build_result("PLTR", {"security_name": "x", "currency": "USD"},
                                   rows, BENCHMARK, benchmark_rows, TODAY)
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["gaps"], [])
        self.assertIsNotNone(payload["technicals"]["moving_averages"]["sma_200d"])
        self.assertIsNotNone(payload["technicals"]["beta_alpha"]["beta"])
        self.assertEqual(payload["prices"]["count"], 400)

    def test_quote_field_defaults_to_skipped(self):
        # build_result's own default (no_quote=True) matches build_trace's --
        # a direct call with no quote args produces the CLI's --no-quote shape.
        payload = cli.build_result("PLTR", {"security_name": "x", "currency": "USD"}, [], BENCHMARK, [], TODAY)
        self.assertEqual(payload["quote"], {"skipped": True})
        self.assertNotIn("current-price quote unavailable", payload["gaps"])

    def test_quote_field_carries_the_fetched_quote(self):
        fake_quote = {"price": 189.10, "as_of": "2026-08-10T14:32:01", "source": "fast_info"}
        payload = cli.build_result(
            "PLTR", {"security_name": "x", "currency": "USD"}, [], BENCHMARK, [], TODAY,
            quote=fake_quote, no_quote=False,
        )
        self.assertEqual(payload["quote"], fake_quote)

    def test_quote_fetch_failure_is_reported_as_a_gap(self):
        payload = cli.build_result(
            "PLTR", {"security_name": "x", "currency": "USD"}, [], BENCHMARK, [], TODAY,
            quote=None, no_quote=False,
        )
        self.assertIsNone(payload["quote"])
        self.assertIn("current-price quote unavailable", payload["gaps"])

    def test_price_summary_carries_coverage_not_the_series(self):
        closes = [100.0, 110.0, 120.0]
        ticker_id, _ = self._seed_pair(ticker_closes=closes, benchmark_closes=closes)
        connection = self._reopen_read_only()
        try:
            rows = price_reader.read_security_prices(connection, ticker_id)
        finally:
            connection.close()
        summary = cli._price_summary(rows)
        self.assertEqual(summary["count"], 3)
        self.assertAlmostEqual(summary["latest_close"], 120.0)
        self.assertNotIn("rows", summary)


class CliWorkspaceTest(FixtureDatabaseTest):
    """End-to-end through `main()`, including run attachment and registration."""

    def _patch_workspace(self) -> Path:
        """Point the workspace at a temp dir AND make the temp DB the configured
        default.

        The second half matters: `_resolve_run` deliberately skips workspace
        attachment for a non-default `--db-path` (the test/debug convention
        inherited from `investment-analyst-resources`, so the suite never
        populates the real `workspace/runs/`). Passing `--db-path` here would
        therefore silently test the skip path instead of the attach path.
        Patching the module's `DATABASE_PATH` -- which `parse_args` reads at
        call time for its default and `_resolve_run` compares against -- lets
        these tests exercise the real default-on behavior.
        """
        import config as config_module

        base = Path(self.temp_dir.name) / "ws"
        runs_root = base / "runs"
        runs_root.mkdir(parents=True)
        patcher = patch.multiple(
            config_module, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        db_patcher = patch.object(cli, "DATABASE_PATH", self.db_path)
        db_patcher.start()
        self.addCleanup(db_patcher.stop)
        return runs_root

    def _run_cli(self, argv: list[str]) -> int:
        import io
        from contextlib import redirect_stdout

        database.close_connection()
        with redirect_stdout(io.StringIO()):
            return cli.main(argv)

    def _seed_full(self):
        closes = [100.0 + i * 0.5 for i in range(400)]
        benchmark_closes = [50.0 + i * 0.1 for i in range(400)]
        self._seed_pair(ticker_closes=closes, benchmark_closes=benchmark_closes)

    def test_artifact_lands_in_calculations_not_evidence(self):
        # The core claim of this refactor: a derived calculation is not
        # evidence-from-outside, so it goes in `calculations/`.
        self._seed_full()
        runs_root = self._patch_workspace()
        exit_code = self._run_cli([
            "--ticker", "PLTR",
            "--run-id", "technicals-run",
            "--no-quote",
            "--trace-log-path", str(Path(self.temp_dir.name) / "trace.txt"),
        ])
        self.assertEqual(exit_code, 0)

        run_dir = runs_root / "technicals-run"
        artifacts = list((run_dir / "calculations").glob("security-technicals-PLTR-*.json"))
        self.assertEqual(len(artifacts), 1, "artifact must be written into calculations/")
        self.assertEqual(list((run_dir / "evidence").glob("security-technicals-*.json")), [])

        payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "security-technicals.v1")
        self.assertEqual(payload["ticker"], "PLTR")
        self.assertIn("trace", payload)

    def test_manifest_lists_the_artifact_under_calculation_paths(self):
        # Proves the calculations/ wiring works end to end: a downstream stage
        # finds this artifact through the manifest, not by guessing a filename.
        from workspace import manifest as manifest_module
        from workspace import run as run_module

        self._seed_full()
        runs_root = self._patch_workspace()
        self._run_cli([
            "--ticker", "PLTR",
            "--run-id", "technicals-run",
            "--no-quote",
            "--trace-log-path", str(Path(self.temp_dir.name) / "trace.txt"),
        ])

        run_dir = runs_root / "technicals-run"
        request = run_module.load_request(run_dir / "request.yaml")
        rebuilt = manifest_module.build(run_dir, request, run_id="technicals-run")
        self.assertTrue(
            any(path.startswith("calculations/") for path in rebuilt.calculation_paths),
            f"expected a calculations/ entry, got {rebuilt.calculation_paths}",
        )

    def test_evidence_and_audit_record_the_calculation(self):
        from workspace import audit, evidence as evidence_module

        self._seed_full()
        runs_root = self._patch_workspace()
        self._run_cli([
            "--ticker", "PLTR",
            "--run-id", "technicals-run",
            "--no-quote",
            "--trace-log-path", str(Path(self.temp_dir.name) / "trace.txt"),
        ])

        run_dir = runs_root / "technicals-run"
        records = evidence_module.read_records(run_dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["evidence_type"], "security_technicals")
        self.assertEqual(records[0]["status"], "available")
        self.assertTrue(records[0]["artifact_path"].startswith("calculations/"))

        kinds = [event["event"] for event in audit.read_events(run_dir)]
        self.assertIn("calculation_written", kinds)

    def test_run_is_created_by_default_without_an_explicit_id(self):
        self._seed_full()
        runs_root = self._patch_workspace()
        exit_code = self._run_cli([
            "--ticker", "PLTR",
            "--no-quote",
            "--trace-log-path", str(Path(self.temp_dir.name) / "trace.txt"),
        ])
        self.assertEqual(exit_code, 0)
        runs = [d for d in runs_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
        self.assertEqual(len(runs), 1)
        self.assertTrue(list((runs[0] / "calculations").glob("*.json")))

    def test_no_run_writes_to_output_and_records_the_skip(self):
        self._seed_full()
        self._patch_workspace()
        destination = Path(self.temp_dir.name) / "technicals.json"
        trace_log = Path(self.temp_dir.name) / "trace.txt"
        exit_code = self._run_cli([
            "--ticker", "PLTR",
            "--no-run", "--no-quote", "--output", str(destination), "--trace-log-path", str(trace_log),
        ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(destination.is_file())

        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["trace"]["workspace"]["status"], "skipped")
        self.assertEqual(payload["trace"]["workspace"]["skip_reason"], "--no-run")

    def test_trace_log_line_is_written(self):
        self._seed_full()
        self._patch_workspace()
        trace_log = Path(self.temp_dir.name) / "trace.txt"
        self._run_cli([
            "--ticker", "PLTR",
            "--no-run", "--no-quote", "--output", str(Path(self.temp_dir.name) / "t.json"),
            "--trace-log-path", str(trace_log),
        ])
        self.assertTrue(trace_log.is_file())
        self.assertIn("skill=security-technicals", trace_log.read_text(encoding="utf-8"))
        self.assertTrue(trace_log.with_suffix(".jsonl").is_file())

    def test_no_trace_skips_the_trace_key_and_the_log(self):
        self._seed_full()
        self._patch_workspace()
        destination = Path(self.temp_dir.name) / "technicals.json"
        trace_log = Path(self.temp_dir.name) / "trace.txt"
        self._run_cli([
            "--ticker", "PLTR",
            "--no-run", "--no-quote", "--output", str(destination),
            "--no-trace", "--trace-log-path", str(trace_log),
        ])
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertNotIn("trace", payload)
        self.assertFalse(trace_log.exists())

    def test_unresolved_ticker_still_writes_an_artifact_with_the_gap(self):
        self._patch_workspace()
        destination = Path(self.temp_dir.name) / "technicals.json"
        exit_code = self._run_cli([
            "--ticker", "NOSUCH",
            "--no-run", "--output", str(destination),
        ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertFalse(payload["resolved"])
        self.assertTrue(payload["gaps"])

    def test_quote_is_fetched_by_default_and_included_in_the_artifact(self):
        # Patches the network call itself, not resolve_provider_symbol -- a
        # verified mapping is seeded so the CLI's own resolution path runs
        # end to end, same as it would against the real database.
        ticker_id = self._seed_ticker()
        self._seed_prices(ticker_id, [100.0 + i * 0.5 for i in range(400)])
        benchmark_id = self._seed_ticker(BENCHMARK_STORED, exchange="TORONTO", currency="CAD")
        self._seed_prices(benchmark_id, [50.0 + i * 0.1 for i in range(400)])
        self.connection.execute(
            "INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, "
            "verification_status) VALUES (?, 'yahoo', 'PLTR', 'verified')",
            [ticker_id],
        )
        self._patch_workspace()
        destination = Path(self.temp_dir.name) / "technicals.json"
        fake_quote = {"price": 189.10, "as_of": None, "source": "fast_info", "previous_close": 187.42}

        with patch.object(cli, "_fetch_latest_quote", return_value=fake_quote) as mock_quote:
            exit_code = self._run_cli(["--ticker", "PLTR", "--no-run", "--output", str(destination)])

        self.assertEqual(exit_code, 0)
        mock_quote.assert_called_once_with("PLTR")
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["quote"]["price"], 189.10)

    def test_no_quote_flag_skips_the_fetch_entirely(self):
        self._seed_full()
        self._patch_workspace()
        destination = Path(self.temp_dir.name) / "technicals.json"

        with patch.object(cli, "_fetch_latest_quote") as mock_quote:
            self._run_cli(["--ticker", "PLTR", "--no-run", "--no-quote", "--output", str(destination)])

        mock_quote.assert_not_called()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["quote"], {"skipped": True})


class CliArgumentTest(unittest.TestCase):
    def test_no_run_and_run_id_together_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--ticker", "PLTR", "--no-run", "--run-id", "x"])

    def test_output_with_multiple_tickers_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--ticker", "PLTR", "NVDA", "--output", "x.json"])

    def test_benchmark_defaults_to_the_configured_symbol(self):
        args = cli.parse_args(["--ticker", "PLTR"])
        self.assertEqual(args.benchmark, BENCHMARK)

    def test_quote_is_fetched_by_default(self):
        args = cli.parse_args(["--ticker", "PLTR"])
        self.assertFalse(args.no_quote)

    def test_no_quote_flag_disables_it(self):
        args = cli.parse_args(["--ticker", "PLTR", "--no-quote"])
        self.assertTrue(args.no_quote)


if __name__ == "__main__":
    unittest.main()
