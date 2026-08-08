"""Tests for the run workspace (`src/workspace/`).

Every fixture is clearly synthetic -- ticker `SYNTH`, a made-up question, no
real positions or prices. Nothing here touches the network, the pipeline
database, or any real account data, and no test asserts a financial value.

Each test points `config.WORKSPACE_RUNS_FOLDER` at a temp directory, so a run
is never created under the repository's own `workspace/`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import workspace  # noqa: E402
from workspace import audit, evidence, manifest, paths, run, state, validation  # noqa: E402

SYNTHETIC_REQUEST = {
    "schema_version": "1.0",
    "mode": "portfolio_check",
    "subject": {
        "type": "security",
        "identifiers": {"ticker": "SYNTH", "name": "Synthetic Test Security"},
    },
    "request": {
        "question": "Should this synthetic position be held, trimmed, or exited?",
        "context": "Synthetic fixture. Not real market data.",
    },
    "required_analysis": ["business_quality", "valuation", "portfolio_impact"],
    "restrictions": {
        "research_only": True,
        "execute_trades": False,
        "do_not_invent_data": True,
        "human_review_required": True,
    },
}


class WorkspaceTestCase(unittest.TestCase):
    """Redirects the workspace roots into a temp directory for every test."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.runs_root = base / "runs"
        self.archive_root = base / "archive"
        self.runs_root.mkdir()
        self.archive_root.mkdir()

        patcher = patch.multiple(
            config,
            WORKSPACE_FOLDER=base,
            WORKSPACE_RUNS_FOLDER=self.runs_root,
            WORKSPACE_ARCHIVE_FOLDER=self.archive_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.request_path = base / "request.yaml"
        self.write_request()

    def write_request(self, **overrides) -> Path:
        payload = json.loads(json.dumps(SYNTHETIC_REQUEST))
        payload.update(overrides)
        self.request_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return self.request_path

    def create(self) -> tuple[str, Path]:
        return run.create_run(self.request_path)


# --- 1. run creation ---------------------------------------------------


class RunCreationTest(WorkspaceTestCase):
    def test_creates_every_required_file_and_directory(self):
        run_id, directory = self.create()
        self.assertTrue(directory.is_dir())
        for name in (paths.REQUEST_FILENAME, paths.METADATA_FILENAME,
                     paths.MANIFEST_FILENAME, paths.AUDIT_FILENAME,
                     paths.EVIDENCE_REGISTRY_RELPATH):
            self.assertTrue((directory / name).exists(), f"missing {name}")
        for subdir in paths.RUN_SUBDIRS:
            self.assertTrue((directory / subdir).is_dir(), f"missing dir {subdir}")
        self.assertEqual(run.read_metadata(directory).status, state.CREATED)
        self.assertEqual(run.read_metadata(directory).run_id, run_id)

    def test_run_id_is_readable_and_sanitized(self):
        run_id, _ = self.create()
        self.assertIn("portfolio_check", run_id)
        self.assertIn("SYNTH", run_id)
        self.assertTrue(paths.is_valid_run_id(run_id))

    def test_run_id_segments_cannot_smuggle_path_separators(self):
        generated = paths.generate_run_id("../../etc", "..\\..\\windows")
        self.assertNotIn("/", generated)
        self.assertNotIn("\\", generated)
        self.assertTrue(paths.is_valid_run_id(generated))

    def test_records_components_including_unavailable_ones(self):
        _, directory = self.create()
        components = run.read_metadata(directory).available_components
        # An absent stage is reported, never silently omitted.
        self.assertEqual(components["policy_engine"], "unavailable")
        self.assertEqual(components["portfolio_manager_agent"], "deferred")

    def test_failed_initialization_leaves_no_partial_run(self):
        # Fail at the last step; the staging directory must be removed and the
        # runs root left with nothing half-built.
        with patch.object(manifest, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(list(self.runs_root.iterdir()), [])


# --- 2. duplicate-run prevention ---------------------------------------


class DuplicateRunTest(WorkspaceTestCase):
    def test_existing_run_directory_is_never_overwritten(self):
        self.write_request(run_id="fixed-synthetic-run")
        run_id, directory = self.create()
        self.assertEqual(run_id, "fixed-synthetic-run")
        marker = directory / "inputs" / "keep-me.txt"
        marker.write_text("original", encoding="utf-8")

        with self.assertRaises(run.RunExistsError):
            self.create()
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_two_runs_in_the_same_second_get_distinct_ids(self):
        moment = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
        first = paths.generate_run_id("portfolio_check", "SYNTH", now=moment)
        second = paths.generate_run_id("portfolio_check", "SYNTH", now=moment)
        self.assertNotEqual(first, second)


# --- 3. request normalization ------------------------------------------


class RequestNormalizationTest(WorkspaceTestCase):
    def test_stored_request_gains_run_id_and_created_at(self):
        run_id, directory = self.create()
        stored = yaml.safe_load((directory / paths.REQUEST_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(stored["run_id"], run_id)
        self.assertIsNotNone(stored["created_at"])

    def test_absent_ticker_is_allowed(self):
        self.write_request(
            subject={"type": "market", "identifiers": {}},
            request={"question": "What changed in the macro landscape?"},
        )
        _, directory = self.create()
        stored = run.read_request(directory)
        self.assertIsNone(stored.subject.identifiers.ticker)

    def test_unknown_key_is_rejected_rather_than_silently_ignored(self):
        self.write_request(unexpected_field="typo")
        with self.assertRaises(workspace.WorkspaceError):
            self.create()

    def test_request_cannot_enable_trade_execution(self):
        self.write_request(
            restrictions={
                "research_only": True, "execute_trades": True,
                "do_not_invent_data": True, "human_review_required": True,
            }
        )
        with self.assertRaises(workspace.WorkspaceError):
            self.create()

    def test_request_cannot_disable_human_review(self):
        self.write_request(
            restrictions={
                "research_only": True, "execute_trades": False,
                "do_not_invent_data": True, "human_review_required": False,
            }
        )
        with self.assertRaises(workspace.WorkspaceError):
            self.create()


# --- 4. audit events ---------------------------------------------------


class AuditLogTest(WorkspaceTestCase):
    def test_creation_appends_a_run_created_event(self):
        run_id, directory = self.create()
        events = audit.read_events(directory)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "run_created")
        self.assertEqual(events[0]["run_id"], run_id)
        self.assertIn("timestamp", events[0])
        self.assertIn("event_id", events[0])

    def test_log_is_append_only_across_operations(self):
        _, directory = self.create()
        run.set_status(directory, state.IN_PROGRESS)
        run.rebuild_manifest(directory)
        kinds = [event["event"] for event in audit.read_events(directory)]
        self.assertEqual(kinds, ["run_created", "status_changed", "manifest_built"])

    def test_secrets_are_dropped_from_event_details(self):
        _, directory = self.create()
        audit.append_event(
            directory, run_id="x", event="test",
            details={"api_key": "sk-should-not-persist", "ticker": "SYNTH"},
        )
        raw = (directory / paths.AUDIT_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("sk-should-not-persist", raw)
        self.assertIn("SYNTH", raw)

    def test_truncated_final_line_does_not_break_reading(self):
        _, directory = self.create()
        with (directory / paths.AUDIT_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write('{"event": "truncated"')
        self.assertEqual(len(audit.read_events(directory)), 1)
        self.assertEqual(audit.count_malformed_lines(directory), 1)


# --- 5 & 6. evidence registration --------------------------------------


class EvidenceRegistrationTest(WorkspaceTestCase):
    def test_registers_a_file_with_a_content_hash(self):
        _, directory = self.create()
        artifact = directory / "evidence" / "synthetic-bundle.json"
        artifact.write_text(json.dumps({"synthetic": True}), encoding="utf-8")

        record = evidence.register(
            directory, run_id="r", evidence_type="market_data_bundle",
            source_name="synthetic", status="available", artifact=artifact,
        )
        self.assertEqual(record.artifact_path, "evidence/synthetic-bundle.json")
        self.assertEqual(record.content_hash, evidence.content_hash(artifact))
        self.assertIn(record.evidence_id, evidence.known_evidence_ids(directory))

    def test_registers_a_gap_explicitly_instead_of_inventing_a_value(self):
        _, directory = self.create()
        record = evidence.register(
            directory, run_id="r", evidence_type="price_quote",
            source_name="synthetic", status="missing",
            notes=["no live pull in this fixture"],
        )
        self.assertEqual(record.status, "missing")
        self.assertIsNone(record.artifact_path)
        self.assertIsNone(record.content_hash)
        self.assertIn("price_quote from synthetic: missing", evidence.missing_summary(directory))

    def test_duplicate_evidence_id_is_rejected(self):
        _, directory = self.create()
        evidence.register(
            directory, run_id="r", evidence_type="price_quote", source_name="synthetic",
            status="missing", evidence_id="ev_fixed",
        )
        with self.assertRaises(evidence.EvidenceError):
            evidence.register(
                directory, run_id="r", evidence_type="price_quote", source_name="synthetic",
                status="missing", evidence_id="ev_fixed",
            )

    def test_same_artifact_cannot_be_registered_twice(self):
        _, directory = self.create()
        artifact = directory / "evidence" / "bundle.json"
        artifact.write_text("{}", encoding="utf-8")
        evidence.register(
            directory, run_id="r", evidence_type="bundle", source_name="synthetic",
            status="available", artifact=artifact,
        )
        with self.assertRaises(evidence.EvidenceError):
            evidence.register(
                directory, run_id="r", evidence_type="bundle", source_name="synthetic",
                status="available", artifact=artifact,
            )

    def test_artifact_outside_the_run_is_rejected(self):
        _, directory = self.create()
        outside = Path(self.temp_dir.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(workspace.PathEscapeError):
            evidence.register(
                directory, run_id="r", evidence_type="bundle", source_name="synthetic",
                status="available", artifact=outside,
            )

    def test_missing_artifact_file_is_rejected(self):
        _, directory = self.create()
        with self.assertRaises(evidence.EvidenceError):
            evidence.register(
                directory, run_id="r", evidence_type="bundle", source_name="synthetic",
                status="available", artifact=directory / "evidence" / "nope.json",
            )


# --- 7. context-manifest generation ------------------------------------


class ManifestTest(WorkspaceTestCase):
    def test_manifest_lists_evidence_and_names_the_gaps(self):
        _, directory = self.create()
        evidence.register(
            directory, run_id="r", evidence_type="price_quote",
            source_name="synthetic", status="missing",
        )
        run.rebuild_manifest(directory, target_stage="investment_analyst")

        built = manifest.read(directory)
        self.assertEqual(built["target_stage"], "investment_analyst")
        self.assertEqual(len(built["evidence"]), 1)
        self.assertTrue(built["missing_information"])
        # Nothing usable was registered, so the manifest must not claim ok.
        self.assertEqual(built["validation_status"], "incomplete")

    def test_manifest_is_ok_only_when_usable_evidence_and_no_gaps(self):
        _, directory = self.create()
        artifact = directory / "evidence" / "bundle.json"
        artifact.write_text("{}", encoding="utf-8")
        evidence.register(
            directory, run_id="r", evidence_type="bundle", source_name="synthetic",
            status="available", artifact=artifact,
        )
        run.rebuild_manifest(directory)
        self.assertEqual(manifest.read(directory)["validation_status"], "ok")

    def test_manifest_carries_restrictions_forward(self):
        _, directory = self.create()
        built = manifest.read(directory)
        self.assertTrue(built["restrictions"]["research_only"])
        self.assertFalse(built["restrictions"]["execute_trades"])
        self.assertTrue(built["restrictions"]["human_review_required"])

    def test_manifest_picks_up_input_and_calculation_files(self):
        _, directory = self.create()
        (directory / "inputs" / "holdings.csv").write_text("synthetic", encoding="utf-8")
        (directory / "calculations" / "metrics.json").write_text("{}", encoding="utf-8")
        run.rebuild_manifest(directory)
        built = manifest.read(directory)
        self.assertIn("inputs/holdings.csv", built["input_paths"])
        self.assertIn("calculations/metrics.json", built["calculation_paths"])


# --- 8. invalid evidence references ------------------------------------


class InvalidEvidenceReferenceTest(WorkspaceTestCase):
    def test_manifest_citing_an_unregistered_id_fails_validation(self):
        _, directory = self.create()
        built = manifest.read(directory)
        built["evidence"] = [
            {"evidence_id": "ev_never_registered", "evidence_type": "bundle",
             "status": "available", "path": None}
        ]
        (directory / paths.MANIFEST_FILENAME).write_text(
            yaml.safe_dump(built), encoding="utf-8"
        )
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("ev_never_registered" in e for e in result["errors"]))

    def test_agent_output_citing_an_unregistered_id_fails_validation(self):
        run_id, directory = self.create()
        (directory / "agent_outputs" / "analyst.json").write_text(
            json.dumps({
                "schema_version": "1.0", "run_id": run_id, "agent": "investment_analyst",
                "status": "complete", "generated_at": "2026-08-08T12:00:00Z",
                "evidence_ids_used": ["ev_does_not_exist"], "human_review_required": True,
            }),
            encoding="utf-8",
        )
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("ev_does_not_exist" in e for e in result["errors"]))

    def test_tampered_artifact_is_detected_by_hash_mismatch(self):
        _, directory = self.create()
        artifact = directory / "evidence" / "bundle.json"
        artifact.write_text(json.dumps({"value": 1}), encoding="utf-8")
        evidence.register(
            directory, run_id="r", evidence_type="bundle", source_name="synthetic",
            status="available", artifact=artifact,
        )
        artifact.write_text(json.dumps({"value": 999}), encoding="utf-8")
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("content hash" in e for e in result["errors"]))


# --- 9. path-traversal rejection ---------------------------------------


class PathTraversalTest(WorkspaceTestCase):
    def test_parent_traversal_is_rejected(self):
        _, directory = self.create()
        for attempt in ("../escape.json", "../../etc/passwd", "evidence/../../escape.json"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(workspace.PathEscapeError):
                    workspace.resolve_in_run(directory, attempt)

    def test_absolute_path_is_rejected(self):
        _, directory = self.create()
        with self.assertRaises(workspace.PathEscapeError):
            workspace.resolve_in_run(directory, str(Path(self.temp_dir.name) / "outside.json"))

    def test_ordinary_relative_path_resolves_inside_the_run(self):
        _, directory = self.create()
        resolved = workspace.resolve_in_run(directory, "evidence/sources.jsonl")
        self.assertTrue(directory.resolve() in resolved.parents)

    def test_manifest_path_escape_fails_validation(self):
        _, directory = self.create()
        built = manifest.read(directory)
        built["input_paths"] = ["../../escape.csv"]
        (directory / paths.MANIFEST_FILENAME).write_text(yaml.safe_dump(built), encoding="utf-8")
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("escape" in e for e in result["errors"]))

    def test_run_id_with_separators_is_refused(self):
        for bad in ("../other", "a/b", "a\\b", ".hidden", ""):
            with self.subTest(bad=bad):
                self.assertFalse(paths.is_valid_run_id(bad))


# --- 10. state transitions ---------------------------------------------


class StateTransitionTest(WorkspaceTestCase):
    def test_full_valid_lifecycle(self):
        _, directory = self.create()
        for target in (state.IN_PROGRESS, state.AWAITING_HUMAN_REVIEW, state.COMPLETED):
            run.set_status(directory, target)
        self.assertEqual(run.read_metadata(directory).status, state.COMPLETED)

    def test_skipping_straight_to_completed_is_refused(self):
        _, directory = self.create()
        with self.assertRaises(state.InvalidTransitionError):
            run.set_status(directory, state.COMPLETED)
        self.assertEqual(run.read_metadata(directory).status, state.CREATED)

    def test_archived_is_terminal(self):
        with self.assertRaises(state.InvalidTransitionError):
            state.assert_transition(state.ARCHIVED, state.IN_PROGRESS)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(state.InvalidTransitionError):
            state.assert_transition(state.CREATED, "sold_everything")

    def test_started_and_completed_timestamps_are_recorded(self):
        _, directory = self.create()
        run.set_status(directory, state.IN_PROGRESS)
        self.assertIsNotNone(run.read_metadata(directory).started_at)
        run.set_status(directory, state.COMPLETED)
        self.assertIsNotNone(run.read_metadata(directory).completed_at)

    def test_failing_a_run_records_the_reason(self):
        _, directory = self.create()
        run.set_status(directory, state.FAILED, note="synthetic failure")
        metadata = run.read_metadata(directory)
        self.assertEqual(metadata.status, state.FAILED)
        self.assertIn("synthetic failure", metadata.errors)


# --- 11. validation of an incomplete run -------------------------------


class IncompleteRunValidationTest(WorkspaceTestCase):
    def test_fresh_run_is_valid_but_warns_about_missing_evidence(self):
        _, directory = self.create()
        result = validation.validate_run(directory)
        self.assertTrue(result["ok"])
        self.assertIn("no evidence registered", result["warnings"])

    def test_missing_metadata_is_an_error(self):
        _, directory = self.create()
        (directory / paths.METADATA_FILENAME).unlink()
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any(paths.METADATA_FILENAME in e for e in result["errors"]))

    def test_malformed_evidence_line_is_an_error(self):
        _, directory = self.create()
        with (directory / paths.EVIDENCE_REGISTRY_RELPATH).open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("malformed" in e for e in result["errors"]))

    def test_evidence_pointing_at_a_deleted_artifact_is_an_error(self):
        _, directory = self.create()
        artifact = directory / "evidence" / "bundle.json"
        artifact.write_text("{}", encoding="utf-8")
        evidence.register(
            directory, run_id="r", evidence_type="bundle", source_name="synthetic",
            status="available", artifact=artifact,
        )
        artifact.unlink()
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("artifact missing on disk" in e for e in result["errors"]))

    def test_empty_audit_log_is_an_error(self):
        _, directory = self.create()
        (directory / paths.AUDIT_FILENAME).write_text("", encoding="utf-8")
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("audit log is empty" in e for e in result["errors"]))

    def test_missing_run_directory_is_reported_not_raised(self):
        result = validation.validate_run(self.runs_root / "no-such-run")
        self.assertFalse(result["ok"])


