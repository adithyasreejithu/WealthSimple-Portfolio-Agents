import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

import app
import database
import ticker_mapping
from market_data import EarningsDividendsSyncResult, FinancialSnapshotsSyncResult, MarketSyncResult


class AppPipelineTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.data_dir = Path(self.temp_dir.name)

    def test_rename_monthly_documents_only_renames_pdf(self):
        pdf = self.data_dir / "Wealthsimple account statement 2025-04 final.pdf"
        excel = self.data_dir / "activities-export-2025-04.xlsx"
        pdf.touch()
        excel.touch()

        renamed = app.rename_monthly_documents(self.data_dir)

        self.assertEqual(renamed, [self.data_dir / "2025-04.pdf"])
        self.assertTrue((self.data_dir / "2025-04.pdf").exists())
        self.assertTrue(excel.exists())

    def test_rename_does_not_overwrite_existing_month(self):
        original = self.data_dir / "statement 2025-04 account.pdf"
        target = self.data_dir / "2025-04.pdf"
        original.touch()
        target.touch()

        renamed = app.rename_monthly_documents(self.data_dir)

        self.assertEqual(renamed, [])
        self.assertTrue(original.exists())
        self.assertTrue(target.exists())

    def test_default_pipeline_stages_sources_in_precedence_order(self):
        statement = self.data_dir / "2025-04.pdf"
        statement.touch()
        export = self.data_dir / "activities-export-2025-04.csv"
        export.write_text(
            "transaction_date,settlement_date,account_id,account_type,activity_type,activity_sub_type,direction,symbol,name,currency,quantity,unit_price,commission,net_cash_amount\n"
            "2025-04-01,,A,TFSA,MoneyMovement,E_TRFIN,,,,CAD,1,,,1\n",
            encoding="utf-8",
        )
        empty_statement = pd.DataFrame(columns=[
            "date", "transaction", "ticker_id", "quantity", "execDate", "fx_rate",
            "debit", "credit", "balance", "statement_code", "description",
        ])
        empty_email = pd.DataFrame(columns=[
            "account", "transaction", "ticker_id", "ticker", "quantity", "avg_price",
            "total_cost", "debit", "date", "price_currency",
        ])
        with (
            patch.object(app, "extract_statement_pdf", return_value=empty_statement),
            patch.object(app, "fetch_email_transactions", return_value=empty_email),
            patch.object(app, "_run_market_data_refresh", return_value=[]),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        staged_order = [row[0] for row in database.get_shared_connection(
            self.data_dir / "db.duckdb"
        ).execute("SELECT source_type FROM staged_files ORDER BY file_sequence").fetchall()]
        self.assertEqual(staged_order, ["statement", "email", "export"])
        self.assertTrue(result.succeeded)

    def test_pipeline_refreshes_market_data_after_batch_completion(self):
        """Market-data refresh is a post-ingestion tail stage, not part of the
        email branch -- it runs once, after `complete_batch`, regardless of
        which sources staged anything (see `run_pipeline`'s docstring).
        """
        events = []
        email_data = pd.DataFrame(columns=[
            "account", "transaction", "ticker_id", "ticker", "quantity", "avg_price",
            "total_cost", "debit", "date", "price_currency", "source_message_id",
            "received_at",
        ])
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "_stage_email_batch", return_value=([(9, email_data)], 2, [])),
            patch.object(app, "_stage_export_files", return_value=([], 3, [])),
            patch.object(app, "resolve_batch"),
            patch.object(app, "_pending_email_symbols", return_value=[]),
            patch.object(app, "_publish_email_batch", side_effect=lambda *_: events.append("email") or 0),
            patch.object(app, "complete_batch", side_effect=lambda *_: events.append("batch")),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(
                app,
                "sync_market_data",
                side_effect=lambda *a, **k: events.append("market") or MarketSyncResult(1, 2),
            ) as sync,
            patch.object(app, "get_market_targets", return_value=[]),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        self.assertEqual(events, ["email", "batch", "market"])
        sync.assert_called_once_with(self.data_dir / "db.duckdb", as_of=None, full=False)
        by_source = {r.source: r for r in result.results}
        self.assertEqual(by_source["email"], app.SourceResult("email", None, "succeeded", 0))
        self.assertEqual(by_source["market-data"], app.SourceResult("market-data", None, "succeeded", 2))
        self.assertEqual(by_source["earnings-dividends"].status, "skipped")
        self.assertEqual(by_source["financial-snapshots"].status, "skipped")
        self.assertNotIn("classification", by_source)

    def test_source_specific_pipeline_still_refreshes_market_data(self):
        """The refresh stage is orthogonal to which ingestion source ran --
        a `--source statements` run still refreshes prices/earnings/dividends/
        financials for every currently owned ticker (see run_pipeline's
        docstring: the pipeline pulls everything stored locally)."""
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(0, 0)) as sync,
            patch.object(app, "get_market_targets", return_value=[]),
        ):
            app.run_pipeline("statements", self.data_dir, self.data_dir / "db.duckdb")

        sync.assert_called_once()

    def test_failed_ingestion_still_refreshes_market_data(self):
        """A failed statement publish should not block refreshing market data
        for tickers unrelated to that failure -- only the pipeline result's
        overall success reflects the ingestion failure."""
        failed = app.SourceResult("statement", None, "failed", error="bad statement")
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [failed])),
            patch.object(app, "_stage_email_batch", return_value=([], 2, [])),
            patch.object(app, "_stage_export_files", return_value=([], 3, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(0, 0)) as sync,
            patch.object(app, "get_market_targets", return_value=[]),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        sync.assert_called_once()
        self.assertFalse(result.succeeded)

    def test_pipeline_never_runs_classification(self):
        """Classification is its own command (`classify`), never part of
        `pipeline` for any --source value -- see run_pipeline's docstring."""
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(0, 0)),
            patch.object(app, "get_market_targets", return_value=[]),
            patch.object(app, "_run_portfolio_classification") as classify,
        ):
            result = app.run_pipeline("statements", self.data_dir, self.data_dir / "db.duckdb")

        classify.assert_not_called()
        self.assertNotIn("classification", {r.source for r in result.results})

    def test_pipeline_full_flag_bypasses_staleness_gates(self):
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(0, 0)) as sync,
            patch.object(app, "get_market_targets", return_value=[]),
            patch.object(
                app, "sync_earnings_dividends",
                return_value=EarningsDividendsSyncResult(0, 0, 0),
            ) as ed_sync,
            patch.object(
                app, "sync_financial_snapshots",
                return_value=FinancialSnapshotsSyncResult(0, 0),
            ) as fs_sync,
        ):
            app.run_pipeline("statements", self.data_dir, self.data_dir / "db.duckdb", full=True)

        sync.assert_called_once_with(self.data_dir / "db.duckdb", as_of=None, full=True)
        # full=True bypasses the gates entirely (symbols=None), even with no owned tickers.
        ed_sync.assert_called_once_with(self.data_dir / "db.duckdb", None)
        fs_sync.assert_called_once_with(self.data_dir / "db.duckdb", None)

    def test_pipeline_skip_market_data_flag_skips_the_whole_stage(self):
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "ensure_positions_fresh"),
            patch.object(app, "sync_market_data") as sync,
        ):
            result = app.run_pipeline(
                "statements", self.data_dir, self.data_dir / "db.duckdb", skip_market_data=True
            )

        sync.assert_not_called()
        by_source = {r.source: r for r in result.results}
        self.assertEqual(by_source["market-data"].status, "skipped")

    def test_run_portfolio_classification_persists_and_reports_rows(self):
        fake_module = types.ModuleType("classification_workflow")
        fake_module.classify_portfolio = lambda db_path: {"holdings": []}
        fake_module.write_output = lambda payload: Path("fake-output.json")
        db_path = self.data_dir / "db.duckdb"
        with (
            patch.dict("sys.modules", {"classification_workflow": fake_module}),
            patch.object(app, "upload_portfolio_classifications", return_value=5) as upload,
        ):
            result = app._run_portfolio_classification(db_path)

        self.assertEqual(result, app.SourceResult("classification", Path("fake-output.json"), "succeeded", 5))
        upload.assert_called_once_with(Path("fake-output.json"), db_path)

    def test_run_portfolio_classification_releases_shared_connection_for_read_only_workflow(self):
        """A full pipeline run holds a read-write shared connection, but the
        classification workflow opens its own read-only one. DuckDB rejects the
        second connection unless the first is released first.
        """
        db_path = self.data_dir / "db.duckdb"
        database.initialize_database(db_path)
        # Reproduce the pipeline state at the point classification runs: the
        # shared read-write connection is still open from ingestion.
        database.get_shared_connection(db_path)

        fake_module = types.ModuleType("classification_workflow")

        def _classify(path):
            # What read_classification_data actually does.
            connection = duckdb.connect(str(Path(path).resolve()), read_only=True)
            connection.close()
            return {"holdings": []}

        fake_module.classify_portfolio = _classify
        fake_module.write_output = lambda payload: Path("fake-output.json")
        with (
            patch.dict("sys.modules", {"classification_workflow": fake_module}),
            patch.object(app, "upload_portfolio_classifications", return_value=0),
        ):
            result = app._run_portfolio_classification(db_path)

        self.assertEqual(result.status, "succeeded")

    def test_run_portfolio_classification_failure_is_non_fatal(self):
        fake_module = types.ModuleType("classification_workflow")

        def _boom(db_path):
            raise RuntimeError("yfinance unavailable")

        fake_module.classify_portfolio = _boom
        fake_module.write_output = lambda payload: Path("unused.json")
        with patch.dict("sys.modules", {"classification_workflow": fake_module}):
            result = app._run_portfolio_classification(self.data_dir / "db.duckdb")

        self.assertEqual(result.source, "classification")
        self.assertEqual(result.status, "failed")
        self.assertIn("yfinance unavailable", result.error)

    def test_classify_command_succeeds(self):
        with patch.object(
            app, "_run_portfolio_classification",
            return_value=app.SourceResult("classification", Path("out.json"), "succeeded", 28),
        ) as classify:
            output = app.main(["classify", "--database", str(self.data_dir / "db.duckdb")])

        self.assertEqual(output, 0)
        classify.assert_called_once_with(self.data_dir / "db.duckdb")

    def test_classify_command_failure_returns_nonzero(self):
        with patch.object(
            app, "_run_portfolio_classification",
            return_value=app.SourceResult("classification", None, "failed", error="boom"),
        ):
            output = app.main(["classify"])

        self.assertEqual(output, 1)

    def test_market_data_refresh_gates_earnings_dividends_and_financial_snapshots(self):
        """Only symbols due for refresh (per stale_*_symbols) are passed through;
        a domain with nothing due is skipped without calling its sync function."""
        targets_sentinel = object()
        with (
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(2, 5)) as market_sync,
            patch.object(app, "get_market_targets", return_value=targets_sentinel) as targets,
            patch.object(app, "stale_earnings_symbols", return_value=["AAPL"]),
            patch.object(app, "stale_dividend_symbols", return_value=["ENB"]),
            patch.object(app, "stale_financial_snapshot_symbols", return_value=[]),
            patch.object(
                app, "sync_earnings_dividends",
                return_value=EarningsDividendsSyncResult(2, 3, 1, upcoming_dividend_rows=1),
            ) as ed_sync,
            patch.object(app, "sync_financial_snapshots") as fs_sync,
        ):
            results = app._run_market_data_refresh(self.data_dir / "db.duckdb")

        market_sync.assert_called_once_with(self.data_dir / "db.duckdb", as_of=None, full=False)
        targets.assert_called_once_with(self.data_dir / "db.duckdb")
        ed_sync.assert_called_once_with(self.data_dir / "db.duckdb", ["AAPL", "ENB"])
        fs_sync.assert_not_called()
        by_source = {r.source: r for r in results}
        self.assertEqual(by_source["market-data"], app.SourceResult("market-data", None, "succeeded", 5))
        self.assertEqual(by_source["earnings-dividends"], app.SourceResult("earnings-dividends", None, "succeeded", 5))
        self.assertEqual(by_source["financial-snapshots"], app.SourceResult("financial-snapshots", None, "skipped"))

    def test_market_data_refresh_never_widens_to_research_tickers(self):
        """The pipeline's refresh stage stays owned-only after `market_data.
        get_market_targets` grew an `include_research` widening for the
        investment-analyst-resources skill's on-demand research path --
        `_run_market_data_refresh` must never pass it (or any other new
        kwarg) through to `sync_market_data`/`sync_earnings_dividends`/
        `sync_financial_snapshots`, so a declared-wishlist ticker is never
        pulled into a routine `pipeline` run."""
        with (
            patch.object(app, "sync_market_data", return_value=MarketSyncResult(0, 0)) as market_sync,
            patch.object(app, "get_market_targets", return_value=[]),
            patch.object(app, "stale_earnings_symbols", return_value=[]),
            patch.object(app, "stale_dividend_symbols", return_value=[]),
            patch.object(app, "stale_financial_snapshot_symbols", return_value=[]),
        ):
            app._run_market_data_refresh(self.data_dir / "db.duckdb")

        market_sync.assert_called_once_with(self.data_dir / "db.duckdb", as_of=None, full=False)

    def test_market_data_refresh_skip_flag_produces_single_skipped_result(self):
        with patch.object(app, "sync_market_data") as market_sync:
            results = app._run_market_data_refresh(self.data_dir / "db.duckdb", skip=True)

        market_sync.assert_not_called()
        self.assertEqual(results, [app.SourceResult("market-data", None, "skipped")])

    def test_market_data_refresh_reports_degraded_when_some_symbols_fail(self):
        """A thin/newly-listed ticker with an isolated fetch gap (e.g. no
        history before its actual listing date) must not read the same as a
        genuinely broken sync -- the domain is `degraded`, not `failed`, so
        it doesn't fail the whole pipeline/dashboard job."""
        with (
            patch.object(
                app, "sync_market_data",
                return_value=MarketSyncResult(3, 2, error="history fetch failed for: XNDU", failed_symbols=("XNDU",)),
            ),
            patch.object(app, "get_market_targets", return_value=[]),
            patch.object(app, "stale_earnings_symbols", return_value=[]),
            patch.object(app, "stale_dividend_symbols", return_value=[]),
            patch.object(app, "stale_financial_snapshot_symbols", return_value=[]),
        ):
            results = app._run_market_data_refresh(self.data_dir / "db.duckdb")

        by_source = {r.source: r for r in results}
        market_data_result = by_source["market-data"]
        self.assertEqual(market_data_result.status, "degraded")
        self.assertEqual(market_data_result.rows, 2)
        self.assertEqual(market_data_result.error, "history fetch failed for: XNDU")

    def test_market_data_refresh_reports_failed_when_every_symbol_fails(self):
        with (
            patch.object(
                app, "sync_market_data",
                return_value=MarketSyncResult(2, 0, error="history fetch failed for: A, B", failed_symbols=("A", "B")),
            ),
            patch.object(app, "get_market_targets", return_value=[]),
            patch.object(app, "stale_earnings_symbols", return_value=[]),
            patch.object(app, "stale_dividend_symbols", return_value=[]),
            patch.object(app, "stale_financial_snapshot_symbols", return_value=[]),
        ):
            results = app._run_market_data_refresh(self.data_dir / "db.duckdb")

        by_source = {r.source: r for r in results}
        self.assertEqual(by_source["market-data"].status, "failed")

    def test_analytics_command_prints_report(self):
        report = {
            "generated_at": "2025-01-01T00:00:00",
            "parameters": {},
            "summary": {
                "portfolio_value": 1234,
                "cash": {"balance": 250, "source": "explicit_balance"},
                "book_cost": 900,
                "unrealized_gain": {"amount": 84, "percent": 0.0933},
                "realized_gain_total": 0,
            },
            "holdings": [
                {
                    "ticker_symbol": "AAPL",
                    "exchange": "NASDAQ",
                    "quantity": 3,
                    "market_value": 984,
                }
            ],
            "allocation": {"by_group": {}},
            "targets": {"groups": []},
            "income": {"source": "email", "totals_by_currency": {}},
            "fees": {"fx": {"available": False, "source": "statements"}},
            "data_quality": {"counts_by_code": {}},
            "unavailable_metrics": [],
        }

        with patch.object(app, "run_analytics", return_value=report), patch(
            "sys.stdout.write"
        ) as write:
            output = app.main(["analytics", "--database", str(self.data_dir / "db.duckdb")])

        self.assertEqual(output, 0)
        printed = "".join(call.args[0] for call in write.call_args_list)
        self.assertIn("Portfolio Analytics", printed)
        self.assertIn("Cash source     : explicit_balance", printed)
        self.assertIn("Positions", printed)
        self.assertIn("AAPL", printed)
        self.assertIn("NASDAQ", printed)
        self.assertIn("3.00", printed)
        self.assertIn("984.00", printed)

    def test_analytics_command_exports_json_to_file(self):
        report = {
            "generated_at": "2025-01-01T00:00:00",
            "parameters": {},
            "summary": {"portfolio_value": 1234, "cash": {"balance": 250, "source": "explicit_balance"}},
            "holdings": [],
        }
        export_folder = self.data_dir / "exports" / "analytics"

        with patch.object(app, "run_analytics", return_value=report):
            output = app.main([
                "analytics",
                "--export",
                "--export-folder",
                str(export_folder),
                "--database",
                str(self.data_dir / "db.duckdb"),
            ])

        self.assertEqual(output, 0)
        export_path = export_folder / "portfolio-analytics.json"
        self.assertTrue(export_path.exists())
        written = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual(written["report"]["summary"]["portfolio_value"], 1234)

    def test_pipeline_subcommand_forwards_pipeline_options(self):
        result = app.PipelineResult((app.SourceResult("email", None, "skipped"),))

        with patch.object(app, "run_pipeline", return_value=result) as run_pipeline:
            output = app.main([
                "pipeline",
                "--source",
                "email",
                "--data-folder",
                str(self.data_dir),
                "--database",
                str(self.data_dir / "db.duckdb"),
            ])

        self.assertEqual(output, 0)
        run_pipeline.assert_called_once_with(
            "email", self.data_dir, self.data_dir / "db.duckdb", None,
            full=False, skip_market_data=False,
        )

    def test_pipeline_subcommand_forwards_email_date_from_override(self):
        result = app.PipelineResult((app.SourceResult("email", None, "skipped"),))

        with patch.object(app, "run_pipeline", return_value=result) as run_pipeline:
            output = app.main([
                "pipeline",
                "--source",
                "email",
                "--data-folder",
                str(self.data_dir),
                "--database",
                str(self.data_dir / "db.duckdb"),
                "--email-date-from",
                "2026-08-05",
            ])

        self.assertEqual(output, 0)
        run_pipeline.assert_called_once_with(
            "email", self.data_dir, self.data_dir / "db.duckdb", date(2026, 8, 5),
            full=False, skip_market_data=False,
        )

    def test_resolve_tickers_with_no_pending_skips_retry(self):
        db_path = self.data_dir / "db.duckdb"
        with (
            patch.object(ticker_mapping, "list_pending", return_value=[]) as list_pending,
            patch.object(app, "run_pipeline") as run_pipeline,
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                output = app.main(["resolve-tickers", "--database", str(db_path)])

        self.assertEqual(output, 0)
        list_pending.assert_called_once_with(db_path)
        run_pipeline.assert_not_called()
        self.assertIn("No pending ticker mappings.", buffer.getvalue())

    def test_resolve_tickers_retries_pipeline_when_mapping_verified(self):
        db_path = self.data_dir / "db.duckdb"
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD", "trade_count": 1,
            "first_seen": None, "last_seen": None, "sources": ["export"],
        }]
        resolutions = [{
            "source_symbol": "MDA", "provider_symbol": "MDA.TO", "currency": "CAD",
            "status": "verified", "resolved_email_rows": 1,
        }]
        pipeline_result = app.PipelineResult(
            (app.SourceResult("export", None, "succeeded", 569),)
        )
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "resolve_pending_interactively", return_value=resolutions),
            patch.object(app, "run_pipeline", return_value=pipeline_result) as run_pipeline,
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                output = app.main([
                    "resolve-tickers",
                    "--data-folder", str(self.data_dir),
                    "--database", str(db_path),
                ])

        self.assertEqual(output, 0)
        run_pipeline.assert_called_once_with("all", self.data_dir, db_path)
        printed = buffer.getvalue()
        self.assertIn("MDA: mapped to MDA.TO (CAD)", printed)
        self.assertIn("export: succeeded (569 row(s))", printed)
        self.assertNotIn("{", printed)

    def test_resolve_tickers_skips_retry_when_all_skipped(self):
        db_path = self.data_dir / "db.duckdb"
        pending = [{
            "source_symbol": "XNDU", "detected_currency": "CAD", "trade_count": 1,
            "first_seen": None, "last_seen": None, "sources": ["email"],
        }]
        resolutions = [{"source_symbol": "XNDU", "status": "skipped"}]
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "resolve_pending_interactively", return_value=resolutions),
            patch.object(app, "run_pipeline") as run_pipeline,
        ):
            output = app.main(["resolve-tickers", "--database", str(db_path)])

        self.assertEqual(output, 0)
        run_pipeline.assert_not_called()

    def test_app_delegates_feature_subcommands(self):
        commands = {
            "statements": "statement_extractor.main",
            "email": "email_extractor.main",
            "yfinance": "yfinance_extractor.main",
            "earnings-dividends": "earnings_dividends_extractor.main",
            "financial-snapshots": "financial_snapshots_extractor.main",
            "annual-financial-context": "annual_financial_context.main",
            "ticker-map": "ticker_mapping.main",
            "import-activities": "data_sorter.main",
        }

        for command, target in commands.items():
            with self.subTest(command=command), patch(target, return_value=0) as delegated:
                output = app.main([command, "--help"])

            self.assertEqual(output, 0)
            delegated.assert_called_once_with(["--help"])

    def test_delegated_command_failure_returns_nonzero(self):
        with patch("statement_extractor.main", side_effect=RuntimeError("failed")):
            output = app.main(["statements"])

        self.assertEqual(output, 1)

    def test_root_help_lists_all_user_commands(self):
        with patch("sys.stdout.write") as write:
            output = app.main(["--help"])

        self.assertEqual(output, 0)
        printed = "".join(call.args[0] for call in write.call_args_list)
        for command in (
            "pipeline",
            "classify",
            "analytics",
            "statements",
            "email",
            "yfinance",
            "yfinance-sync",
            "earnings-dividends",
            "earnings-dividends-sync",
            "financial-snapshots",
            "financial-snapshots-sync",
            "annual-financial-context",
            "ticker-map",
            "resolve-tickers",
            "import-activities",
            "portfolio-classify",
            "classification-sync",
        ):
            self.assertIn(command, printed)

    def test_yfinance_sync_command_forwards_database_and_tickers(self):
        expected = MarketSyncResult(2, 8, 1)
        with patch.object(app, "sync_market_data", return_value=expected) as sync:
            output = app.main([
                "yfinance-sync",
                "--database",
                str(self.data_dir / "db.duckdb"),
                "--tickers",
                "AAPL",
                "VFV.TO",
            ])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(
            self.data_dir / "db.duckdb", ["AAPL", "VFV.TO"], full=False
        )

    def test_yfinance_sync_full_flag_is_forwarded(self):
        expected = MarketSyncResult(1, 3)
        with patch.object(app, "sync_market_data", return_value=expected) as sync:
            output = app.main(["yfinance-sync", "--full"])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(app.DATABASE_PATH, None, full=True)

    def test_earnings_dividends_sync_command_forwards_database_and_tickers(self):
        expected = EarningsDividendsSyncResult(2, 40, 12)
        with patch.object(app, "sync_earnings_dividends", return_value=expected) as sync:
            output = app.main([
                "earnings-dividends-sync",
                "--database",
                str(self.data_dir / "db.duckdb"),
                "--tickers",
                "AAPL",
                "VFV.TO",
            ])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(
            self.data_dir / "db.duckdb", ["AAPL", "VFV.TO"],
            skip_earnings=False, skip_dividends=False, skip_upcoming=False,
        )

    def test_earnings_dividends_sync_skip_flags_are_forwarded(self):
        expected = EarningsDividendsSyncResult(1, 0, 3)
        with patch.object(app, "sync_earnings_dividends", return_value=expected) as sync:
            output = app.main(["earnings-dividends-sync", "--skip-earnings"])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(
            app.DATABASE_PATH, None, skip_earnings=True, skip_dividends=False, skip_upcoming=False,
        )

    def test_earnings_dividends_sync_skip_upcoming_flag_is_forwarded(self):
        expected = EarningsDividendsSyncResult(1, 5, 3, upcoming_dividend_rows=0)
        with patch.object(app, "sync_earnings_dividends", return_value=expected) as sync:
            output = app.main(["earnings-dividends-sync", "--skip-upcoming"])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(
            app.DATABASE_PATH, None, skip_earnings=False, skip_dividends=False, skip_upcoming=True,
        )

    def test_earnings_dividends_sync_failure_returns_nonzero(self):
        expected = EarningsDividendsSyncResult(1, 0, 0, error="provider down")
        with patch.object(app, "sync_earnings_dividends", return_value=expected):
            output = app.main(["earnings-dividends-sync"])

        self.assertEqual(output, 1)

    def test_financial_snapshots_sync_command_forwards_database_and_tickers(self):
        expected = FinancialSnapshotsSyncResult(2, 8)
        with patch.object(app, "sync_financial_snapshots", return_value=expected) as sync:
            output = app.main([
                "financial-snapshots-sync",
                "--database",
                str(self.data_dir / "db.duckdb"),
                "--tickers",
                "AAPL",
                "VFV.TO",
            ])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(self.data_dir / "db.duckdb", ["AAPL", "VFV.TO"])

    def test_financial_snapshots_sync_failure_returns_nonzero(self):
        expected = FinancialSnapshotsSyncResult(1, 0, error="provider down")
        with patch.object(app, "sync_financial_snapshots", return_value=expected):
            output = app.main(["financial-snapshots-sync"])

        self.assertEqual(output, 1)

    def test_classification_sync_command_forwards_input_and_database(self):
        with patch.object(app, "upload_portfolio_classifications", return_value=3) as upload:
            output = app.main([
                "classification-sync",
                "--input",
                str(self.data_dir / "classification.json"),
                "--database",
                str(self.data_dir / "db.duckdb"),
            ])

        self.assertEqual(output, 0)
        upload.assert_called_once_with(
            self.data_dir / "classification.json", self.data_dir / "db.duckdb"
        )

    def test_classification_sync_failure_returns_nonzero(self):
        with patch.object(app, "upload_portfolio_classifications", side_effect=RuntimeError("bad json")):
            output = app.main(["classification-sync"])

        self.assertEqual(output, 1)

    def test_reconcile_holdings_command_exit_codes(self):
        db_path = self.data_dir / "db.duckdb"
        database.initialize_database(db_path)
        connection = database.get_shared_connection(db_path)
        ticker_id = connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES ('PZA', 'TSX', 'CAD', 'Pizza Pizza', 'stock') RETURNING ticker_id"""
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES ('2025-01-02', 'BUY', ?, 5, 100)",
            [ticker_id],
        )
        connection.execute(
            "INSERT INTO historical_records VALUES (?, '2025-01-03', 20, 20, 20, 20, 20, 100)",
            [ticker_id],
        )

        matching_csv = self.data_dir / "matching.csv"
        matching_csv.write_text(
            'Symbol,Quantity,"Book Value (CAD)","Market Unrealized Returns"\n'
            'PZA,5,100.00,0.00\n',
            encoding="utf-8",
        )
        mismatched_csv = self.data_dir / "mismatched.csv"
        mismatched_csv.write_text(
            'Symbol,Quantity,"Book Value (CAD)","Market Unrealized Returns"\n'
            'PZA,999,100.00,0.00\n',
            encoding="utf-8",
        )

        ok_output = app.main([
            "reconcile-holdings", "--report", str(matching_csv), "--database", str(db_path),
        ])
        mismatch_output = app.main([
            "reconcile-holdings", "--report", str(mismatched_csv), "--database", str(db_path),
        ])

        self.assertEqual(ok_output, 0)
        self.assertEqual(mismatch_output, 1)


class DomainStatusTest(unittest.TestCase):
    """`_domain_status` distinguishes an isolated, expected per-symbol gap
    (a thin/newly-listed ticker, a fund with no earnings/fundamentals) from a
    genuinely broken sync, across all three market-data sync result types."""

    def test_no_failures_is_succeeded(self):
        self.assertEqual(app._domain_status(MarketSyncResult(5, 5)), "succeeded")

    def test_some_symbols_failed_is_degraded(self):
        result = MarketSyncResult(5, 4, error="history fetch failed for: X", failed_symbols=("X",))
        self.assertEqual(app._domain_status(result), "degraded")

    def test_every_symbol_failed_is_failed(self):
        result = MarketSyncResult(2, 0, error="history fetch failed for: A, B", failed_symbols=("A", "B"))
        self.assertEqual(app._domain_status(result), "failed")

    def test_write_rollback_marks_every_target_failed_and_is_failed(self):
        """A rolled-back write leaves every target in `failed_symbols` with
        zero rows (see sync_market_data's rollback path) -- this must still
        be `failed`, not `degraded`, since nothing was actually persisted."""
        result = MarketSyncResult(3, 0, error="bad row", failed_symbols=("A", "B", "C"))
        self.assertEqual(app._domain_status(result), "failed")

    def test_total_fetch_failure_with_no_targets_processed_is_failed(self):
        result = EarningsDividendsSyncResult(2, 0, 0, error="provider failed", failed_symbols=("A", "B"))
        self.assertEqual(app._domain_status(result), "failed")

    def test_partial_earnings_dividends_failure_is_degraded(self):
        result = EarningsDividendsSyncResult(3, 2, 1, error="fetch failed for: C", failed_symbols=("C",))
        self.assertEqual(app._domain_status(result), "degraded")

    def test_partial_financial_snapshots_failure_is_degraded(self):
        result = FinancialSnapshotsSyncResult(4, 3, error="fetch failed for: LYTE", failed_symbols=("LYTE",))
        self.assertEqual(app._domain_status(result), "degraded")

    def test_every_financial_snapshots_symbol_failed_is_failed(self):
        result = FinancialSnapshotsSyncResult(1, 0, error="provider failed", failed_symbols=("A",))
        self.assertEqual(app._domain_status(result), "failed")


class PipelineResultSucceededTest(unittest.TestCase):
    """`PipelineResult.succeeded` drives the CLI exit code and (via
    dashboard/api/main.py's `_run_cli`) the dashboard job status, so a
    `degraded` domain must not fail the whole pipeline while a genuinely
    `failed` one still must."""

    def test_degraded_alongside_succeeded_and_skipped_still_succeeds(self):
        result = app.PipelineResult((
            app.SourceResult("email", None, "succeeded", 3),
            app.SourceResult("market-data", None, "degraded", 5, "history fetch failed for: XNDU"),
            app.SourceResult("financial-snapshots", None, "skipped"),
        ))
        self.assertTrue(result.succeeded)

    def test_any_failed_result_still_fails_the_pipeline(self):
        result = app.PipelineResult((
            app.SourceResult("email", None, "succeeded", 3),
            app.SourceResult("market-data", None, "degraded", 5, "history fetch failed for: XNDU"),
            app.SourceResult("statement", None, "failed", error="unresolved ticker(s): FOO"),
        ))
        self.assertFalse(result.succeeded)


class RetryQuarantinedExportsTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "db.duckdb"

    def _write_export(self, name: str, symbol: str) -> Path:
        export = self.data_dir / name
        export.write_text(
            "transaction_date,settlement_date,account_id,account_type,activity_type,"
            "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,"
            "commission,net_cash_amount\n"
            f"2025-04-01,2025-04-03,A1,TFSA,Trade,Buy,Buy,{symbol},{symbol} Inc.,CAD,1,10,0,-10\n",
            encoding="utf-8",
        )
        return export

    def _quarantine_one_export(self, symbol: str) -> int:
        """Stage+resolve one export file with an unresolvable symbol, returning
        its staged_file_id -- mirrors what run_full_exports does today when a
        ticker doesn't exist yet."""
        self._write_export(f"activities-export-{symbol}.csv", symbol)
        app.run_full_exports(self.data_dir, self.db_path)
        row = database.get_shared_connection(self.db_path).execute(
            "SELECT staged_file_id FROM staged_files WHERE source_type = 'export'"
        ).fetchone()
        self.assertIsNotNone(row, "expected the export to be staged and quarantined")
        return int(row[0])

    def _insert_mapping(self, source_symbol: str, currency: str = "CAD") -> int:
        connection = database.get_shared_connection(self.db_path)
        ticker_id = int(connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES (?, 'TSX', ?, ?, 'equity') RETURNING ticker_id",
            [source_symbol, currency, f"{source_symbol} Inc."],
        ).fetchone()[0])
        connection.execute(
            "INSERT INTO ticker_symbol_history (ticker_id, source_symbol, provider_symbol, "
            "currency, exchange, reason, mapping_source, created_by) "
            "VALUES (?, ?, ?, ?, 'TSX', 'resolved pending ticker', 'manual', 'user')",
            [ticker_id, source_symbol, f"{source_symbol}.TO", currency],
        )
        return ticker_id

    def test_retry_republishes_quarantined_export_in_place(self):
        staged_file_id = self._quarantine_one_export("MDA")
        status = database.get_shared_connection(self.db_path).execute(
            "SELECT status FROM staged_files WHERE staged_file_id = ?", [staged_file_id]
        ).fetchone()[0]
        self.assertEqual(status, "quarantined")
        self._insert_mapping("MDA")

        results = app.retry_quarantined_exports_for_symbol("MDA", self.db_path, self.data_dir)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "succeeded")
        connection = database.get_shared_connection(self.db_path)
        export_files = connection.execute(
            "SELECT staged_file_id, status FROM staged_files WHERE source_type = 'export'"
        ).fetchall()
        # Reprocessed in place: still exactly one staged_files row, now published.
        self.assertEqual(export_files, [(staged_file_id, "published")])
        resolution = connection.execute(
            "SELECT resolution_status FROM staged_records WHERE staged_file_id = ?", [staged_file_id]
        ).fetchone()[0]
        self.assertEqual(resolution, "resolved")

    def test_retry_without_saved_mapping_returns_clear_error(self):
        results = app.retry_quarantined_exports_for_symbol("ZZZZ", self.db_path, self.data_dir)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "failed")
        self.assertIn("No saved mapping", results[0].error)

    def test_retry_reports_missing_source_file_without_crashing(self):
        staged_file_id = self._quarantine_one_export("MDA")
        self._insert_mapping("MDA")
        # Simulate the source CSV having been moved/deleted since it was quarantined.
        (self.data_dir / "activities-export-MDA.csv").unlink()

        results = app.retry_quarantined_exports_for_symbol("MDA", self.db_path, self.data_dir)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "failed")
        self.assertIn("no longer at", results[0].error)
        status = database.get_shared_connection(self.db_path).execute(
            "SELECT status FROM staged_files WHERE staged_file_id = ?", [staged_file_id]
        ).fetchone()[0]
        self.assertEqual(status, "quarantined")

    def test_retry_leaves_file_quarantined_when_another_symbol_still_unresolved(self):
        export = self.data_dir / "activities-export-multi.csv"
        export.write_text(
            "transaction_date,settlement_date,account_id,account_type,activity_type,"
            "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,"
            "commission,net_cash_amount\n"
            "2025-04-01,2025-04-03,A1,TFSA,Trade,Buy,Buy,MDA,MDA Inc.,CAD,1,10,0,-10\n"
            "2025-04-01,2025-04-03,A1,TFSA,Trade,Buy,Buy,OTHER,Other Inc.,CAD,1,10,0,-10\n",
            encoding="utf-8",
        )
        app.run_full_exports(self.data_dir, self.db_path)
        connection = database.get_shared_connection(self.db_path)
        staged_file_id = int(connection.execute(
            "SELECT staged_file_id FROM staged_files WHERE source_type = 'export'"
        ).fetchone()[0])
        self._insert_mapping("MDA")

        results = app.retry_quarantined_exports_for_symbol("MDA", self.db_path, self.data_dir)

        self.assertEqual(results[0].status, "failed")
        self.assertIn("OTHER", results[0].error)
        mda_status, other_status = connection.execute(
            "SELECT resolution_status FROM staged_records WHERE staged_file_id = ? "
            "ORDER BY source_symbol", [staged_file_id]
        ).fetchall()
        self.assertEqual(mda_status[0], "resolved")
        self.assertEqual(other_status[0], "unresolved")
        status = connection.execute(
            "SELECT status FROM staged_files WHERE staged_file_id = ?", [staged_file_id]
        ).fetchone()[0]
        self.assertEqual(status, "quarantined")


if __name__ == "__main__":
    unittest.main()
