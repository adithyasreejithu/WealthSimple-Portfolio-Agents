import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from statement_extractor import (
    OUTPUT_COLUMNS,
    export_run_csv,
    extract_glossary_from_tables,
    merge_wrapped_activity_rows,
    parse_activity_rows,
    trim_activity_table,
)


class StatementExtractorTest(unittest.TestCase):
    def test_trim_activity_table_starts_at_first_dated_row_and_stops_at_future_settlement(self):
        raw = pd.DataFrame(
            [
                ["Activity - Current period", "", "", "", "", ""],
                ["Date", "Transaction Description", "", "Debit ($)", "Credit ($)", "Balance ($)"],
                ["2025-04-01", "CONT", "Contribution (executed at 2025-04-01)", "$0.00", "$10.00", "$10.00"],
                ["Transactions for Future Settlement", "", "", "", "", ""],
                ["2025-05-01", "BUY", "Future row", "$1.00", "$0.00", ""],
            ]
        )

        trimmed = trim_activity_table(raw)

        self.assertEqual(len(trimmed), 1)
        self.assertIn("2025-04-01", " ".join(trimmed.iloc[0].astype(str)))

    def test_wrapped_activity_rows_are_merged_with_their_dated_row(self):
        raw = pd.DataFrame(
            [
                ["Date", "Transaction Description", "", "Debit ($)", "Credit ($)", "Balance ($)"],
                ["2025-04-02", "BUY", "SPLG - ETF: Bought 0.0029 shares (executed at", "$0.28", "$0.00", "$17.52"],
                ["", "", "2025-04-01), FX Rate: 1.4664", "", "", ""],
            ]
        )

        merged = merge_wrapped_activity_rows(raw)

        self.assertEqual(len(merged), 1)
        self.assertIn("FX Rate: 1.4664", merged.loc[0, "raw_text"])

    def test_parse_activity_rows_retains_statement_code_and_expanded_fields(self):
        rows = pd.DataFrame(
            {
                "raw_text": [
                    "2025-04-02 BUY SPLG - SPDR Portfolio S&P 500 ETF: Bought 0.0029 shares (executed at 2025-04-01), FX Rate: 1.4664 $0.28 $0.00 $17.52",
                    "2025-04-03 LOAN T - AT&T, Inc.: 4.0000 Shares on loan (executed at 2025-04-03) $0.00 $0.00 $16.45",
                    "2025-04-04 RECALL T - AT&T, Inc.: Loan of 4.0000 shares terminated (executed at 2025-04-04) $0.00 $0.00 $15.95",
                    "2025-04-14 FPLINT Stock lending monthly interest payment $0.00 $0.01 $6.17",
                    "2025-04-15 NRT Non resident tax withheld (executed at 2025-04-15) $0.03 $0.00 $6.14",
                ]
            }
        )

        parsed = parse_activity_rows(rows)

        self.assertEqual(parsed["statement_code"].tolist(), ["BUY", "LOAN", "RECALL", "FPLINT", "NRT"])
        self.assertEqual(parsed.loc[0, "quantity"], "0.0029")
        self.assertEqual(parsed.loc[0, "fx_rate"], "1.4664")
        self.assertEqual(parsed.loc[1, "quantity"], "4.0000")
        self.assertEqual(parsed.loc[2, "quantity"], "4.0000")
        self.assertEqual(parsed.loc[3, "credit"], "$0.01")
        self.assertEqual(parsed.loc[4, "debit"], "$0.03")
        self.assertEqual(parsed.loc[4, "execDate"], "2025-04-15")

    def test_unknown_activity_code_is_retained_for_later_mapping(self):
        rows = pd.DataFrame({"raw_text": ["2025-04-30 NEWCODE Some future statement code $0.00 $1.00 $2.00"]})

        parsed = parse_activity_rows(rows)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.loc[0, "statement_code"], "NEWCODE")
        self.assertEqual(parsed.loc[0, "transaction"], "NEWCODE")

    def test_glossary_extraction_keeps_code_and_description_columns(self):
        table = pd.DataFrame(
            [
                ["Information about Statement Codes", "", ""],
                ["BUY - Purchase of assets", "LOAN - Stocks on loan", "RECALL - Termination of stock on"],
                ["", "account", "loan"],
            ]
        )

        glossary = extract_glossary_from_tables([table])

        self.assertEqual(list(glossary.columns), ["code", "description"])
        self.assertEqual(glossary.set_index("code").loc["BUY", "description"], "Purchase of assets")
        self.assertEqual(glossary.set_index("code").loc["LOAN", "description"], "Stocks on loan account")

    def test_export_run_csv_creates_exports_folder_and_writes_current_run_data(self):
        data = pd.DataFrame(
            [
                {
                    "date": "2025-04-02",
                    "transaction": "BUY",
                    "ticker_id": "SPLG",
                    "quantity": "0.0029",
                    "execDate": "2025-04-01",
                    "fx_rate": "1.4664",
                    "debit": "$0.28",
                    "credit": "$0.00",
                    "balance": "$17.52",
                    "statement_code": "BUY",
                    "description": "SPDR Portfolio S&P 500 ETF",
                }
            ],
            columns=OUTPUT_COLUMNS,
        )
        export_folder = Path("exports")

        with patch.object(pd.DataFrame, "to_csv") as to_csv:
            export_path = export_run_csv(data, export_folder)

        self.assertTrue(export_path.name.startswith("statement_transactions_"))
        self.assertEqual(export_path.suffix, ".csv")
        self.assertEqual(export_path.parent, export_folder)
        to_csv.assert_called_once_with(export_path, index=False)


if __name__ == "__main__":
    unittest.main()