# --- 12 & 13. archiving -------------------------------------------------


class ArchiveTest(WorkspaceTestCase):
    def _completed_run(self) -> tuple[str, Path]:
        run_id, directory = self.create()
        run.set_status(directory, state.IN_PROGRESS)
        run.set_status(directory, state.COMPLETED)
        return run_id, directory

    def test_archiving_moves_the_run_and_preserves_its_structure(self):
        run_id, directory = self._completed_run()
        (directory / "inputs" / "holdings.csv").write_text("synthetic", encoding="utf-8")

        destination = run.archive_run(directory, archive_root=self.archive_root)

        self.assertFalse(directory.exists())
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / paths.REQUEST_FILENAME).is_file())
        self.assertTrue((destination / paths.AUDIT_FILENAME).is_file())
        self.assertEqual(
            (destination / "inputs" / "holdings.csv").read_text(encoding="utf-8"), "synthetic"
        )
        self.assertEqual(run.read_metadata(destination).status, state.ARCHIVED)
        self.assertTrue(run.read_metadata(destination).archived)

    def test_archive_is_date_bucketed(self):
        _, directory = self._completed_run()
        destination = run.archive_run(directory, archive_root=self.archive_root)
        self.assertEqual(destination.parent.parent, self.archive_root)
        self.assertRegex(destination.parent.name, r"^\d{4}-\d{2}$")

    def test_only_tmp_contents_are_discarded(self):
        _, directory = self._completed_run()
        (directory / "tmp" / "scratch.txt").write_text("disposable", encoding="utf-8")
        (directory / "final" / "keep.json").write_text("{}", encoding="utf-8")

        destination = run.archive_run(directory, archive_root=self.archive_root)

        self.assertFalse((destination / "tmp" / "scratch.txt").exists())
        self.assertTrue((destination / "final" / "keep.json").is_file())

    def test_archive_collision_is_refused(self):
        run_id, directory = self._completed_run()
        metadata = run.read_metadata(directory)
        occupied = paths.archive_dir(
            run_id, created_at=metadata.created_at, root=self.archive_root
        )
        occupied.mkdir(parents=True)
        (occupied / "existing.txt").write_text("do not clobber", encoding="utf-8")

        with self.assertRaises(run.ArchiveCollisionError):
            run.archive_run(directory, archive_root=self.archive_root)

        self.assertTrue(directory.exists())
        self.assertEqual((occupied / "existing.txt").read_text(encoding="utf-8"), "do not clobber")

    def test_invalid_run_is_not_archived_by_default(self):
        _, directory = self._completed_run()
        (directory / paths.AUDIT_FILENAME).write_text("", encoding="utf-8")
        with self.assertRaises(workspace.WorkspaceError):
            run.archive_run(directory, archive_root=self.archive_root)
        self.assertTrue(directory.exists())

    def test_failed_run_is_retained_not_deleted(self):
        _, directory = self.create()
        run.set_status(directory, state.FAILED, note="synthetic failure")
        destination = run.archive_run(directory, archive_root=self.archive_root, validate=False)
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / paths.REQUEST_FILENAME).is_file())


