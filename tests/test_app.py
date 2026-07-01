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
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

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


if __name__ == "__main__":
    unittest.main()
