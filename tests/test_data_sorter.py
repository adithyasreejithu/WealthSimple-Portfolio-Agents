import csv
import tempfile
import unittest
from pathlib import Path

import database
from config import ACTIVITY_EXPORT_COLUMNS
from data_sorter import sort_data


class DataSorterImportTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        )
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def tearDown(self):
        database.close_connection()
        self.temp_dir.cleanup()

    def _insert_ticker(
        self,
        symbol="VFV.TO",
        exchange="TSX",
        currency="CAD",
        name="Vanguard S&P 500 Index ETF",
        security_type="etf",
    ):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            """
            INSERT INTO tickers (
                ticker_symbol,
                exchange,
                currency,
                security_name,
                security_type
            )
            VALUES (?, ?, ?, ?, ?)
            RETURNING ticker_id
            """,
            [symbol, exchange, currency, name, security_type],
        ).fetchone()[0]

    def _row(self, **overrides):
        row = {
            "transaction_date": "2025-01-02",
            "settlement_date": "2025-01-03",
            "account_id": "ACCOUNT-1",
            "account_type": "TFSA",
            "activity_type": "Trade",
            "activity_sub_type": "BUY",
            "direction": "LONG",
            "symbol": "VFV",
            "name": "Vanguard S&P 500 Index ETF",
            "currency": "CAD",
            "quantity": "2",
            "unit_price": "100.50",
            "commission": "0",
            "net_cash_amount": "-201.00",
        }
        row.update(overrides)
        return row

    def _write_export(self, filename, rows):
        path = self.data_dir / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ACTIVITY_EXPORT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_successful_import_preserves_raw_symbol_and_normalizes_ticker(self):
        ticker_id = self._insert_ticker()
        source = self._write_export(
            "activities-export-2025-01-04.csv",
            [self._row()],
        )

        result = sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)
        raw_symbol = connection.execute(
            "SELECT symbol FROM raw_activity_exports"
        ).fetchone()[0]
        activity = connection.execute(
            "SELECT ticker_id, activity_code FROM activities"
        ).fetchone()
        activity_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('activities')"
            ).fetchall()
        }

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(raw_symbol, "VFV")
        self.assertEqual(activity, (ticker_id, "BUY"))
        self.assertNotIn("symbol", activity_columns)
        self.assertFalse(source.exists())
        self.assertTrue(result.processed_path.exists())

    def test_ambiguous_ticker_rejects_normalized_load_and_keeps_source(self):
        self._insert_ticker(
            symbol="ABC.TO",
            exchange="TSX",
            currency="CAD",
            name="Shared Name",
        )
        self._insert_ticker(
            symbol="ABC.TO",
            exchange="NYSE",
            currency="CAD",
            name="Shared Name",
        )
        source = self._write_export(
            "activities-export-2025-02-01.csv",
            [self._row(symbol="ABC", name="Shared Name")],
        )

        result = sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM raw_activity_exports"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM activities"
            ).fetchone()[0],
            0,
        )
        self.assertTrue(source.exists())
        self.assertEqual(len(result.unresolved_dataframe), 1)

    def test_overlapping_exports_append_new_rows_and_deduplicate_history(self):
        self._insert_ticker()
        first_row = self._row()
        first_source = self._write_export(
            "activities-export-2025-01-04.csv",
            [first_row],
        )
        first_result = sort_data(
            source_file=first_source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        second_source = self._write_export(
            "activities-export-2025-02-04.csv",
            [
                first_row,
                self._row(
                    transaction_date="2025-02-02",
                    settlement_date="2025-02-03",
                ),
            ],
        )

        second_result = sort_data(
            source_file=second_source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)

        self.assertEqual(first_result.duplicate_rows, 0)
        self.assertEqual(second_result.duplicate_rows, 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0],
            2,
        )
        seen_imports = connection.execute(
            """
            SELECT first_seen_import_id, last_seen_import_id
            FROM activities
            WHERE transaction_date = DATE '2025-01-02'
            """
        ).fetchone()
        self.assertEqual(
            seen_imports,
            (first_result.import_id, second_result.import_id),
        )

    def test_identical_legitimate_rows_keep_separate_ordinals(self):
        self._insert_ticker()
        row = self._row()
        source = self._write_export(
            "activities-export-2025-03-04.csv",
            [row, row],
        )

        sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)
        ordinals = connection.execute(
            """
            SELECT duplicate_ordinal
            FROM activities
            ORDER BY duplicate_ordinal
            """
        ).fetchall()

        self.assertEqual(ordinals, [(1,), (2,)])

    def test_cash_activity_allows_null_ticker_id(self):
        source = self._write_export(
            "activities-export-2025-04-04.csv",
            [
                self._row(
                    activity_type="MoneyMovement",
                    activity_sub_type="E_TRFIN",
                    direction="",
                    symbol="",
                    name="",
                    quantity="",
                    unit_price="",
                    net_cash_amount="500",
                )
            ],
        )

        result = sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)
        activity = connection.execute(
            "SELECT ticker_id, activity_code FROM activities"
        ).fetchone()

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(activity, (None, "CONT"))

    def test_rejected_import_can_be_retried_after_ticker_is_added(self):
        source = self._write_export(
            "activities-export-2025-05-04.csv",
            [self._row(symbol="NEW", name="New Security")],
        )
        rejected = sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        self._insert_ticker(symbol="NEW.TO", name="New Security")

        retried = sort_data(
            source_file=source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(retried.status, "succeeded")
        self.assertTrue(retried.duplicate_file)
        self.assertEqual(rejected.import_id, retried.import_id)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM raw_activity_exports"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0],
            1,
        )

    def test_duplicate_file_hash_does_not_create_another_import(self):
        self._insert_ticker()
        first_source = self._write_export(
            "activities-export-2025-06-04.csv",
            [self._row()],
        )
        first = sort_data(
            source_file=first_source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        duplicate_source = self._write_export(
            "activities-export-2025-06-05.csv",
            [self._row()],
        )

        duplicate = sort_data(
            source_file=duplicate_source,
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        connection = database.get_shared_connection(self.db_path)

        self.assertTrue(duplicate.duplicate_file)
        self.assertEqual(first.import_id, duplicate.import_id)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM activity_imports"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
