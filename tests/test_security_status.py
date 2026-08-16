"""Tests for the security-status feature: `analytics.resolve_security_status`,
`database_command.set_security_status`, and the shared
`.claude/skills/security-status/scripts/security_status_cli.py` skill.

Covers the one design point worth defending -- ownership (derived from
`position_snapshots.quantity > 0`) always wins over any declared status in
`security_status`, and an undeclared ticker is a normal state, never a gap.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "security-status" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import analytics  # noqa: E402
import database  # noqa: E402
import position_engine  # noqa: E402
from database_command import SECURITY_STATUS_VALUES, set_security_status  # noqa: E402

import security_status_cli as sscli  # noqa: E402

TODAY = date(2026, 8, 14)


class FixtureDatabaseTest(unittest.TestCase):
    """One seeded DuckDB per test, mirroring
    `test_investment_analyst_resources.FixtureDatabaseTest`'s pattern."""

    def setUp(self):
        database.close_connection()
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(database.close_connection)
        self.db_path = Path(self.temp_dir.name) / "portfolio.duckdb"
        database.initialize_database(self.db_path)
        self.connection = database.get_shared_connection(self.db_path)

    def _seed_ticker(self, symbol="OUST", *, owned=False, quantity=10) -> int:
        ticker_id = self.connection.execute(
            """INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
               VALUES (?, 'NYSE', 'USD', ?, 'stock') RETURNING ticker_id""",
            [symbol, f"{symbol} Inc."],
        ).fetchone()[0]
        if owned:
            self.connection.execute(
                "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
                "VALUES (?, 'BUY', ?, ?, ?)",
                [date(2025, 1, 2), ticker_id, quantity, 1000],
            )
            position_engine.recompute_positions(self.connection)
        return ticker_id

    def _declare(self, ticker_id: int, status: str, *, rationale=None, declared_by="cli") -> None:
        self.connection.execute(
            """INSERT INTO security_status (ticker_id, declared_status, rationale, declared_at, declared_by)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)""",
            [ticker_id, status, rationale, declared_by],
        )


# --- analytics.resolve_security_status: owned > declared > unknown ---------


class ResolveSecurityStatusTest(FixtureDatabaseTest):
    def test_owned_wins_over_any_declaration(self):
        ticker_id = self._seed_ticker("OUST", owned=True)
        self._declare(ticker_id, "wishlist", rationale="stale, should be overridden")

        result = analytics.resolve_security_status("OUST", self.db_path)

        self.assertTrue(result["resolved"])
        self.assertEqual(result["status"], "owned")
        # The declaration is still surfaced (a human can see the stale
        # wishlist note), it just does not win.
        self.assertEqual(result["declaration"]["declared_status"], "wishlist")

    def test_unowned_with_a_declaration_reports_the_declared_status(self):
        ticker_id = self._seed_ticker("OUST", owned=False)
        self._declare(ticker_id, "wishlist", rationale="watching for entry")

        result = analytics.resolve_security_status("OUST", self.db_path)

        self.assertTrue(result["resolved"])
        self.assertEqual(result["status"], "wishlist")
        self.assertEqual(result["declaration"]["rationale"], "watching for entry")

    def test_unowned_with_no_declaration_is_unknown(self):
        self._seed_ticker("OUST", owned=False)

        result = analytics.resolve_security_status("OUST", self.db_path)

        self.assertTrue(result["resolved"])
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["declaration"])

    def test_unresolvable_ticker_is_unknown_not_an_error(self):
        result = analytics.resolve_security_status("ZZZZ", self.db_path)

        self.assertFalse(result["resolved"])
        self.assertEqual(result["status"], "unknown")

    def test_a_bought_wishlist_ticker_becomes_owned_with_no_write(self):
        """The design point: buying a declared wishlist ticker flips the
        resolved status with zero writes to `security_status`."""
        ticker_id = self._seed_ticker("OUST", owned=False)
        self._declare(ticker_id, "wishlist")
        self.assertEqual(analytics.resolve_security_status("OUST", self.db_path)["status"], "wishlist")

        self.connection.execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, debit) "
            "VALUES (?, 'BUY', ?, 5, 500)",
            [date(2025, 6, 1), ticker_id],
        )
        position_engine.recompute_positions(self.connection)

        result = analytics.resolve_security_status("OUST", self.db_path)
        self.assertEqual(result["status"], "owned")
        # The stale declaration is untouched -- nobody wrote to the table.
        declared = self.connection.execute(
            "SELECT declared_status FROM security_status WHERE ticker_id = ?", [ticker_id]
        ).fetchone()
        self.assertEqual(declared[0], "wishlist")


