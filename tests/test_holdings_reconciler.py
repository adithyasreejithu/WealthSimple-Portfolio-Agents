import tempfile
import unittest
from datetime import date
from pathlib import Path

import database
from holdings_reconciler import parse_holdings_csv, reconcile_holdings


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


class HoldingsReconcilerTest(unittest.TestCase):
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

    def _seed_holding(self, symbol, quantity, debit, close, currency="CAD"):
        import position_engine

        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES (?, 'TSX', ?, ?, 'stock') RETURNING ticker_id""",
            [symbol, currency, f"{symbol} Inc."],
        ).fetchone()[0]
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

    def test_parse_holdings_csv_skips_blank_and_footer_rows(self):
        self._write_csv(_csv_row("AAPL", "1", "100.00", "10.00"))

        parsed = parse_holdings_csv(self.csv_path)

        self.assertEqual(set(parsed), {"AAPL"})
        self.assertEqual(parsed["AAPL"]["quantity"].normalize(), 1)

    def test_matching_holding_passes_within_tolerance(self):
        # 5 shares * $20 debit = $100 book value; market value 5*$20=$100 too
        # (unrealized ~0), matching a CSV row within tolerance.
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("PZA", "5", "100.00", "0.00"))

        result = reconcile_holdings(self.csv_path, self.db_path)

        self.assertTrue(result.ok)
        self.assertEqual(result.mismatches, [])

    def test_quantity_mismatch_is_reported(self):
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("PZA", "6", "100.00", "0.00"))

        result = reconcile_holdings(self.csv_path, self.db_path)

        self.assertFalse(result.ok)
        fields = {m.field for m in result.mismatches}
        self.assertIn("quantity", fields)

    def test_book_value_outside_tolerance_is_reported(self):
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("PZA", "5", "150.00", "0.00"))

        result = reconcile_holdings(self.csv_path, self.db_path)

        self.assertFalse(result.ok)
        fields = {m.field for m in result.mismatches}
        self.assertIn("book_value_cad", fields)

    def test_small_price_snapshot_lag_is_within_tolerance(self):
        # Real price staleness case: book value matches exactly, but market
        # unrealized P/L differs by a couple dollars because the DB's last
        # stored close differs slightly from the CSV's intraday snapshot.
        self._seed_holding("PZA", quantity=5, debit=100, close=20.3)
        self._write_csv(_csv_row("PZA", "5", "100.00", "1.00"))

        result = reconcile_holdings(self.csv_path, self.db_path)

        self.assertTrue(result.ok)

    def test_missing_ticker_in_each_direction_is_reported(self):
        self._seed_holding("PZA", quantity=5, debit=100, close=20.0)
        self._write_csv(_csv_row("OTHER", "1", "50.00", "0.00"))

        result = reconcile_holdings(self.csv_path, self.db_path)

        self.assertFalse(result.ok)
        self.assertEqual(result.db_only_tickers, ["PZA"])
        self.assertEqual(result.csv_only_tickers, ["OTHER"])


if __name__ == "__main__":
    unittest.main()
