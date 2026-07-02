import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
import database


class AppPipelineTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.data_dir = Path(tempfile.mkdtemp(dir=Path.cwd()))

    def tearDown(self):
        database.close_connection()

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
        with patch.object(app, "extract_statement_pdf", return_value=empty_statement), patch.object(
            app, "fetch_email_transactions", return_value=empty_email
        ):
            result = app.run_pipeline("all", self.data_dir, self.data_dir / "db.duckdb")

        staged_order = [row[0] for row in database.get_shared_connection(
            self.data_dir / "db.duckdb"
        ).execute("SELECT source_type FROM staged_files ORDER BY file_sequence").fetchall()]
        self.assertEqual(staged_order, ["statement", "email", "export"])
        self.assertTrue(result.succeeded)

    def test_analytics_command_prints_report(self):
        report = {
            "portfolio_value": 1234,
            "cash": {"balance": 250, "source": "explicit_balance"},
            "holdings": [
                {
                    "ticker_symbol": "AAPL",
                    "exchange": "NASDAQ",
                    "quantity": 3,
                    "market_value": 984,
                }
            ],
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

    def test_analytics_command_exports_json(self):
        report = {
            "portfolio_value": 1234,
            "cash": {"balance": 250, "source": "explicit_balance"},
            "holdings": [],
        }

        with patch.object(app, "run_analytics", return_value=report), patch(
            "sys.stdout.write"
        ) as write:
            output = app.main([
                "analytics",
                "--export",
                "--database",
                str(self.data_dir / "db.duckdb"),
            ])

        self.assertEqual(output, 0)
        printed = "".join(call.args[0] for call in write.call_args_list)
        self.assertIn('"portfolio_value": 1234', printed)

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
            "ticker-map",
            "import-activities",
        ):
            self.assertIn(command, printed)


if __name__ == "__main__":
    unittest.main()