# --- database_command.set_security_status: the sole write path -------------


class SetSecurityStatusTest(FixtureDatabaseTest):
    def test_declares_wishlist_on_an_existing_ticker(self):
        self._seed_ticker("OUST", owned=False)

        result = set_security_status("OUST", "wishlist", db_path=self.db_path, rationale="LIDAR play")

        self.assertEqual(result["declared_status"], "wishlist")
        row = self.connection.execute(
            "SELECT declared_status, rationale, declared_by FROM security_status s "
            "JOIN tickers t ON t.ticker_id = s.ticker_id WHERE t.ticker_symbol = 'OUST'"
        ).fetchone()
        self.assertEqual(row, ("wishlist", "LIDAR play", "cli"))

    def test_clear_removes_an_existing_declaration(self):
        ticker_id = self._seed_ticker("OUST", owned=False)
        self._declare(ticker_id, "wishlist")

        result = set_security_status("OUST", "clear", db_path=self.db_path)

        self.assertIsNone(result["declared_status"])
        count = self.connection.execute(
            "SELECT COUNT(*) FROM security_status WHERE ticker_id = ?", [ticker_id]
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_rewriting_a_declaration_upserts_rather_than_duplicates(self):
        self._seed_ticker("OUST", owned=False)
        set_security_status("OUST", "wishlist", db_path=self.db_path)
        set_security_status("OUST", "avoid", db_path=self.db_path, rationale="changed my mind")

        rows = self.connection.execute(
            "SELECT declared_status FROM security_status s "
            "JOIN tickers t ON t.ticker_id = s.ticker_id WHERE t.ticker_symbol = 'OUST'"
        ).fetchall()
        self.assertEqual(rows, [("avoid",)])

    def test_invalid_status_is_rejected(self):
        self._seed_ticker("OUST", owned=False)
        with self.assertRaises(ValueError):
            set_security_status("OUST", "owned", db_path=self.db_path)

    def test_ambiguous_symbol_across_exchanges_is_rejected(self):
        self.connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES ('ABC', 'TSX', 'CAD', 'ABC Canada', 'stock')"
        )
        self.connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES ('ABC', 'NYSE', 'USD', 'ABC United States', 'stock')"
        )
        with self.assertRaises(ValueError):
            set_security_status("ABC", "wishlist", db_path=self.db_path)


# --- security_status_cli: pure resolution + trace grading -------------------


class ResolveStatusPureFunctionTest(unittest.TestCase):
    def test_owned_wins_regardless_of_declaration(self):
        declaration = {"declared_status": "avoid"}
        self.assertEqual(sscli.resolve_status(Decimal("1"), declaration), "owned")

    def test_declared_status_used_when_not_owned(self):
        declaration = {"declared_status": "retired"}
        self.assertEqual(sscli.resolve_status(Decimal("0"), declaration), "retired")

    def test_unknown_when_neither(self):
        self.assertEqual(sscli.resolve_status(Decimal("0"), None), "unknown")


