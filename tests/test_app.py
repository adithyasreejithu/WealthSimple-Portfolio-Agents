import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
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
            patch.object(app, "ensure_positions_fresh"),
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
            patch.object(app, "ensure_positions_fresh"),
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
            patch.object(app, "ensure_positions_fresh"),
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
            patch.object(app, "ensure_positions_fresh"),
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
            skip_earnings=False, skip_dividends=False,
        )

    def test_earnings_dividends_sync_skip_flags_are_forwarded(self):
        expected = EarningsDividendsSyncResult(1, 0, 3)
        with patch.object(app, "sync_earnings_dividends", return_value=expected) as sync:
            output = app.main(["earnings-dividends-sync", "--skip-earnings"])

        self.assertEqual(output, 0)
        sync.assert_called_once_with(
            app.DATABASE_PATH, None, skip_earnings=True, skip_dividends=False,
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
