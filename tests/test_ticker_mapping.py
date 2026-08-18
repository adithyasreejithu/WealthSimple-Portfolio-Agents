import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import database
import ticker_mapping
import yfinance_extractor


_EMPTY_STOCKS = pd.DataFrame(columns=yfinance_extractor.STOCK_INFO_COLUMNS)
_EMPTY_ETFS = pd.DataFrame(columns=yfinance_extractor.ETF_INFO_COLUMNS)


def _stock_match(provider_symbol: str, company_name: str = "MDA Space Ltd.", exchange: str = "Toronto") -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": provider_symbol, "provider_symbol": provider_symbol,
        "company_name": company_name, "asset": "EQUITY", "exchange": exchange,
        "currency": "CAD", "financial_currency": "CAD", "sector": "Industrials", "industry": "Aerospace & Defense",
    }], columns=yfinance_extractor.STOCK_INFO_COLUMNS)


class TickerMappingInteractionTest(unittest.TestCase):
    def test_interactive_pending_mapping_requires_confirmation(self):
        pending = [{
            "source_symbol": "XNDU", "detected_currency": "CAD",
            "trade_count": 1, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "", "TSX", "n"])
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping") as add_mapping,
            patch.object(ticker_mapping, "configure_yfinance_cache"),
            patch.object(ticker_mapping, "fetch_security_info", return_value=(_stock_match("XNDU.TO"), _EMPTY_ETFS)),
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        add_mapping.assert_not_called()
        self.assertEqual(result, [{"source_symbol": "XNDU", "status": "skipped"}])

    def test_interactive_pending_mapping_uses_detected_defaults(self):
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD",
            "trade_count": 2, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "", "TSX", "yes"])
        expected = {"source_symbol": "MDA", "status": "verified"}
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping", return_value=expected) as add_mapping,
            patch.object(ticker_mapping, "configure_yfinance_cache"),
            patch.object(ticker_mapping, "fetch_security_info", return_value=(_stock_match("MDA.TO"), _EMPTY_ETFS)),
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        add_mapping.assert_called_once_with(
            "MDA", "MDA", "MDA.TO", "CAD", "TSX",
            reason="resolved pending email ticker", created_by="interactive-cli",
            db_path="portfolio.duckdb",
        )
        self.assertEqual(result, [expected])

    def test_interactive_pending_mapping_rejects_unverifiable_symbol_then_retries(self):
        """A typed value (like "no", meant to reject a bad default) must not be
        silently saved as the provider symbol -- it has to fail verification
        and re-prompt, since this is exactly how MDA got mapped to "NO"."""
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD",
            "trade_count": 1, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "NO", "", "TSX", "yes"])
        expected = {"source_symbol": "MDA", "status": "verified"}
        fetch_results = iter([
            (_EMPTY_STOCKS, _EMPTY_ETFS),
            (_stock_match("MDA.TO"), _EMPTY_ETFS),
        ])
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping", return_value=expected) as add_mapping,
            patch.object(ticker_mapping, "configure_yfinance_cache"),
            patch.object(ticker_mapping, "fetch_security_info", side_effect=lambda *_: next(fetch_results)),
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        add_mapping.assert_called_once_with(
            "MDA", "MDA", "MDA.TO", "CAD", "TSX",
            reason="resolved pending email ticker", created_by="interactive-cli",
            db_path="portfolio.duckdb",
        )
        self.assertEqual(result, [expected])

    def test_interactive_pending_mapping_skip_keyword_abandons_symbol(self):
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD",
            "trade_count": 1, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "skip"])
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping") as add_mapping,
            patch.object(ticker_mapping, "configure_yfinance_cache"),
            patch.object(ticker_mapping, "fetch_security_info") as fetch_security_info,
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        fetch_security_info.assert_not_called()
        add_mapping.assert_not_called()
        self.assertEqual(result, [{"source_symbol": "MDA", "status": "skipped"}])

    def test_interactive_pending_mapping_does_not_double_suffix_canadian_canonical(self):
        """Regression test: entering a canonical symbol that already carries the
        .TO suffix must not produce a "SYMBOL.TO.TO" default, which is what
        confused the user into typing "no" in the first place."""
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD",
            "trade_count": 1, "first_seen": None, "last_seen": None,
        }]
        prompts: list[str] = []
        fixed_answers = {
            "Currency [CAD/USD] [CAD]: ": "",
            "Canonical symbol [MDA]: ": "MDA.TO",
        }
        remaining_answers = iter(["", "TSX", "yes"])

        def _record_and_answer(prompt: str) -> str:
            prompts.append(prompt)
            if prompt in fixed_answers:
                return fixed_answers[prompt]
            return next(remaining_answers)
        expected = {"source_symbol": "MDA", "status": "verified"}
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping", return_value=expected) as add_mapping,
            patch.object(ticker_mapping, "configure_yfinance_cache"),
            patch.object(ticker_mapping, "fetch_security_info", return_value=(_stock_match("MDA.TO"), _EMPTY_ETFS)),
            patch("builtins.input", side_effect=_record_and_answer),
        ):
            ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        self.assertIn("Yahoo symbol [MDA.TO]: ", prompts)
        add_mapping.assert_called_once_with(
            "MDA", "MDA.TO", "MDA.TO", "CAD", "TSX",
            reason="resolved pending email ticker", created_by="interactive-cli",
            db_path="portfolio.duckdb",
        )


class ListPendingTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)

    def test_list_pending_surfaces_export_only_symbol_with_no_email_row(self):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO email_transactions (transaction_type, transaction_date, "
            "source_symbol, price_currency, ticker_resolution_status) "
            "VALUES ('Market Buy', ?, 'MDA', 'CAD', 'pending')",
            [date(2026, 6, 19)],
        )
        batch_id = int(
            connection.execute(
                "INSERT INTO ingestion_batches (status) VALUES ('resolved') RETURNING batch_id"
            ).fetchone()[0]
        )
        staged_file_id = int(
            connection.execute(
                "INSERT INTO staged_files (batch_id, source_type, source_path, source_hash, "
                "file_sequence, status) VALUES (?, 'export', 'export.csv', 'hash', 1, 'quarantined') "
                "RETURNING staged_file_id",
                [batch_id],
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO staged_records (staged_file_id, record_sequence, transaction_date, "
            "source_symbol, price_currency, resolution_status, raw_payload, normalized_payload) "
            "VALUES (?, 1, ?, 'XNDU', 'CAD', 'unresolved', '{}', '{}')",
            [staged_file_id, date(2026, 6, 19)],
        )

        pending = ticker_mapping.list_pending(self.db_path)
        by_symbol = {item["source_symbol"]: item for item in pending}

        self.assertIn("MDA", by_symbol)
        self.assertEqual(by_symbol["MDA"]["sources"], ["email"])
        self.assertIn("XNDU", by_symbol)
        self.assertEqual(by_symbol["XNDU"]["sources"], ["export"])
        self.assertEqual(by_symbol["XNDU"]["trade_count"], 1)
        self.assertEqual(by_symbol["XNDU"]["first_seen"], date(2026, 6, 19))

    def test_list_pending_flags_already_mapped_symbol(self):
        connection = database.get_shared_connection(self.db_path)
        ticker_id = int(
            connection.execute(
                "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
                "VALUES ('XNDU', 'TSX', 'CAD', 'Test Fund', 'etf') RETURNING ticker_id"
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO ticker_symbol_history (ticker_id, source_symbol, provider_symbol, "
            "currency, exchange, reason, mapping_source, created_by) "
            "VALUES (?, 'XNDU', 'XNDU.TO', 'CAD', 'TSX', 'resolved pending ticker', 'manual', 'user')",
            [ticker_id],
        )
        batch_id = int(
            connection.execute(
                "INSERT INTO ingestion_batches (status) VALUES ('resolved') RETURNING batch_id"
            ).fetchone()[0]
        )
        staged_file_id = int(
            connection.execute(
                "INSERT INTO staged_files (batch_id, source_type, source_path, source_hash, "
                "file_sequence, status) VALUES (?, 'export', 'export.csv', 'hash', 1, 'quarantined') "
                "RETURNING staged_file_id",
                [batch_id],
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO staged_records (staged_file_id, record_sequence, transaction_date, "
            "source_symbol, price_currency, resolution_status, raw_payload, normalized_payload) "
            "VALUES (?, 1, ?, 'XNDU', 'CAD', 'unresolved', '{}', '{}')",
            [staged_file_id, date(2026, 6, 19)],
        )

        pending = ticker_mapping.list_pending(self.db_path)
        by_symbol = {item["source_symbol"]: item for item in pending}

        self.assertTrue(by_symbol["XNDU"]["already_mapped"])
        self.assertEqual(by_symbol["XNDU"]["mapping"], {
            "canonical_symbol": "XNDU", "provider_symbol": "XNDU.TO",
            "currency": "CAD", "exchange": "TSX",
        })

    def test_list_pending_marks_unmapped_symbol_not_already_mapped(self):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO email_transactions (transaction_type, transaction_date, "
            "source_symbol, price_currency, ticker_resolution_status) "
            "VALUES ('Market Buy', ?, 'MDA', 'CAD', 'pending')",
            [date(2026, 6, 19)],
        )

        pending = ticker_mapping.list_pending(self.db_path)
        by_symbol = {item["source_symbol"]: item for item in pending}

        self.assertFalse(by_symbol["MDA"]["already_mapped"])
        self.assertIsNone(by_symbol["MDA"]["mapping"])

    def test_list_pending_excludes_email_deposit_placeholder(self):
        connection = database.get_shared_connection(self.db_path)
        connection.execute(
            "INSERT INTO email_transactions (transaction_type, transaction_date, "
            "source_symbol, ticker_resolution_status) "
            "VALUES ('Deposit', ?, 'EMAIL', 'pending')",
            [date(2024, 10, 23)],
        )

        pending = ticker_mapping.list_pending(self.db_path)

        self.assertNotIn("EMAIL", {item["source_symbol"] for item in pending})


class MergeTickersTest(unittest.TestCase):
    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)
        self._import_counter = 0

    def _insert_ticker(self, symbol, exchange="NYSEARCA", currency="USD",
                        security_type="etf", name=None):
        name = name or f"{symbol} Fund"
        ticker_id = self.connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, ?, ?, ?, ?) RETURNING ticker_id
            """,
            [symbol, exchange, currency, name, security_type],
        ).fetchone()[0]
        return int(ticker_id)

    def _insert_historical(self, ticker_id, record_date, close=100.0):
        self.connection.execute(
            """
            INSERT INTO historical_records (ticker_id, record_date, open, high, low, close, adjusted_close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1000)
            """,
            [ticker_id, record_date, close, close, close, close, close],
        )

    def _insert_transaction(self, ticker_id, transaction_date, transaction_type="Buy",
                             quantity=1, debit=100):
        self.connection.execute(
            """
            INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit)
            VALUES (?, ?, ?, ?, ?)
            """,
            [transaction_date, transaction_type, ticker_id, quantity, debit],
        )

    def _insert_email_transaction(self, ticker_id, transaction_date, account="TFSA",
                                   transaction_type="Market Buy", quantity=1):
        self.connection.execute(
            """
            INSERT INTO email_transactions (account, transaction_type, ticker_id, quantity, transaction_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            [account, transaction_type, ticker_id, quantity, transaction_date],
        )

    def _insert_import(self):
        self._import_counter += 1
        import_id = self.connection.execute(
            """
            INSERT INTO activity_imports (source_file, file_hash, status)
            VALUES (?, ?, 'succeeded') RETURNING import_id
            """,
            [f"file-{self._import_counter}", f"hash-{self._import_counter}"],
        ).fetchone()[0]
        return int(import_id)

    def _insert_activity(self, ticker_id, import_id, fingerprint):
        self.connection.execute(
            """
            INSERT INTO activities (
                transaction_date, account_id, account_type, activity_code, ticker_id,
                row_fingerprint, duplicate_ordinal, first_seen_import_id, last_seen_import_id
            ) VALUES (?, 'A1', 'TFSA', 'BUY', ?, ?, 1, ?, ?)
            """,
            [date(2026, 1, 1), ticker_id, fingerprint, import_id, import_id],
        )

    def _insert_etf_details(self, ticker_id, fund_family="Fund", aum=1000):
        self.connection.execute(
            "INSERT INTO etf_details (ticker_id, fund_family, aum) VALUES (?, ?, ?)",
            [ticker_id, fund_family, aum],
        )

    def _insert_provider_mapping(self, ticker_id, provider_symbol):
        self.connection.execute(
            """
            INSERT INTO ticker_provider_mappings (ticker_id, provider, provider_symbol, verification_status)
            VALUES (?, 'yahoo', ?, 'verified')
            """,
            [ticker_id, provider_symbol],
        )

    def _insert_history(self, ticker_id, source_symbol, effective_from=None):
        self.connection.execute(
            """
            INSERT INTO ticker_symbol_history (
                ticker_id, source_symbol, provider_symbol, currency, exchange,
                effective_from, reason, mapping_source, created_by
            ) VALUES (?, ?, ?, 'USD', 'NYSEARCA', ?, 'seed', 'automatic', 'test')
            """,
            [ticker_id, source_symbol, source_symbol, effective_from],
        )

    def test_happy_path_merges_all_tables_into_older_survivor(self):
        old_id = self._insert_ticker("SPLG", security_type="etf")
        new_id = self._insert_ticker("SPYM", security_type="stock")
        self.assertLess(old_id, new_id)

        self._insert_historical(old_id, date(2024, 1, 1))
        self._insert_historical(new_id, date(2025, 1, 1))
        self._insert_transaction(old_id, date(2024, 1, 1))
        self._insert_transaction(new_id, date(2025, 1, 1))
        self._insert_email_transaction(old_id, date(2024, 1, 1))
        self._insert_email_transaction(new_id, date(2025, 1, 1))
        import_id = self._insert_import()
        self._insert_activity(new_id, import_id, "fp-1")
        self._insert_etf_details(old_id, fund_family="Old Fund", aum=100)
        self._insert_etf_details(new_id, fund_family="New Fund", aum=200)
        self._insert_provider_mapping(old_id, "SPLG")
        self._insert_provider_mapping(new_id, "SPYM")
        self._insert_history(old_id, "SPLG", date(2024, 1, 1))
        self._insert_history(new_id, "SPYM", date(2025, 1, 1))

        report = ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

        self.assertEqual(report["surviving_ticker_id"], old_id)
        self.assertEqual(report["losing_ticker_id"], new_id)
        self.assertEqual(report["tables"]["transactions"], {"moved": 1, "collided_and_dropped": 0})
        self.assertEqual(report["tables"]["email_transactions"], {"moved": 1, "collided_and_dropped": 0})
        self.assertEqual(report["tables"]["activities"], {"moved": 1})
        self.assertEqual(report["tables"]["historical_records"], {"moved": 1, "overlap_dates_dropped": 0})

        survivor_symbol = self.connection.execute(
            "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [old_id]
        ).fetchone()[0]
        self.assertEqual(survivor_symbol, "SPYM")

        loser_symbol = self.connection.execute(
            "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [new_id]
        ).fetchone()[0]
        self.assertEqual(loser_symbol, f"SPYM_MERGED_{new_id}")

        etf_rows = self.connection.execute(
            "SELECT ticker_id, fund_family, aum FROM etf_details"
        ).fetchall()
        self.assertEqual(etf_rows, [(old_id, "Old Fund", 100)])

        history_rows = self.connection.execute(
            "SELECT ticker_id, source_symbol, effective_to, mapping_source FROM ticker_symbol_history"
        ).fetchall()
        self.assertEqual(len(history_rows), 3)
        merge_rows = [row for row in history_rows if row[3] == "merge"]
        self.assertEqual(len(merge_rows), 1)
        self.assertEqual(merge_rows[0][0], old_id)
        self.assertEqual(merge_rows[0][1], "SPYM")

        for table in ("transactions", "email_transactions", "activities", "historical_records",
                      "ticker_provider_mappings", "etf_details", "stock_details",
                      "portfolio_classifications"):
            remaining = self.connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ticker_id = ?", [new_id]
            ).fetchone()[0]
            self.assertEqual(remaining, 0, table)

    def test_historical_records_overlap_keeps_survivor_values(self):
        old_id = self._insert_ticker("SPLG")
        self._insert_ticker("SPYM")
        loser_dates = [date(2026, 1, day) for day in range(1, 6)]
        survivor_dates = [date(2026, 1, day) for day in range(3, 8)]
        for record_date in loser_dates:
            self._insert_historical(2, record_date, close=999.0)
        for record_date in survivor_dates:
            self._insert_historical(old_id, record_date, close=111.0)

        report = ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

        self.assertEqual(report["tables"]["historical_records"], {"moved": 2, "overlap_dates_dropped": 3})
        rows = self.connection.execute(
            "SELECT record_date, close FROM historical_records WHERE ticker_id = ? ORDER BY record_date",
            [old_id],
        ).fetchall()
        self.assertEqual(len(rows), 7)
        for record_date, close in rows:
            if record_date < date(2026, 1, 3):
                self.assertEqual(close, 999.0)  # loser-only dates, moved as-is
            else:
                self.assertEqual(close, 111.0)  # overlap + survivor-only: survivor's value wins/stays

    def test_etf_details_collision_keeps_survivor_row(self):
        old_id = self._insert_ticker("SPLG")
        self._insert_ticker("SPYM")
        self._insert_etf_details(old_id, fund_family="Old Fund", aum=100)
        self._insert_etf_details(2, fund_family="New Fund", aum=999)

        ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

        rows = self.connection.execute("SELECT ticker_id, fund_family, aum FROM etf_details").fetchall()
        self.assertEqual(rows, [(old_id, "Old Fund", 100)])

    def test_transactions_collision_aborts_and_rolls_back(self):
        self._insert_ticker("SPLG")
        new_id = self._insert_ticker("SPYM")
        shared_date = date(2026, 1, 1)
        self._insert_transaction(1, shared_date, transaction_type="Buy", quantity=1, debit=100)
        self._insert_transaction(new_id, shared_date, transaction_type="Buy", quantity=1, debit=100)
        self._insert_historical(new_id, date(2025, 1, 1))

        with self.assertRaises(ValueError):
            ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE ticker_id = ?", [new_id]
            ).fetchone()[0], 1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM historical_records WHERE ticker_id = ?", [new_id]
            ).fetchone()[0], 1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [1]
            ).fetchone()[0], "SPLG",
        )

    def test_email_transactions_collision_aborts(self):
        self._insert_ticker("SPLG")
        new_id = self._insert_ticker("SPYM")
        shared_date = date(2026, 1, 1)
        self._insert_email_transaction(1, shared_date, account="TFSA",
                                        transaction_type="Market Buy", quantity=1)
        self._insert_email_transaction(new_id, shared_date, account="TFSA",
                                        transaction_type="Market Buy", quantity=1)

        with self.assertRaises(ValueError):
            ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM email_transactions WHERE ticker_id = ?", [new_id]
            ).fetchone()[0], 1,
        )

    def test_dry_run_makes_no_mutations(self):
        self._insert_ticker("SPLG")
        new_id = self._insert_ticker("SPYM")
        self._insert_historical(1, date(2024, 1, 1))
        self._insert_historical(new_id, date(2025, 1, 1))
        self._insert_transaction(new_id, date(2025, 1, 1))
        self._insert_etf_details(new_id, fund_family="New Fund", aum=200)

        tables = ["tickers", "transactions", "email_transactions", "activities",
                  "staged_records", "historical_records", "ticker_provider_mappings",
                  "stock_details", "etf_details", "portfolio_classifications",
                  "ticker_symbol_history"]

        def _snapshot():
            return {table: self.connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}

        before = _snapshot()
        report = ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", dry_run=True, db_path=self.db_path)
        after = _snapshot()

        self.assertEqual(before, after)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["tables"]["transactions"], {"moved": 1, "collided_and_dropped": 0})
        self.assertEqual(report["tables"]["historical_records"], {"moved": 1, "overlap_dates_dropped": 0})

    def test_merge_raises_for_unknown_symbol(self):
        self._insert_ticker("SPLG")
        with self.assertRaises(ValueError):
            ticker_mapping.merge_tickers("SPLG", "NOPE", "USD", db_path=self.db_path)

    def test_merge_raises_for_ambiguous_symbol_needing_exchange(self):
        self._insert_ticker("SPLG", exchange="NYSEARCA")
        self._insert_ticker("SPLG", exchange="ARCX")
        self._insert_ticker("SPYM")
        with self.assertRaises(ValueError):
            ticker_mapping.merge_tickers("SPLG", "SPYM", "USD", db_path=self.db_path)

    def test_merge_raises_when_symbols_resolve_to_same_ticker(self):
        self._insert_ticker("SPLG")
        with self.assertRaises(ValueError):
            ticker_mapping.merge_tickers("SPLG", "SPLG", "USD", db_path=self.db_path)

    def test_cli_merge_without_yes_previews_and_exits_nonzero(self):
        self._insert_ticker("SPLG")
        self._insert_ticker("SPYM")

        exit_code = ticker_mapping.main([
            "merge", "--old-symbol", "SPLG", "--new-symbol", "SPYM",
            "--currency", "USD", "--database", str(self.db_path),
        ])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [1]
            ).fetchone()[0], "SPLG",
        )

    def test_cli_merge_with_yes_applies(self):
        self._insert_ticker("SPLG")
        self._insert_ticker("SPYM")

        exit_code = ticker_mapping.main([
            "merge", "--old-symbol", "SPLG", "--new-symbol", "SPYM",
            "--currency", "USD", "--yes", "--database", str(self.db_path),
        ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [1]
            ).fetchone()[0], "SPYM",
        )