class BuildTraceGradingTest(unittest.TestCase):
    def test_undeclared_owned_ticker_is_not_applicable_never_missing(self):
        """The requirement this whole feature was built to satisfy: a ticker
        with nothing declared is a normal state, not a gap."""
        trace = sscli.build_trace("OUST", True, None)
        declaration_domain = next(d for d in trace.domains if d.name == "declaration")

        self.assertEqual(declaration_domain.missing, [])
        self.assertEqual(declaration_domain.not_applicable, ["declared_status"])
        self.assertEqual(trace.completeness_pct, 100.0)

    def test_declared_ticker_grades_ok(self):
        trace = sscli.build_trace("OUST", True, {"declared_status": "wishlist"})
        declaration_domain = next(d for d in trace.domains if d.name == "declaration")

        self.assertEqual(declaration_domain.ok, ["declared_status"])
        self.assertEqual(declaration_domain.missing, [])

    def test_unresolved_ticker_grades_nothing(self):
        trace = sscli.build_trace("ZZZZ", False, None)
        self.assertEqual(trace.domains, [])


class BuildResultShapeTest(unittest.TestCase):
    def test_unresolved_ticker_reports_one_gap(self):
        result = sscli.build_result("ZZZZ", None, Decimal("0"), None, "investment-analyst", TODAY)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(result["gaps"]), 1)

    def test_resolved_owned_ticker(self):
        identity = {"ticker_id": 1, "security_name": "Ouster, Inc."}
        result = sscli.build_result(
            "OUST", identity, Decimal("5"), {"declared_status": "wishlist"},
            "investment-portfolio-manager", TODAY,
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["status"], "owned")
        self.assertEqual(result["actor"], "investment-portfolio-manager")
        self.assertEqual(result["gaps"], [])


# --- CLI integration: two actors, two attributable evidence rows -----------


class SharedSkillDualActorTest(FixtureDatabaseTest):
    """Two agents invoking this one shared skill for the same ticker in the
    same run must each leave a distinct, attributable audit trail -- the
    caller-stamping design confirmed instead of two separate skill copies."""

    def _make_run(self, run_id="test-security-status-run"):
        import config as config_module
        from workspace import run as run_module

        base = Path(self.temp_dir.name) / "ws"
        runs_root = base / "runs"
        runs_root.mkdir(parents=True)
        patcher = unittest.mock.patch.multiple(
            config_module, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        resolved, directory, _created = run_module.ensure_run(
            run_id, mode="status_check", subject_type="security", ticker="OUST"
        )
        return resolved, directory

    def test_two_actors_produce_two_evidence_rows_and_audit_events(self):
        from workspace import audit as audit_module, evidence as evidence_module

        ticker_id = self._seed_ticker("OUST", owned=True)
        run_id, run_dir = self._make_run()

        database.close_connection()
        connection = sscli.price_reader.connect_read_only(self.db_path)
        try:
            for actor in ("investment-analyst", "investment-portfolio-manager"):
                sscli.process_ticker(
                    "OUST", connection, actor,
                    today=TODAY, no_trace=False, output=None, pretty=False,
                    trace_log_path=Path(self.temp_dir.name) / "trace.txt",
                    run_dir=run_dir, run_id=run_id,
                    workspace_status="attached", skip_reason=None,
                )
        finally:
            connection.close()

        records = evidence_module.read_records(run_dir)
        self.assertEqual(len(records), 2)
        methods = {record["collection_method"] for record in records}
        self.assertEqual(
            methods, {"security-status:investment-analyst", "security-status:investment-portfolio-manager"}
        )
        artifact_paths = {record["artifact_path"] for record in records}
        self.assertEqual(len(artifact_paths), 2)

        events = [e for e in audit_module.read_events(run_dir) if e["event"] == "security_status_read"]
        self.assertEqual(len(events), 2)
        actors = {event["actor"] for event in events}
        self.assertEqual(actors, {"investment-analyst", "investment-portfolio-manager"})


if __name__ == "__main__":
    unittest.main()
