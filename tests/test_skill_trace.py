"""Tests for the shared skill trace (`src/skill_trace.py`).

The behaviour under test is the three-way split: `ok` / `missing` /
`not_applicable`, with only the first two forming the completeness
denominator. Everything here is synthetic -- no tickers are fetched, no data
is read, no percentage below corresponds to a real security.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import skill_trace  # noqa: E402


class CompletenessArithmeticTest(unittest.TestCase):
    def test_not_applicable_is_excluded_from_the_denominator(self):
        """The regression this module exists for. A run that obtained
        everything obtainable reads 100%, even when most of the manifest did
        not apply to the subject."""
        trace = skill_trace.Trace(skill="synthetic", subject="SYNTH")
        trace.add("position", ok=["quantity", "book_value"])
        trace.add("derived.options", not_applicable=["iv_skew", "put_call_oi_ratio"])

        self.assertEqual(trace.completeness_pct, 100.0)
        self.assertEqual(trace.fields_graded, 2)
        self.assertEqual(trace.fields_not_applicable, 2)
        self.assertEqual(trace.missing_fields(), [])

    def test_real_gaps_still_reduce_completeness(self):
        trace = skill_trace.Trace(skill="synthetic", subject="SYNTH")
        trace.add("position", ok=["quantity"], missing=["book_value"])
        trace.add("derived.options", not_applicable=["iv_skew"])

        self.assertEqual(trace.completeness_pct, 50.0)
        self.assertEqual(trace.missing_fields(), ["position.book_value"])

    def test_all_not_applicable_does_not_divide_by_zero(self):
        trace = skill_trace.Trace(skill="synthetic", subject="SYNTH")
        trace.add("derived.options", not_applicable=["iv_skew"])
        self.assertEqual(trace.completeness_pct, 100.0)
        self.assertEqual(trace.fields_graded, 0)

    def test_empty_trace_is_complete_rather_than_zero(self):
        trace = skill_trace.Trace(skill="synthetic", subject="SYNTH")
        self.assertEqual(trace.completeness_pct, 100.0)

    def test_domain_counts_classify_partial_and_failed_separately(self):
        trace = skill_trace.Trace(skill="synthetic", subject="SYNTH")
        trace.add("full", ok=["a", "b"])
        trace.add("partial", ok=["a"], missing=["b"])
        trace.add("failed", missing=["a", "b"])
        trace.add("n_a", not_applicable=["a"])

        self.assertEqual(trace.domains_ok, 1)
        self.assertEqual(trace.domains_partial, 1)
        self.assertEqual(trace.domains_failed, 1)
        self.assertEqual(trace.domains_not_applicable, 1)


class TextLineFormatTest(unittest.TestCase):
    def _trace(self) -> skill_trace.Trace:
        trace = skill_trace.Trace(
            skill="investment-analyst-resources", subject="SYNTH", kind="stock"
        )
        trace.add("position", ok=["quantity", "book_value"])
        trace.add("derived.options", not_applicable=[f"m{i}" for i in range(7)])
        trace.add("derived.analyst", not_applicable=["a", "b", "c"])
        return trace

    def test_line_is_pipe_delimited_with_key_value_fields(self):
        line = skill_trace.format_line(self._trace(), now=datetime(2026, 8, 8, 12, 0, 0))
        self.assertTrue(line.startswith("2026-08-08 12:00:00 | TRACE | "))
        self.assertIn("skill=investment-analyst-resources", line)
        self.assertIn("subject=SYNTH", line)
        self.assertIn("kind=stock", line)
        self.assertIn("pct=100.0", line)
        self.assertTrue(line.endswith("\n"))

    def test_groups_are_collapsed_to_counts_not_spelled_out(self):
        """The old format spelled out every field path, producing lines over a
        thousand characters where the real gap was invisible."""
        line = skill_trace.format_line(self._trace())
        self.assertIn("n_a=derived.options[7],derived.analyst[3]", line)
        self.assertNotIn("m0", line)
        self.assertLess(len(line), 250)

    def test_absent_groups_render_as_a_dash(self):
        line = skill_trace.format_line(self._trace())
        self.assertIn("missing=-", line)


class EmitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.text_path = Path(self.temp_dir.name) / "SkillTrace.txt"
        self.jsonl_path = Path(self.temp_dir.name) / "SkillTrace.jsonl"

    def _emit(self, trace, **kwargs):
        return skill_trace.emit(
            trace, text_log_path=self.text_path, jsonl_log_path=self.jsonl_path, **kwargs
        )

    def _trace(self, subject="SYNTH") -> skill_trace.Trace:
        trace = skill_trace.Trace(skill="synthetic-skill", subject=subject, kind="stock")
        trace.add("position", ok=["quantity"], missing=["book_value"])
        trace.add("derived.options", not_applicable=["iv_skew"])
        return trace

    def test_writes_both_the_text_line_and_the_jsonl_record(self):
        self._emit(self._trace())
        self.assertEqual(len(self.text_path.read_text(encoding="utf-8").splitlines()), 1)
        record = json.loads(self.jsonl_path.read_text(encoding="utf-8").strip())
        self.assertEqual(record["skill"], "synthetic-skill")
        self.assertEqual(record["subject"], "SYNTH")
        self.assertEqual(record["completeness_pct"], 50.0)

    def test_jsonl_keeps_the_field_names_the_text_line_collapses(self):
        self._emit(self._trace())
        record = json.loads(self.jsonl_path.read_text(encoding="utf-8").strip())
        self.assertEqual(record["missing"], ["position.book_value"])
        self.assertEqual(record["not_applicable"], ["derived.options.iv_skew"])
        self.assertEqual(record["domains"]["position"]["ok"], ["quantity"])

    def test_appends_rather_than_overwrites(self):
        self._emit(self._trace("A"))
        self._emit(self._trace("B"))
        lines = self.text_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("subject=A", lines[0])
        self.assertIn("subject=B", lines[1])

    def test_a_logging_failure_never_raises(self):
        """A trace write must not sink a data pull that already succeeded."""
        unwritable = Path(self.temp_dir.name) / "nope.txt" / "deeper.txt"
        result = skill_trace.emit(
            self._trace(), text_log_path=unwritable, jsonl_log_path=unwritable
        )
        self.assertEqual(result["subject"], "SYNTH")

    def test_run_audit_event_is_written_when_a_run_is_supplied(self):
        import config
        from unittest.mock import patch

        base = Path(self.temp_dir.name) / "ws"
        runs_root = base / "runs"
        runs_root.mkdir(parents=True)
        with patch.multiple(
            config, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        ):
            from workspace import audit

            run_dir = runs_root / "synthetic-run"
            run_dir.mkdir()
            self._emit(self._trace(), run_dir=run_dir, run_id="synthetic-run")

            events = audit.read_events(run_dir)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "trace_recorded")
            self.assertEqual(events[0]["actor"], "synthetic-skill")
            self.assertEqual(events[0]["details"]["completeness_pct"], 50.0)
            self.assertEqual(events[0]["details"]["missing"], ["position.book_value"])

    def test_no_audit_event_without_a_run(self):
        payload = self._emit(self._trace())
        self.assertNotIn("run_id", payload)


class WorkspaceOutcomeTest(unittest.TestCase):
    """The audit-critical piece: whether this pull's evidence landed in a run
    workspace, recorded on the trace itself so a skip is a positive, greppable
    record rather than an absence indistinguishable from 'nothing ran'."""

    def _trace(self) -> skill_trace.Trace:
        trace = skill_trace.Trace(skill="synthetic-skill", subject="SYNTH", kind="stock")
        trace.add("position", ok=["quantity"])
        return trace

    def test_to_dict_carries_the_workspace_outcome(self):
        trace = self._trace()
        trace.workspace = skill_trace.WorkspaceOutcome(status="created", run_id="run-1")
        payload = trace.to_dict()
        self.assertEqual(payload["workspace"], {"status": "created", "run_id": "run-1", "skip_reason": None})

    def test_to_dict_reports_none_when_no_outcome_was_set(self):
        payload = self._trace().to_dict()
        self.assertIsNone(payload["workspace"])

    def test_text_line_names_an_attached_or_created_run(self):
        trace = self._trace()
        trace.workspace = skill_trace.WorkspaceOutcome(status="created", run_id="run-1")
        self.assertIn("run=run-1", skill_trace.format_line(trace))

        trace.workspace = skill_trace.WorkspaceOutcome(status="attached", run_id="run-2")
        self.assertIn("run=run-2", skill_trace.format_line(trace))

    def test_text_line_names_the_skip_reason_not_just_that_it_skipped(self):
        """A bare 'run=skipped' would still hide the one thing an auditor
        needs: whether the skip was a deliberate --no-run or a debug/test
        invocation. The reason has to be inline, not just in the JSONL."""
        trace = self._trace()
        trace.workspace = skill_trace.WorkspaceOutcome(status="skipped", skip_reason="--no-run")
        self.assertIn("run=skipped(--no-run)", skill_trace.format_line(trace))

    def test_text_line_falls_back_to_a_dash_when_unset(self):
        self.assertIn("run=-", skill_trace.format_line(self._trace()))

    def test_emit_sets_workspace_from_convenience_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = Path(tmp) / "t.txt"
            jsonl_path = Path(tmp) / "t.jsonl"
            trace = self._trace()
            skill_trace.emit(
                trace, text_log_path=text_path, jsonl_log_path=jsonl_path,
                run_id="run-3", workspace_status="attached",
            )
            self.assertIsNotNone(trace.workspace)
            self.assertEqual(trace.workspace.status, "attached")
            self.assertEqual(trace.workspace.run_id, "run-3")
            record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["workspace"]["status"], "attached")

    def test_emit_does_not_override_a_workspace_outcome_set_by_the_caller(self):
        """A caller (like process_ticker, which must set it before the digest's
        own `to_dict()` call) may already have populated `trace.workspace`;
        emit's convenience params must not clobber it."""
        with tempfile.TemporaryDirectory() as tmp:
            trace = self._trace()
            trace.workspace = skill_trace.WorkspaceOutcome(status="created", run_id="explicit")
            skill_trace.emit(
                trace, text_log_path=Path(tmp) / "t.txt", jsonl_log_path=Path(tmp) / "t.jsonl",
                run_id="convenience-arg", workspace_status="attached",
            )
            self.assertEqual(trace.workspace.run_id, "explicit")


