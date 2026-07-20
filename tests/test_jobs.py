"""Tests for the dashboard action job runner (dashboard/api/jobs.py)."""

import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (REPO_ROOT / "src", REPO_ROOT / "dashboard" / "api"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from jobs import JobBusyError, JobRunner  # noqa: E402


class JobRunnerTest(unittest.TestCase):
    def setUp(self):
        self.runner = JobRunner()

    def _wait(self, job, timeout=5.0):
        finished = threading.Event()

        def poll():
            while self.runner.get(job.id).status in {"queued", "running"}:
                pass
            finished.set()

        watcher = threading.Thread(target=poll, daemon=True)
        watcher.start()
        self.assertTrue(finished.wait(timeout), "job did not finish in time")
        return self.runner.get(job.id)

    def test_successful_job_records_its_result(self):
        job = self.runner.submit("classify", lambda: {"rows": 12})

        finished = self._wait(job)

        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result, {"rows": 12})
        self.assertIsNotNone(finished.started_at)
        self.assertIsNotNone(finished.finished_at)
        self.assertIsNone(finished.detail)

    def test_failing_job_is_captured_rather_than_raised(self):
        def boom():
            raise RuntimeError("pipeline exited 1")

        job = self.runner.submit("refresh", boom)

        finished = self._wait(job)

        self.assertEqual(finished.status, "failed")
        self.assertIn("pipeline exited 1", finished.detail)
        self.assertIsNone(finished.result)

    def test_only_one_job_runs_at_a_time(self):
        release = threading.Event()
        first = self.runner.submit("refresh", release.wait)

        # A second submission while the first is in flight is refused, which is
        # what keeps two writers off the DuckDB file.
        with self.assertRaises(JobBusyError) as raised:
            self.runner.submit("classify", lambda: None)
        self.assertEqual(raised.exception.active.id, first.id)

        release.set()
        self._wait(first)

        # Once it drains, the next job is accepted.
        second = self.runner.submit("classify", lambda: "ok")
        self.assertEqual(self._wait(second).status, "succeeded")

    def test_active_is_none_once_work_drains(self):
        job = self.runner.submit("classify", lambda: "ok")
        self._wait(job)

        self.assertIsNone(self.runner.active())

    def test_recent_returns_newest_first_and_is_bounded(self):
        runner = JobRunner(history=3)
        jobs = []
        for index in range(5):
            job = runner.submit("classify", lambda i=index: i)
            jobs.append(job)
            while runner.get(job.id) and runner.get(job.id).status in {"queued", "running"}:
                pass

        recent = runner.recent()
        self.assertEqual(len(recent), 3)
        self.assertEqual([j.id for j in recent], [jobs[4].id, jobs[3].id, jobs[2].id])
        # Evicted jobs are gone entirely, not just hidden from the listing.
        self.assertIsNone(runner.get(jobs[0].id))

    def test_unknown_job_id_returns_none(self):
        self.assertIsNone(self.runner.get("nope"))


if __name__ == "__main__":
    unittest.main()
