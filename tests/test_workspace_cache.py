"""Tests for the run-scoped ephemeral cache (`src/workspace/cache.py`).

Every fixture points `config.WORKSPACE_RUNS_FOLDER` at a temp directory,
matching `test_run_workspace.py`'s convention, so a test run is never
created under the repository's own `workspace/`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from workspace import audit, cache, evidence, run, state, validation  # noqa: E402


class WorkspaceCacheTestCase(unittest.TestCase):
    """Redirects the workspace roots into a temp directory for every test,
    matching `test_run_workspace.WorkspaceTestCase`."""

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

    def _make_run(self, run_id="cache-test-run") -> tuple[str, Path]:
        resolved, directory, _created = run.ensure_run(
            run_id, mode="test", subject_type="security", ticker="TEST"
        )
        return resolved, directory


# --- write_payload / purge basics -------------------------------------------


class WritePayloadTest(WorkspaceCacheTestCase):
    def test_write_payload_lands_in_cache_dir(self):
        run_id, directory = self._make_run()
        path = cache.write_payload(directory, "TEST-blob.json", b'{"a": 1}', source="yfinance")

        self.assertEqual(path, directory / "cache" / "TEST-blob.json")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b'{"a": 1}')

    def test_write_payload_accepts_str_or_bytes(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", '{"x": 1}', source="duckdb")
        cache.write_payload(directory, "b.json", b'{"y": 2}', source="duckdb")

        self.assertEqual((directory / "cache" / "a.json").read_text(encoding="utf-8"), '{"x": 1}')
        self.assertEqual((directory / "cache" / "b.json").read_bytes(), b'{"y": 2}')

    def test_write_payload_rejects_a_nested_path(self):
        run_id, directory = self._make_run()
        with self.assertRaises(ValueError):
            cache.write_payload(directory, "sub/blob.json", b"{}", source="yfinance")


class PurgeTest(WorkspaceCacheTestCase):
    def test_purge_deletes_payloads_and_writes_a_manifest(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")
        cache.write_payload(directory, "b.json", b"bbbb", source="duckdb")

        result = cache.purge(directory, run_id)

        self.assertEqual(result["purged"], 2)
        cache_files = {p.name for p in (directory / "cache").iterdir()}
        self.assertEqual(cache_files, {"cache_manifest.json"})

        manifest = json.loads((directory / "cache" / "cache_manifest.json").read_text(encoding="utf-8"))
        by_name = {entry["filename"]: entry for entry in manifest}
        self.assertEqual(set(by_name), {"a.json", "b.json"})
        self.assertEqual(by_name["a.json"]["bytes"], 3)
        self.assertEqual(by_name["a.json"]["source"], "yfinance")
        self.assertIsNotNone(by_name["a.json"]["sha256"])
        self.assertIsNotNone(by_name["a.json"]["fetched_at"])
        self.assertIsNotNone(by_name["a.json"]["purged_at"])

    def test_purge_retains_provenance_hash_matching_the_original_bytes(self):
        import hashlib

        run_id, directory = self._make_run()
        payload = b'{"raw": "payload data"}'
        cache.write_payload(directory, "a.json", payload, source="yfinance")

        cache.purge(directory, run_id)

        manifest = json.loads((directory / "cache" / "cache_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest[0]["sha256"], hashlib.sha256(payload).hexdigest())

    def test_purge_is_idempotent_no_op_on_an_already_purged_cache(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")
        first = cache.purge(directory, run_id)
        self.assertEqual(first["purged"], 1)

        second = cache.purge(directory, run_id)
        self.assertEqual(second["purged"], 0)
        self.assertEqual(second["manifest_entries"], 1)

        events = [e for e in audit.read_events(directory) if e["event"] == "cache_purged"]
        self.assertEqual(len(events), 1)

    def test_purge_on_a_never_populated_cache_is_a_clean_no_op(self):
        run_id, directory = self._make_run()

        result = cache.purge(directory, run_id)

        self.assertEqual(result, {"purged": 0, "manifest_entries": 0})
        events = [e for e in audit.read_events(directory) if e["event"] == "cache_purged"]
        self.assertEqual(events, [])

    def test_purge_appends_one_cache_purged_audit_event_with_totals(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")
        cache.write_payload(directory, "b.json", b"bb", source="yfinance")

        cache.purge(directory, run_id)

        events = [e for e in audit.read_events(directory) if e["event"] == "cache_purged"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["purged"], 2)
        self.assertEqual(events[0]["details"]["total_bytes"], 5)


# --- validate_run stays ok after a purge ------------------------------------


class ValidateAfterPurgeTest(WorkspaceCacheTestCase):
    def test_validate_run_stays_ok_after_cache_is_purged(self):
        """The critical constraint the whole module exists to satisfy: a
        cache payload is never registered as evidence, so purging it must
        never turn `validate_run` into a permanent error the way deleting a
        *registered* evidence artifact would."""
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"raw payload", source="yfinance")

        # A real evidence artifact, registered and NOT purged, to prove the
        # run is otherwise healthy -- the test would be meaningless if it
        # only ever validated an empty run.
        evidence_dir = directory / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        artifact = evidence_dir / "TEST-bundle.json"
        artifact.write_text('{"ticker": "TEST"}', encoding="utf-8")
        evidence.register(
            directory, run_id=run_id, evidence_type="market_data_bundle", source_name="duckdb+yfinance",
            status="available", artifact=artifact,
        )

        cache.purge(directory, run_id)

        result = validation.validate_run(directory)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["counts"]["evidence"], 1)


# --- run.set_status wiring ---------------------------------------------------


class SetStatusPurgeWiringTest(WorkspaceCacheTestCase):
    def test_completed_purges_cache(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        run.set_status(directory, state.IN_PROGRESS)
        self.assertTrue((directory / "cache" / "a.json").is_file())

        run.set_status(directory, state.COMPLETED)
        self.assertFalse((directory / "cache" / "a.json").is_file())
        self.assertTrue((directory / "cache" / "cache_manifest.json").is_file())

    def test_awaiting_human_review_does_not_purge(self):
        """Not terminal by design (`state.py`) -- the analyst's cache must
        survive for the portfolio-manager stage that follows in the same
        run."""
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        run.set_status(directory, state.IN_PROGRESS)
        run.set_status(directory, state.AWAITING_HUMAN_REVIEW)

        self.assertTrue((directory / "cache" / "a.json").is_file())

    def test_failed_purges_cache(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        run.set_status(directory, state.FAILED, note="synthetic failure")

        self.assertFalse((directory / "cache" / "a.json").is_file())

    def test_insufficient_evidence_purges_cache(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        run.set_status(directory, state.IN_PROGRESS)
        run.set_status(directory, state.INSUFFICIENT_EVIDENCE, note="blocking gap")

        self.assertFalse((directory / "cache" / "a.json").is_file())

    def test_archive_purges_a_cache_that_never_went_through_a_terminal_set_status(self):
        """`archive_run` can be reached directly from a non-terminal status
        (e.g. `awaiting_input`), bypassing `set_status`'s purge hook -- must
        still be covered."""
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")
        run.set_status(directory, state.AWAITING_INPUT)

        run.archive_run(directory, validate=False)

        archived_dir = self.archive_root
        found = list(archived_dir.rglob("a.json"))
        self.assertEqual(found, [])


# --- gc sweeper --------------------------------------------------------------


class GcTest(WorkspaceCacheTestCase):
    def test_gc_dry_run_reports_without_deleting(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        results = cache.gc(older_than_days=0, dry_run=True)

        matching = [r for r in results if r["run_id"] == run_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["would_purge"], 1)
        self.assertTrue((directory / "cache" / "a.json").is_file())

    def test_gc_purges_a_run_older_than_the_cutoff(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        results = cache.gc(older_than_days=0, dry_run=False)

        matching = [r for r in results if r["run_id"] == run_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["purged"], 1)
        self.assertFalse((directory / "cache" / "a.json").is_file())

    def test_gc_respects_older_than_days_and_skips_a_fresh_run(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        results = cache.gc(older_than_days=7, dry_run=True)

        matching = [r for r in results if r["run_id"] == run_id]
        self.assertEqual(matching, [])
        self.assertTrue((directory / "cache" / "a.json").is_file())

    def test_gc_is_idempotent_across_repeated_calls(self):
        run_id, directory = self._make_run()
        cache.write_payload(directory, "a.json", b"aaa", source="yfinance")

        first = cache.gc(older_than_days=0, dry_run=False)
        second = cache.gc(older_than_days=0, dry_run=False)

        self.assertEqual(len([r for r in first if r["run_id"] == run_id]), 1)
        self.assertEqual([r for r in second if r["run_id"] == run_id], [])

    def test_gc_skips_a_run_with_nothing_to_purge(self):
        run_id, directory = self._make_run()
        # No cache payload written at all.

        results = cache.gc(older_than_days=0, dry_run=True)

        self.assertEqual([r for r in results if r["run_id"] == run_id], [])


if __name__ == "__main__":
    unittest.main()