# --- 14. no trade execution --------------------------------------------


class NoTradeExecutionTest(WorkspaceTestCase):
    def test_decision_proposal_cannot_claim_a_trade_was_executed(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            workspace.DecisionProposal(
                proposal_id="p1", run_id="r", subject={"type": "security"},
                proposed_action="trim", summary="synthetic", trade_executed=True,
            )

    def test_decision_proposal_cannot_waive_human_approval(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            workspace.DecisionProposal(
                proposal_id="p1", run_id="r", subject={"type": "security"},
                proposed_action="trim", summary="synthetic", human_approval_required=False,
            )

    def test_execution_fields_in_an_agent_output_fail_validation(self):
        run_id, directory = self.create()
        (directory / "agent_outputs" / "analyst.json").write_text(
            json.dumps({
                "schema_version": "1.0", "run_id": run_id, "agent": "investment_analyst",
                "status": "complete", "generated_at": "2026-08-08T12:00:00Z",
                "human_review_required": True,
                "findings": [{"note": "synthetic", "broker_order_id": "SHOULD-NOT-EXIST"}],
            }),
            encoding="utf-8",
        )
        result = validation.validate_run(directory)
        self.assertFalse(result["ok"])
        self.assertTrue(any("trade-execution" in e for e in result["errors"]))

    def test_execution_fields_are_found_at_any_nesting_depth(self):
        payload = {"a": {"b": [{"c": {"fill_price": 1.0}}]}}
        self.assertEqual(validation.find_execution_keys(payload), ["fill_price"])

    def test_clean_payload_reports_no_execution_fields(self):
        payload = {"findings": [{"claim": "synthetic", "evidence_ids": ["ev_1"]}]}
        self.assertEqual(validation.find_execution_keys(payload), [])


# --- listing ------------------------------------------------------------


class EnsureRunTest(WorkspaceTestCase):
    """Attach-or-create, the single entry point a data-collection skill uses so
    an ad-hoc pull does not need a separate `run create` step first."""

    def test_creates_a_run_under_the_exact_id_requested(self):
        run_id, directory, created = run.ensure_run(
            "my-synthetic-run", mode="data_pull", subject_type="security", ticker="SYNTH"
        )
        self.assertTrue(created)
        self.assertEqual(run_id, "my-synthetic-run")
        self.assertTrue((directory / paths.REQUEST_FILENAME).is_file())
        self.assertEqual(run.read_metadata(directory).status, state.CREATED)

    def test_second_call_with_the_same_id_attaches_instead_of_recreating(self):
        first_id, first_dir, created_first = run.ensure_run("shared-run", mode="data_pull")
        marker = first_dir / "evidence" / "already-here.json"
        marker.write_text("{}", encoding="utf-8")

        second_id, second_dir, created_second = run.ensure_run("shared-run", mode="data_pull")

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first_id, second_id)
        self.assertEqual(first_dir, second_dir)
        # This is what lets an orchestrator fan out N skills on one run ID
        # without caring which lands first.
        self.assertTrue(marker.is_file())

    def test_auto_generates_an_id(self):
        run_id, directory, created = run.ensure_run(
            "auto", mode="data_pull", subject_type="security", ticker="SYNTH"
        )
        self.assertTrue(created)
        self.assertNotEqual(run_id, "auto")
        self.assertIn("SYNTH", run_id)
        self.assertTrue(directory.is_dir())

    def test_none_also_generates_an_id(self):
        run_id, _, created = run.ensure_run(None, mode="data_pull")
        self.assertTrue(created)
        self.assertTrue(paths.is_valid_run_id(run_id))

    def test_auto_created_run_is_honest_that_no_question_was_asked(self):
        """An auto-created run must not fabricate a research question -- the
        stored request has to say plainly that none was recorded, or a later
        reader would take the synthesized text for the owner's intent."""
        _, directory, _ = run.ensure_run(
            "honest-run", mode="data_pull", trigger="investment-analyst-resources"
        )
        request = run.read_request(directory)
        self.assertIn("no research question was recorded", request.request.question)
        self.assertIn("investment-analyst-resources", request.request.question)

    def test_auto_created_run_still_forbids_trade_execution(self):
        _, directory, _ = run.ensure_run("guarded-run", mode="data_pull")
        restrictions = run.read_request(directory).restrictions
        self.assertTrue(restrictions.research_only)
        self.assertFalse(restrictions.execute_trades)
        self.assertTrue(restrictions.human_review_required)

    def test_auto_created_run_validates_clean(self):
        _, directory, _ = run.ensure_run(
            "valid-run", mode="data_pull", subject_type="security", ticker="SYNTH"
        )
        result = validation.validate_run(directory)
        self.assertTrue(result["ok"], result["errors"])

    def test_a_traversing_id_is_refused_rather_than_created(self):
        for bad in ("../escape", "a/b", ".hidden"):
            with self.subTest(bad=bad):
                with self.assertRaises(workspace.WorkspaceError):
                    run.ensure_run(bad, mode="data_pull")

    def test_will_not_attach_to_a_half_built_directory(self):
        """A directory that merely exists is not a run. Writing evidence into
        one whose metadata never landed would produce an artifact nothing can
        later validate."""
        stub = self.runs_root / "half-built"
        (stub / "evidence").mkdir(parents=True)
        with self.assertRaises(workspace.WorkspaceError):
            run.ensure_run("half-built", mode="data_pull")

    def test_ticker_is_recorded_as_the_subject(self):
        _, directory, _ = run.ensure_run(
            "subject-run", mode="data_pull", subject_type="security", ticker="SYNTH"
        )
        request = run.read_request(directory)
        self.assertEqual(request.subject.type, "security")
        self.assertEqual(request.subject.identifiers.ticker, "SYNTH")

    def test_a_subjectless_run_is_allowed(self):
        """A multi-ticker or market-wide pull has no single subject; forcing a
        placeholder there would misdescribe the run."""
        _, directory, _ = run.ensure_run("market-run", mode="market_scan", subject_type="market")
        self.assertIsNone(run.read_request(directory).subject.identifiers.ticker)

    def test_survives_a_create_race_on_an_explicit_id(self):
        """Two invocations sharing one --run-id can both see the directory
        missing and both attempt to create it -- the fan-out pattern this
        module exists to support. The loser must attach to the winner's run,
        not error, or fan-out becomes flaky under real concurrency."""
        winner_id, winner_dir, _ = run.ensure_run("race-run", mode="data_pull")
        marker = winner_dir / "evidence" / "winner-was-here.json"
        marker.write_text("{}", encoding="utf-8")

        with patch.object(
            run, "create_from_request", side_effect=run.RunExistsError("lost the race")
        ):
            loser_id, loser_dir, created = run.ensure_run("race-run", mode="data_pull")

        self.assertFalse(created)
        self.assertEqual(loser_id, "race-run")
        self.assertEqual(loser_dir, winner_dir)
        self.assertTrue(marker.is_file())

    def test_a_genuine_run_exists_error_on_the_auto_path_still_raises(self):
        """The auto-generated-ID path carries a random suffix, so a collision
        there is a real anomaly, not the benign fan-out race -- it must not be
        swallowed into a silent attach."""
        with patch.object(
            run, "create_from_request", side_effect=run.RunExistsError("genuine collision")
        ):
            with self.assertRaises(run.RunExistsError):
                run.ensure_run("auto", mode="data_pull")


class ListRunsTest(WorkspaceTestCase):
    def test_lists_created_runs(self):
        self.write_request(run_id="run-alpha")
        self.create()
        self.write_request(run_id="run-beta")
        self.create()
        listed = {entry["run_id"] for entry in run.list_runs(root=self.runs_root)}
        self.assertEqual(listed, {"run-alpha", "run-beta"})

    def test_empty_runs_root_lists_nothing(self):
        self.assertEqual(run.list_runs(root=self.runs_root), [])


if __name__ == "__main__":
    unittest.main()
