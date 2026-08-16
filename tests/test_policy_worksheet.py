"""Tests for `src/workspace/policy_worksheet.py` (Phase 11's deterministic
policy layer).

Portfolio state comes from a synthetic DuckDB fixture (mirrors
`tests/test_analytics.py`'s `AnalyticsAllocationTest` fixture builders); the
run-workspace side (mirrors `tests/test_run_workspace.py`) is a throwaway
temp directory so nothing here touches the real `workspace/runs/` or the
real pipeline database.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import database  # noqa: E402
import workspace  # noqa: E402
from workspace import evidence, policy_worksheet, run  # noqa: E402

SYNTHETIC_REQUEST = {
    "schema_version": "1.0",
    "mode": "portfolio_check",
    "subject": {"type": "security", "identifiers": {"ticker": "SYNTH"}},
    "request": {"question": "Synthetic policy-worksheet fixture.", "context": "Not real market data."},
}


class PolicyWorksheetTestCase(unittest.TestCase):
    """Wires up both a synthetic portfolio DB and a throwaway run directory."""

    def setUp(self) -> None:
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        base = Path(self.temp_dir.name)

        self.db_path = base / "portfolio.duckdb"
        database.initialize_database(self.db_path)

        runs_root = base / "runs"
        archive_root = base / "archive"
        runs_root.mkdir()
        archive_root.mkdir()
        patcher = patch.multiple(
            config, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=runs_root, WORKSPACE_ARCHIVE_FOLDER=archive_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        import yaml

        request_path = base / "request.yaml"
        request_path.write_text(yaml.safe_dump(SYNTHETIC_REQUEST), encoding="utf-8")
        self.run_id, self.run_dir = run.create_run(request_path)

    # --- DB fixture builders (mirrors tests/test_analytics.py) ---

    def _ticker(self, symbol, exchange="NASDAQ", currency="USD", name=None, security_type="stock"):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, ?, ?, ?, ?) RETURNING ticker_id
            """,
            [symbol, exchange, currency, name or symbol, security_type],
        ).fetchone()[0]

    def _buy(self, ticker_id, quantity, debit, transaction_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO transactions (
                transaction_date, transaction_type, ticker_id, quantity, execution_date, debit, credit, fx_rate
            ) VALUES (?, 'BUY', ?, ?, ?, ?, NULL, NULL)
            """,
            [transaction_date, ticker_id, Decimal(str(quantity)), transaction_date, Decimal(str(debit))],
        )

    def _price(self, ticker_id, close, record_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, record_date, close, close, close, close, close, 100],
        )

    def _classify(self, ticker_id, primary_group, secondary_tags=None):
        database.get_shared_connection(self.db_path).execute(
            """
            INSERT INTO portfolio_classifications (
                ticker_id, primary_group, secondary_tags, review_needed, generated_at
            ) VALUES (?, ?, ?, FALSE, CURRENT_TIMESTAMP)
            """,
            [ticker_id, primary_group, json.dumps(secondary_tags or [])],
        )


class SingleNameCapTest(PolicyWorksheetTestCase):
    def test_position_well_under_cap_passes(self):
        # Portfolio: AAPL 5%, a large diversified remainder.
        aapl = self._ticker("AAPL")
        core = self._ticker("XEQT", exchange="TSX", currency="CAD", security_type="etf")
        self._buy(aapl, 5, 500)
        self._buy(core, 95, 9500, transaction_date=date(2025, 1, 3))
        self._price(aapl, 100)
        self._price(core, 100, record_date=date(2025, 1, 3))
        self._classify(aapl, "Growth")
        self._classify(core, "Core")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "single_name_cap")
        self.assertEqual(check["result"], "pass")
        self.assertAlmostEqual(worksheet["current_weight_pct"], 5.0)

    def test_position_over_cap_fails(self):
        # Portfolio: AAPL 20% (over the 10% single-name cap), rest a stock.
        aapl = self._ticker("AAPL")
        other = self._ticker("MSFT")
        self._buy(aapl, 20, 2000)
        self._buy(other, 80, 8000, transaction_date=date(2025, 1, 3))
        self._price(aapl, 100)
        self._price(other, 100, record_date=date(2025, 1, 3))
        self._classify(aapl, "Growth")
        self._classify(other, "Quality")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "single_name_cap")
        self.assertEqual(check["result"], "fail")
        self.assertAlmostEqual(worksheet["current_weight_pct"], 20.0)

    def test_core_etf_is_exempt_even_over_nominal_cap(self):
        core = self._ticker("XEQT", exchange="TSX", currency="CAD", security_type="etf")
        other = self._ticker("AAPL")
        self._buy(core, 80, 8000)
        self._buy(other, 20, 2000, transaction_date=date(2025, 1, 3))
        self._price(core, 100)
        self._price(other, 100, record_date=date(2025, 1, 3))
        self._classify(core, "Core")
        self._classify(other, "Growth")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="XEQT", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "single_name_cap")
        self.assertEqual(check["result"], "pass")
        self.assertIn("exempt", check["detail"])


class GroupAllocationTargetTest(PolicyWorksheetTestCase):
    def test_group_under_max_passes(self):
        # Growth's max_percent is 15 in policy_v1_1.yaml; keep it well under.
        growth = self._ticker("PLTR")
        core = self._ticker("XEQT", exchange="TSX", currency="CAD", security_type="etf")
        self._buy(growth, 5, 500)
        self._buy(core, 95, 9500, transaction_date=date(2025, 1, 3))
        self._price(growth, 100)
        self._price(core, 100, record_date=date(2025, 1, 3))
        self._classify(growth, "Growth")
        self._classify(core, "Core")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="PLTR", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "group_allocation_target")
        self.assertEqual(check["result"], "pass")

    def test_group_over_max_fails(self):
        # Growth's max_percent is 15 in policy_v1_1.yaml; push the group to 100%.
        growth = self._ticker("PLTR")
        self._buy(growth, 10, 1000)
        self._price(growth, 100)
        self._classify(growth, "Growth")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="PLTR", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "group_allocation_target")
        self.assertEqual(check["result"], "fail")

    def test_unclassified_security_reports_unavailable_not_invented(self):
        unclassified = self._ticker("ZZZZ")
        self._buy(unclassified, 10, 1000)
        self._price(unclassified, 100)

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="ZZZZ", db_path=self.db_path)

        check = next(c for c in worksheet["policy_checks"] if c["name"] == "group_allocation_target")
        self.assertEqual(check["result"], "unavailable")


class InformationalExposureTest(PolicyWorksheetTestCase):
    def test_sector_and_currency_exposure_are_informational_not_pass_fail(self):
        stock = self._ticker("AAPL", currency="USD")
        self._buy(stock, 10, 1000)
        self._price(stock, 100)
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO stock_details (ticker_id, sector, industry) VALUES (?, 'Technology', 'Hardware')",
            [stock],
        )
        self._classify(stock, "Growth")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)

        # No policy_check is ever named after a sector or currency -- the
        # only two checks are single_name_cap and group_allocation_target.
        check_names = {c["name"] for c in worksheet["policy_checks"]}
        self.assertEqual(check_names, {"single_name_cap", "group_allocation_target"})
        self.assertIn("Technology", worksheet["portfolio_context"]["by_sector"])
        self.assertIn("USD", worksheet["portfolio_context"]["by_currency"])


class PriceAndMarketContextTest(PolicyWorksheetTestCase):
    """`price_and_market_context` (Phase 11 extension): a trailing-365-day
    close range, the Portfolio Manager's only source for an `order_guidance`
    price besides the thesis. Record dates are relative to `date.today()`,
    not hardcoded, so this suite does not go stale as the trailing window
    ages past a fixed date."""

    def test_week52_range_reflects_trailing_year_only(self):
        today = date.today()
        aapl = self._ticker("AAPL")
        self._buy(aapl, 10, 1000)
        self._price(aapl, 80, record_date=today - timedelta(days=400))  # outside the window
        self._price(aapl, 60, record_date=today - timedelta(days=200))  # low, inside
        self._price(aapl, 90, record_date=today - timedelta(days=100))  # high, inside
        self._price(aapl, 75, record_date=today)  # latest

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)

        context = worksheet["price_and_market_context"]
        self.assertEqual(context["latest_close"], 75.0)
        self.assertEqual(context["week52_low"], 60.0)
        self.assertEqual(context["week52_high"], 90.0)

    def test_no_price_history_is_all_none_not_a_crash(self):
        unclassified = self._ticker("ZZZZ")
        self._buy(unclassified, 10, 1000)
        # No _price() call -- no historical_records row for this ticker.

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="ZZZZ", db_path=self.db_path)

        self.assertEqual(
            worksheet["price_and_market_context"],
            {"latest_close": None, "week52_low": None, "week52_high": None},
        )


class NotCurrentlyHeldTest(PolicyWorksheetTestCase):
    def test_candidate_not_yet_held_is_scored_at_zero_weight(self):
        self._ticker("NVDA")  # on file, classified, but never bought
        held = self._ticker("AAPL")
        self._buy(held, 10, 1000)
        self._price(held, 100)
        self._classify(held, "Quality")

        worksheet = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="NVDA", db_path=self.db_path)

        self.assertEqual(worksheet["current_weight_pct"], 0.0)
        self.assertFalse(worksheet["subject"]["currently_held"])
        single_name = next(c for c in worksheet["policy_checks"] if c["name"] == "single_name_cap")
        self.assertEqual(single_name["result"], "pass")


class BuildForRunTest(PolicyWorksheetTestCase):
    def test_writes_artifact_registers_evidence_and_audit_event(self):
        aapl = self._ticker("AAPL")
        self._buy(aapl, 5, 500)
        self._price(aapl, 100)
        self._classify(aapl, "Growth")

        worksheet, worksheet_path, worksheet_hash = policy_worksheet.build_policy_worksheet_for_run(
            self.run_dir, run_id=self.run_id, ticker="AAPL", db_path=self.db_path,
        )

        self.assertTrue(worksheet_path.is_file())
        self.assertTrue(worksheet_hash.startswith("sha256:"))
        on_disk = json.loads(worksheet_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema"], "portfolio-policy-worksheet.v1")

        records = list(evidence.read_records(self.run_dir))
        self.assertTrue(any(r["evidence_type"] == "policy_worksheet" for r in records))

        audit_lines = (self.run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(json.loads(line)["event"] == "policy_worksheet_built" for line in audit_lines))

    def test_rebuilding_from_identical_portfolio_state_is_stable(self):
        aapl = self._ticker("AAPL")
        self._buy(aapl, 5, 500)
        self._price(aapl, 100)
        self._classify(aapl, "Growth")

        first = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)
        second = policy_worksheet.build_policy_worksheet(run_id=self.run_id, ticker="AAPL", db_path=self.db_path)

        self.assertEqual(first["policy_checks"], second["policy_checks"])
        self.assertEqual(first["current_weight_pct"], second["current_weight_pct"])


if __name__ == "__main__":
    unittest.main()
