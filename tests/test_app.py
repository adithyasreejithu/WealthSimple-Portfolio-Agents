import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
import database
import ticker_mapping
from market_data import MarketSyncResult


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
            patch.object(
                app, "_run_portfolio_classification",
                return_value=app.SourceResult("classification", None, "succeeded", 0),
            ),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        staged_order = [row[0] for row in database.get_shared_connection(
            self.data_dir / "db.duckdb"
        ).execute("SELECT source_type FROM staged_files ORDER BY file_sequence").fetchall()]
        self.assertEqual(staged_order, ["statement", "email", "export"])
        self.assertTrue(result.succeeded)

    def test_email_pipeline_syncs_market_data_after_publication(self):
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
            patch.object(
                app,
                "sync_market_data",
                side_effect=lambda *_: events.append("market") or MarketSyncResult(1, 2),
            ) as sync,
            patch.object(
                app, "_run_portfolio_classification",
                return_value=app.SourceResult("classification", None, "succeeded", 0),
            ),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        self.assertEqual(events, ["email", "market", "batch"])
        sync.assert_called_once_with(self.data_dir / "db.duckdb")
        self.assertEqual(result.results[-2], app.SourceResult("email", None, "succeeded", 0))
        self.assertEqual(result.results[-1], app.SourceResult("classification", None, "succeeded", 0))

    def test_source_specific_pipeline_does_not_sync_market_data(self):
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "sync_market_data") as sync,
        ):
            app.run_pipeline("statements", self.data_dir, self.data_dir / "db.duckdb")

        sync.assert_not_called()

    def test_failed_ingestion_does_not_sync_market_data(self):
        failed = app.SourceResult("statement", None, "failed", error="bad statement")
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [failed])),
            patch.object(app, "_stage_email_batch", return_value=([], 2, [])),
            patch.object(app, "_stage_export_files", return_value=([], 3, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "sync_market_data") as sync,
            patch.object(
                app, "_run_portfolio_classification",
                return_value=app.SourceResult("classification", None, "succeeded", 0),
            ),
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        sync.assert_not_called()
        self.assertFalse(result.succeeded)

    def test_full_pipeline_skips_classification_for_partial_source(self):
        with (
            patch.object(app, "initialize_database"),
            patch.object(app, "create_batch", return_value=7),
            patch.object(app, "_stage_statement_files", return_value=([], 1, [])),
            patch.object(app, "complete_batch"),
            patch.object(app, "_run_portfolio_classification") as classify,
        ):
            app.run_pipeline("statements", self.data_dir, self.data_dir / "db.duckdb")

        classify.assert_not_called()

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
            "email", self.data_dir, self.data_dir / "db.duckdb"
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
            "analytics",
            "statements",
            "email",
            "yfinance",
            "yfinance-sync",
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


if __name__ == "__main__":
    unittest.main()
