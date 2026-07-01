import unittest
from datetime import date
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from email_extractor import (
    OUTPUT_COLUMNS,
    export_run_csv,
    fetch_email_transactions,
    message_from_interac,
    message_from_wealthsimple,
    message_subject_matches_interac,
    parse_interac_email,
    parse_wealthsimple_email,
    resolve_email_date,
    wealthsimple_subject_type,
)


class StubMailbox:
    def __init__(self, responses):
        self.responses = list(responses)
        self.fetch_calls = []

    def fetch(self, query):
        self.fetch_calls.append(query)
        return self.responses.pop(0)


class EmailExtractorTest(unittest.TestCase):
    """
    Cover the Wealthsimple and Interac parser behavior without requiring a
    live mailbox connection.
    """

    def test_parse_wealthsimple_filled_email_keeps_existing_fields(self):
        email = """
        Account: TFSA
        Type: Buy
        Symbol: XEQT
        Shares: 3.0000
        Average price: $31.60
        Total cost: $94.80
        Time: 2025-04-10 09:30:00
        Amount: $94.80
        """

        parsed = parse_wealthsimple_email(email, "Your order filled")

        self.assertEqual(list(parsed.columns), OUTPUT_COLUMNS)
        self.assertEqual(parsed.loc[0, "account"], "TFSA")
        self.assertEqual(parsed.loc[0, "transaction"], "Buy")
        self.assertEqual(parsed.loc[0, "ticker_id"], "")
        self.assertEqual(parsed.loc[0, "ticker"], "XEQT")
        self.assertEqual(parsed.loc[0, "quantity"], "3.0000")
        self.assertEqual(parsed.loc[0, "avg_price"], "31.60")
        self.assertEqual(parsed.loc[0, "total_cost"], "94.80")
        self.assertEqual(parsed.loc[0, "debit"], "94.80")
        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 10))

    def test_parse_wealthsimple_dividend_email_uses_subject_type_and_received_date_fallback(self):
        email = """
        Account: TFSA
        Symbol: VFV
        Amount: $1.25
        """

        parsed = parse_wealthsimple_email(email, "Cash dividend received", received_date=date(2025, 4, 3))

        self.assertEqual(parsed.loc[0, "transaction"], "Dividend")
        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 3))
        self.assertEqual(parsed.loc[0, "debit"], "1.25")

    def test_parse_wealthsimple_email_preserves_us_price_currency(self):
        parsed = parse_wealthsimple_email(
            "Account: TFSA\nType: Buy\nSymbol: AAPL\nShares: 1\nAverage price: US$200.00\nTotal cost: US$200.00\nTime: 2025-04-10",
            "Your order filled",
        )
        self.assertEqual(parsed.loc[0, "price_currency"], "USD")

    def test_parse_wealthsimple_email_preserves_cad_price_currency(self):
        parsed = parse_wealthsimple_email(
            "Account: TFSA\nType: Buy\nSymbol: BN\nShares: 1\nAverage price: CA$75.00\nTotal cost: CA$75.00\nTime: 2025-04-10",
            "Your order filled",
        )
        self.assertEqual(parsed.loc[0, "price_currency"], "CAD")

    def test_parse_wealthsimple_limit_buy_email_reads_date_without_time_label(self):
        email = """
        Account: TFSA
        Type: Buy
        Symbol: XEQT
        Shares: 2.0000
        Average price: $30.10
        Total cost: $60.20
        Filled at: 2025-04-12 14:35:00
        Amount: $60.20
        """

        parsed = parse_wealthsimple_email(email, "Your limit buy order filled", received_date=date(2025, 4, 13))

        self.assertEqual(parsed.loc[0, "transaction"], "Buy")
        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 12))

    """
    Validate the strict sender and subject filters before any parser runs.
    """

    def test_filter_helpers_match_expected_messages(self):
        wealthsimple_msg = SimpleNamespace(
            from_="notifications@o.wealthsimple.com",
            subject="Your order filled",
        )
        interac_msg = SimpleNamespace(
            from_="catch@payments.interac.ca",
            subject="Interac e-Transfer: ADITHYA SREEJITHU PANICKER sent you money",
        )

        self.assertTrue(message_from_wealthsimple(wealthsimple_msg))
        self.assertEqual(wealthsimple_subject_type(wealthsimple_msg.subject), "filled")
        self.assertTrue(message_from_interac(interac_msg))
        self.assertTrue(message_subject_matches_interac(interac_msg))

    def test_parse_interac_email_extracts_deposit_amount_and_date(self):
        email = """
        Interac e-Transfer deposit
        Deposited on April 9, 2025
        Amount CA$300.00
        """

        parsed = parse_interac_email(email, "Interac e-Transfer: ADITHYA SREEJITHU PANICKER")

        self.assertEqual(list(parsed.columns), OUTPUT_COLUMNS)
        self.assertEqual(parsed.loc[0, "account"], "TFSA")
        self.assertEqual(parsed.loc[0, "transaction"], "Deposit")
        self.assertEqual(parsed.loc[0, "ticker_id"], 0)
        self.assertEqual(parsed.loc[0, "ticker"], "EMAIL")
        self.assertEqual(parsed.loc[0, "quantity"], "")
        self.assertEqual(parsed.loc[0, "avg_price"], "")
        self.assertEqual(parsed.loc[0, "total_cost"], "")
        self.assertEqual(parsed.loc[0, "debit"], "300.00")
        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 9))

    """
    Confirm the shared date resolution keeps one guaranteed output field
    using parsed date first and received date second.
    """

    def test_resolve_email_date_prefers_parsed_date(self):
        resolved = resolve_email_date(date(2025, 4, 10), date(2025, 4, 11), "Wealthsimple")

        self.assertEqual(resolved, date(2025, 4, 10))

    def test_parse_wealthsimple_email_uses_received_date_when_no_body_date_is_present(self):
        email = """
        Account: TFSA
        Type: Buy
        Symbol: XEQT
        Shares: 1.0000
        Average price: $31.00
        Total cost: $31.00
        Amount: $31.00
        """

        parsed = parse_wealthsimple_email(email, "Your market buy order filled", received_date=date(2025, 4, 8))

        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 8))

    def test_parse_interac_email_uses_received_date_when_body_date_is_missing(self):
        email = """
        Interac e-Transfer deposit
        Amount CA$300.00
        """

        parsed = parse_interac_email(
            email,
            "Interac e-Transfer: ADITHYA SREEJITHU PANICKER",
            received_date=date(2025, 4, 8),
        )

        self.assertEqual(parsed.loc[0, "date"], date(2025, 4, 8))

    def test_resolve_email_date_warns_and_returns_blank_when_no_date_exists(self):
        with self.assertLogs("email_extractor", level="WARNING") as captured:
            resolved = resolve_email_date(None, None, "Interac")

        self.assertEqual(resolved, "")
        self.assertIn("No date could be resolved", "\n".join(captured.output))

    """
    Confirm the combined fetch flow returns one normalized dataframe with the
    agreed column order and blank handling.
    """

    def test_fetch_email_transactions_combines_and_sorts_rows(self):
        wealthsimple_msg = SimpleNamespace(
            from_="support@wealthsimple.com",
            subject="Your order filled",
            text="""
            Account: TFSA
            Type: Buy
            Symbol: XEQT
            Shares: 3.0000
            Average price: $31.60
            Total cost: $94.80
            Time: 2025-04-10 09:30:00
            Amount: $94.80
            """,
            date=datetime_date(2025, 4, 10),
        )
        interac_msg = SimpleNamespace(
            from_="catch@payments.interac.ca",
            subject="Interac e-Transfer: ADITHYA SREEJITHU PANICKER sent you money",
            text="""
            Interac e-Transfer deposit
            Deposited on April 9, 2025
            Amount CA$300.00
            """,
            date=datetime_date(2025, 4, 9),
        )
        mailbox = StubMailbox([[wealthsimple_msg], [interac_msg]])

        combined = fetch_email_transactions(mailbox=mailbox, start_date=date(2025, 4, 1))

        self.assertEqual(list(combined.columns), OUTPUT_COLUMNS)
        self.assertEqual(combined["transaction"].tolist(), ["Deposit", "Buy"])
        self.assertEqual(combined["date"].tolist(), [date(2025, 4, 9), date(2025, 4, 10)])
        self.assertEqual(combined.loc[0, "quantity"], "")
        self.assertEqual(combined.loc[1, "ticker_id"], "")

    def test_fetch_email_transactions_returns_empty_dataframe_when_no_matches_exist(self):
        mailbox = StubMailbox([[], []])

        combined = fetch_email_transactions(mailbox=mailbox, start_date=date(2025, 4, 1))

        self.assertTrue(combined.empty)
        self.assertEqual(list(combined.columns), OUTPUT_COLUMNS)

    """
    Confirm the export helper writes to the shared exports folder pattern
    without changing the combined email dataframe shape.
    """

    def test_export_run_csv_writes_timestamped_email_export(self):
        data = pd.DataFrame(
            [
                {
                    "account": "TFSA",
                    "transaction": "Deposit",
                    "ticker_id": 0,
                    "ticker": "EMAIL",
                    "quantity": "",
                    "avg_price": "",
                    "total_cost": "",
                    "debit": "300.00",
                    "date": date(2025, 4, 9),
                }
            ],
            columns=OUTPUT_COLUMNS,
        )
        export_folder = Path("exports")

        with patch.object(pd.DataFrame, "to_csv") as to_csv:
            export_path = export_run_csv(data, export_folder)

        self.assertTrue(export_path.name.startswith("email_transactions_"))
        self.assertEqual(export_path.suffix, ".csv")
        self.assertEqual(export_path.parent, export_folder)
        to_csv.assert_called_once_with(export_path, index=False)


def datetime_date(year: int, month: int, day: int):
    return SimpleNamespace(date=lambda: date(year, month, day))


if __name__ == "__main__":
    unittest.main()