class RenameTickerSymbolTest(unittest.TestCase):
    """Regression coverage for `_TICKER_REFERENCING_TABLES` -- it previously
    omitted `dividend_events`, `earnings_events`, `financial_snapshots`,
    `position_ledger`, `position_snapshots`, and `security_status`, all of
    which have a real `FOREIGN KEY (ticker_id) REFERENCES tickers(ticker_id)`
    (confirmed against `duckdb_constraints()`). Renaming a ticker that had
    rows in any of those tables raised a `ConstraintException` partway
    through the rename -- after the placeholder ticker and the *listed*
    tables' rows had already moved, since `_rename_ticker_symbol` runs
    outside a transaction (see its docstring)."""

    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _insert_ticker(self, symbol, exchange="TORONTO", currency="CAD", name=None):
        ticker_id = self.connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, ?, ?, ?, 'etf') RETURNING ticker_id
            """,
            [symbol, exchange, currency, name or f"{symbol} Fund"],
        ).fetchone()[0]
        return int(ticker_id)

    def test_rename_moves_a_previously_omitted_table_without_raising(self):
        ticker_id = self._insert_ticker("NVDU")
        self.connection.execute(
            """
            INSERT INTO dividend_events (ticker_id, ex_dividend_date, declared_amount)
            VALUES (?, ?, ?)
            """,
            [ticker_id, date(2026, 6, 1), 0.5],
        )
        self.connection.execute(
            """
            INSERT INTO security_status (ticker_id, declared_status, declared_at, declared_by)
            VALUES (?, 'wishlist', ?, 'test')
            """,
            [ticker_id, date(2026, 1, 1)],
        )

        ticker_mapping._rename_ticker_symbol(self.connection, ticker_id, "NVDU_US")

        self.assertEqual(
            self.connection.execute(
                "SELECT ticker_symbol FROM tickers WHERE ticker_id = ?", [ticker_id]
            ).fetchone()[0],
            "NVDU_US",
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT declared_amount FROM dividend_events WHERE ticker_id = ?", [ticker_id]
            ).fetchone()[0],
            0.5,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT declared_status FROM security_status WHERE ticker_id = ?", [ticker_id]
            ).fetchone()[0],
            "wishlist",
        )
        # No leftover placeholder row from the park-then-restore dance.
        leftover = self.connection.execute(
            "SELECT COUNT(*) FROM tickers WHERE ticker_symbol LIKE '__MERGE_HOLD%'"
        ).fetchone()[0]
        self.assertEqual(leftover, 0)


class FormatTextTest(unittest.TestCase):
    def test_format_text_renders_list_of_dicts_as_table(self):
        rows = [
            {"source_symbol": "MDA", "detected_currency": "CAD"},
            {"source_symbol": "XNDU", "detected_currency": None},
        ]
        text = ticker_mapping.format_text(rows)
        self.assertEqual(
            text,
            "SOURCE_SYMBOL  DETECTED_CURRENCY\n"
            "-------------  -----------------\n"
            "MDA            CAD              \n"
            "XNDU                            ",
        )

    def test_format_text_renders_single_dict_as_key_value_lines(self):
        record = {"updated": 2, "status": "verified"}
        text = ticker_mapping.format_text(record)
        self.assertEqual(text, "updated : 2\nstatus  : verified")

    def test_format_text_renders_empty_list_as_no_results(self):
        self.assertEqual(ticker_mapping.format_text([]), "No results.")


if __name__ == "__main__":
    unittest.main()
