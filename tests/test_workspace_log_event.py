"""Tests for the `run log-event` CLI subcommand (`src/workspace/cli.py`).

Lets an orchestrating agent (e.g. `investment-orchestrator`) append its own
audit events -- preflight results, stage dispatch, final report -- into a
run's `audit_log.jsonl`, the same file the specialist stages already write
to via `save-thesis`/`save-decision`/etc. Offline, fixture-based, mirrors
`CliWiringTest` in `tests/test_investment_analyst_cli.py`.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from workspace import audit as audit_module  # noqa: E402
from workspace import cli  # noqa: E402
from workspace import run as run_module  # noqa: E402
from workspace.models import Request, RequestBody, Subject, SubjectIdentifiers  # noqa: E402


class LogEventCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.runs_root = base / "runs"
        self.archive_root = base / "archive"
        self.runs_root.mkdir()
        self.archive_root.mkdir()
        patcher = patch.multiple(
            config, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=self.runs_root,
            WORKSPACE_ARCHIVE_FOLDER=self.archive_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        request = Request(
            mode="initial_research",
            subject=Subject(type="security", identifiers=SubjectIdentifiers(ticker="PLTR")),
            request=RequestBody(question="Is PLTR fundamentally attractive?"),
        )
        self.run_id, self.run_dir = run_module.create_from_request(request)

    def _run_cli(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, json.loads(buf.getvalue())

    def test_log_event_appends_to_audit_log(self):
        code, result = self._run_cli([
            "log-event", "--run-id", self.run_id,
            "--event", "preflight_passed", "--actor", "investment-orchestrator",
            "--details", json.dumps({"ticker_id": 42, "status": "owned"}),
        ])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["event"], "preflight_passed")
        self.assertEqual(result["actor"], "investment-orchestrator")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["details"], {"ticker_id": 42, "status": "owned"})

        events = audit_module.read_events(self.run_dir)
        matching = [e for e in events if e["event"] == "preflight_passed"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["actor"], "investment-orchestrator")

    def test_log_event_supports_failure_status_and_error(self):
        code, result = self._run_cli([
            "log-event", "--run-id", self.run_id,
            "--event", "analyst_stage_failed", "--actor", "investment-orchestrator",
            "--status", "failure", "--error", "insufficient_evidence",
        ])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["error"], "insufficient_evidence")

    def test_log_event_rejects_non_object_details(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main([
                "log-event", "--run-id", self.run_id,
                "--event", "bad", "--actor", "investment-orchestrator",
                "--details", json.dumps(["not", "an", "object"]),
            ])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
