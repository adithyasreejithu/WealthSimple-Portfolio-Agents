import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(
    0, str(ROOT / ".claude" / "skills" / "_archive" / "reconcile-holdings-report" / "scripts")
)

import database

from generate_reconciliation_report import build_report

CSV_HEADER = (
    "Account Name,Account Type,Account Classification,Account Number,Symbol,Exchange,MIC,"
    "Name,Security Type,Quantity,Position Direction,Market Price,Market Price Currency,"
    "Book Value (CAD),Book Value Currency (CAD),Book Value (Market),Book Value Currency (Market),"
    "Market Value,Market Value Currency,Market Unrealized Returns,Market Unrealized Returns Currency\n"
)


def _csv_row(symbol, quantity, book_value_cad, unrealized_mkt):
    return (
        f'"TFSA","TFSA","Trade","ACCT1","{symbol}","NASDAQ","XNAS","{symbol} Inc","EQUITY",'
        f'"{quantity}","LONG","100.00","USD","{book_value_cad}","CAD","90.00","USD","100.00",'
        f'"USD","{unrealized_mkt}","USD"\n'
    )


class GenerateReconciliationReportTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)
        self.csv_path = Path(self.temp_dir.name) / "holdings.csv"

    def _write_csv(self, rows: str):
        self.csv_path.write_text(CSV_HEADER + rows + "\n\"As of 2026-07-07 22:15 GMT-04:00\"\n", encoding="utf-8")

    def _seed_ticker(self, symbol, currency="CAD"):
        return self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES (?, 'TSX', ?, ?, 'stock') RETURNING ticker_id""",
            [symbol, currency, f"{symbol} Inc."],
        ).fetchone()[0]

    def _seed_holding(self, symbol, quantity, debit, close, currency="CAD"):
        import position_engine

        ticker_id = self._seed_ticker(symbol, currency)
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, ?, ?)",
            [date(2025, 1, 2), ticker_id, quantity, debit],
        )
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 1, 3), close, close, close, close, close, 100],
        )
        position_engine.recompute_positions(self.connection)
        return ticker_id

    def test_no_mismatches_omits_root_cause_section(self):
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("PZA", "5", "100.00", "0.00"))

        report = build_report(self.csv_path, self.db_path, generated_at=date(2026, 7, 8))

        self.assertIn("# Holdings Reconciliation Report", report)
        self.assertIn("**PZA**", report)
        self.assertNotIn("## Root Cause Analysis", report)
        self.assertIn("no data-quality issues observed", report)

    def test_buy_missing_cost_is_tagged_rc2c(self):
        import position_engine

        ticker_id = self._seed_ticker("PZA")
        # BUY with no debit -> position_engine flags buy_missing_cost.
        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, ?, NULL)",
            [date(2025, 1, 2), ticker_id, 5],
        )
        self.connection.execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, date(2025, 1, 3), 20.0, 20.0, 20.0, 20.0, 20.0, 100],
        )
        position_engine.recompute_positions(self.connection)
        # CSV disagrees on book value (DB has 0 cost, CSV expects 100) so a
        # mismatch is actually observed for the root-cause section to cover.
        self._write_csv(_csv_row("PZA", "5", "100.00", "0.00"))

        report = build_report(self.csv_path, self.db_path, generated_at=date(2026, 7, 8))

        self.assertIn("## Root Cause Analysis", report)
        self.assertIn("RC2c", report)
        self.assertIn("PZA", report)

    def test_identical_inputs_produce_identical_output(self):
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("PZA", "6", "100.00", "0.00"))

        first = build_report(self.csv_path, self.db_path, generated_at=date(2026, 7, 8))
        second = build_report(self.csv_path, self.db_path, generated_at=date(2026, 7, 8))

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