class FromStatusMapTest(unittest.TestCase):
    """The adapter `market-analyst-resources` uses: per-entry statuses rather
    than per-field presence."""

    def test_maps_statuses_onto_the_three_way_split(self):
        trace = skill_trace.from_status_map(
            "market-analyst-resources",
            "2026-08-08",
            {"BOC_RATE": "fetched", "CPI": "stale", "PAYROLLS": "not_configured"},
            ok_statuses=frozenset({"fetched", "ok"}),
            not_applicable_statuses=frozenset({"not_configured"}),
            domain_of={"BOC_RATE": "policy", "CPI": "inflation", "PAYROLLS": "labour"},
        )
        self.assertEqual(trace.missing_fields(), ["inflation.CPI"])
        self.assertEqual(trace.not_applicable_fields(), ["labour.PAYROLLS"])
        self.assertEqual(trace.completeness_pct, 50.0)

    def test_unconfigured_stubs_do_not_peg_completeness_below_full(self):
        """The registry's `<TBD>` indicators are not data a run failed to
        fetch; counting them as gaps would make a healthy run look degraded
        forever."""
        trace = skill_trace.from_status_map(
            "market-analyst-resources",
            "2026-08-08",
            {"A": "fetched", "B": "not_configured", "C": "not_configured"},
            ok_statuses=frozenset({"fetched", "ok"}),
            not_applicable_statuses=frozenset({"not_configured"}),
        )
        self.assertEqual(trace.completeness_pct, 100.0)
        self.assertEqual(trace.fields_not_applicable, 2)

    def test_entries_group_by_domain(self):
        trace = skill_trace.from_status_map(
            "market-analyst-resources",
            "2026-08-08",
            {"A": "fetched", "B": "fetched"},
            ok_statuses=frozenset({"fetched"}),
            not_applicable_statuses=frozenset(),
            domain_of={"A": "rates", "B": "rates"},
        )
        self.assertEqual([d.name for d in trace.domains], ["rates"])
        self.assertEqual(trace.domains[0].ok, ["A", "B"])

    def test_unknown_status_is_treated_as_missing(self):
        trace = skill_trace.from_status_map(
            "synthetic", "s", {"A": "some_new_status"},
            ok_statuses=frozenset({"ok"}), not_applicable_statuses=frozenset(),
        )
        self.assertEqual(trace.missing_fields(), ["entries.A"])


if __name__ == "__main__":
    unittest.main()
