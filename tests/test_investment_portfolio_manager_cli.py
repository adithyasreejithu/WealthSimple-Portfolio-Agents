"""Tests for Phase 11's three new `run` CLI stages
(`build-policy-context`/`check-decision`/`save-decision`) and their
underlying `src/workspace/policy_worksheet.py` / `decision_validation.py`
functions, driven through `cli.main()` the way
`test_investment_analyst_cli.py`'s `CliWiringTest` drives Phase 4's.

`thesis_ref` is a stub file here, not a real `investment-thesis.v1` artifact
-- `decision_validation.check_thesis_ref` only checks the cited bytes are
unmutated (path + sha256), it never parses or validates the thesis's own
schema, so a stub proves the wiring without paying for a full Phase 3/4
pipeline run. `test_decision_validation.py` covers the ref-checking logic
itself in isolation.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import database  # noqa: E402
from workspace import cli, evidence as evidence_module, run as run_module, validation as validation_module  # noqa: E402
from workspace.models import Request, RequestBody, Subject, SubjectIdentifiers  # noqa: E402


class CliWiringTest(unittest.TestCase):
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

        request = Request(
            mode="portfolio_check",
            subject=Subject(type="security", identifiers=SubjectIdentifiers(ticker="AAPL")),
            request=RequestBody(question="Should this position be sized up?"),
        )
        self.run_id, self.run_dir = run_module.create_from_request(request)

    def _ticker(self, symbol, exchange="NASDAQ", currency="USD", security_type="stock"):
        connection = database.get_shared_connection(self.db_path)
        return connection.execute(
            "INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type) "
            "VALUES (?, ?, ?, ?, ?) RETURNING ticker_id",
            [symbol, exchange, currency, symbol, security_type],
        ).fetchone()[0]

    def _buy(self, ticker_id, quantity, debit, transaction_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO transactions (transaction_date, transaction_type, ticker_id, quantity, "
            "execution_date, debit, credit, fx_rate) VALUES (?, 'BUY', ?, ?, ?, ?, NULL, NULL)",
            [transaction_date, ticker_id, Decimal(str(quantity)), transaction_date, Decimal(str(debit))],
        )

    def _price(self, ticker_id, close, record_date=date(2025, 1, 2)):
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO historical_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [ticker_id, record_date, close, close, close, close, close, 100],
        )

    def _classify(self, ticker_id, primary_group):
        database.get_shared_connection(self.db_path).execute(
            "INSERT INTO portfolio_classifications (ticker_id, primary_group, secondary_tags, "
            "review_needed, generated_at) VALUES (?, ?, '[]', FALSE, CURRENT_TIMESTAMP)",
            [ticker_id, primary_group],
        )

    def _run_cli(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, json.loads(buf.getvalue())

    # `order_guidance` (required for Buy/Add/Trim/Sell) cites this exact
    # value from the stub thesis -- a fixed number rather than anything
    # date-derived (the policy worksheet's own `price_and_market_context` is
    # a trailing-365-day window and would go stale relative to "today" as
    # this test suite ages, unlike a citation into the thesis stub below).
    STUB_FAIR_VALUE = 100.0

    def _write_stub_thesis(self) -> tuple[str, str]:
        """A stub `agent_outputs/` artifact standing in for a real
        `investment-thesis.v1` -- see module docstring. Carries just enough
        of `valuation`/`scenarios`' real shape for an `order_guidance`
        citation to resolve against (`decision_validation.
        check_order_guidance_prices_are_grounded` reads this file)."""
        path = self.run_dir / "agent_outputs" / "AAPL-thesis.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "schema": "investment-thesis.v1",
                "stub": True,
                "scenarios": {"base": {"fair_value_per_share": self.STUB_FAIR_VALUE}},
            }),
            encoding="utf-8",
        )
        record = evidence_module.register(
            self.run_dir, run_id=self.run_id, evidence_type="investment_thesis",
            source_name="investment_analyst", status="available", artifact=path,
        )
        return path.relative_to(self.run_dir).as_posix(), "sha256:" + record.content_hash

    def _order_guidance(self) -> dict:
        price_level = {
            "price": self.STUB_FAIR_VALUE, "source": "thesis.scenarios.base.fair_value_per_share",
            "rationale": "Synthetic CLI-wiring test order guidance.",
        }
        return {"order_type": "Limit", "reference_price": price_level, "limit_price": price_level}

    def _decision_draft(self, *, proposed_action, policy_result, thesis_rel, thesis_hash) -> dict:
        requires_order_guidance = proposed_action in ("Buy", "Add", "Trim", "Sell")
        return {
            "proposal_id": "dp_placeholder",
            "run_id": self.run_id,
            "generated_at": "2026-08-10T15:00:00Z",
            "subject": {"type": "security", "identifiers": {"ticker": "AAPL"}},
            "proposed_action": proposed_action,
            "sizing": {
                "current_weight_pct": policy_result["current_weight_pct"],
                "proposed_weight_pct": policy_result["current_weight_pct"],
                "rationale": "Synthetic CLI-wiring test decision.",
            },
            "order_guidance": self._order_guidance() if requires_order_guidance else None,
            "confidence": "Medium",
            "summary": "Synthetic decision citing the stub thesis and the built policy worksheet.",
            "thesis_ref": {"path": thesis_rel, "hash": thesis_hash},
            "policy_worksheet_ref": {
                "path": policy_result["policy_worksheet_path"], "hash": policy_result["policy_worksheet_hash"],
            },
            "policy_version": "placeholder",
            "supporting_evidence_ids": [],
            "uncertainties": [],
            "policy_checks": policy_result["policy_checks"],
        }

    def test_full_sequence_via_cli_main_with_clean_portfolio_state(self):
        aapl = self._ticker("AAPL")
        core = self._ticker("XEQT", exchange="TSX", currency="CAD", security_type="etf")
        self._buy(aapl, 5, 500)
        self._buy(core, 95, 9500, transaction_date=date(2025, 1, 3))
        self._price(aapl, 100)
        self._price(core, 100, record_date=date(2025, 1, 3))
        self._classify(aapl, "Growth")
        self._classify(core, "Core")

        code, policy_result = self._run_cli([
            "build-policy-context", "--run-id", self.run_id, "--ticker", "AAPL", "--db-path", str(self.db_path),
        ])
        self.assertEqual(code, 0, policy_result)
        self.assertTrue(all(c["result"] == "pass" for c in policy_result["policy_checks"]))

        thesis_rel, thesis_hash = self._write_stub_thesis()
        draft = self._decision_draft(
            proposed_action="Add", policy_result=policy_result, thesis_rel=thesis_rel, thesis_hash=thesis_hash,
        )
        draft_path = self.run_dir / "tmp" / "AAPL-decision-draft.json"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")

        code, check_result = self._run_cli([
            "check-decision", "--run-id", self.run_id, "--path", "tmp/AAPL-decision-draft.json",
        ])
        self.assertEqual(code, 0, check_result)
        self.assertTrue(check_result["ok"])

        code, save_result = self._run_cli([
            "save-decision", "--run-id", self.run_id, "--path", "tmp/AAPL-decision-draft.json", "--ticker", "AAPL",
        ])
        self.assertEqual(code, 0, save_result)
        self.assertTrue((self.run_dir / save_result["artifact_path"]).is_file())
        self.assertIn("final", save_result["artifact_path"])

        run_result = validation_module.validate_run(self.run_dir)
        decision_errors = [e for e in run_result["errors"] if "final" in e]
        self.assertEqual(decision_errors, [])

    def test_save_decision_rejects_buy_against_a_single_name_breach(self):
        aapl = self._ticker("AAPL")
        other = self._ticker("MSFT")
        self._buy(aapl, 20, 2000)
        self._buy(other, 80, 8000, transaction_date=date(2025, 1, 3))
        self._price(aapl, 100)
        self._price(other, 100, record_date=date(2025, 1, 3))
        self._classify(aapl, "Growth")
        self._classify(other, "Quality")

        code, policy_result = self._run_cli([
            "build-policy-context", "--run-id", self.run_id, "--ticker", "AAPL", "--db-path", str(self.db_path),
        ])
        self.assertEqual(code, 0, policy_result)
        single_name = next(c for c in policy_result["policy_checks"] if c["name"] == "single_name_cap")
        self.assertEqual(single_name["result"], "fail")

        thesis_rel, thesis_hash = self._write_stub_thesis()

        # Buy against a breaching single-name cap is rejected...
        buy_draft = self._decision_draft(
            proposed_action="Buy", policy_result=policy_result, thesis_rel=thesis_rel, thesis_hash=thesis_hash,
        )
        buy_path = self.run_dir / "tmp" / "AAPL-buy-draft.json"
        buy_path.write_text(json.dumps(buy_draft), encoding="utf-8")
        code, buy_result = self._run_cli([
            "check-decision", "--run-id", self.run_id, "--path", "tmp/AAPL-buy-draft.json",
        ])
        self.assertEqual(code, 1)
        self.assertFalse(buy_result["ok"])
        self.assertTrue(any("single_name_cap" in e for e in buy_result["errors"]))

        # ...but Trim against the same breach is accepted.
        trim_draft = self._decision_draft(
            proposed_action="Trim", policy_result=policy_result, thesis_rel=thesis_rel, thesis_hash=thesis_hash,
        )
        trim_path = self.run_dir / "tmp" / "AAPL-trim-draft.json"
        trim_path.write_text(json.dumps(trim_draft), encoding="utf-8")
        code, trim_result = self._run_cli([
            "save-decision", "--run-id", self.run_id, "--path", "tmp/AAPL-trim-draft.json", "--ticker", "AAPL",
        ])
        self.assertEqual(code, 0, trim_result)
        self.assertTrue(trim_result["ok"])


if __name__ == "__main__":
    unittest.main()
