"""Background job runner for dashboard-initiated actions.

Action endpoints must never block a request on a pipeline run or a
classification pass, so they submit work here and return a job the frontend
polls. Two constraints shape the design:

* **One writer at a time.** DuckDB allows a single read-write process, and the
  API already holds a connection. A single-worker executor plus the
  single-flight rule below means only one action ever touches the database.
* **In-process state.** Jobs live in memory, so the API must run as a single
  uvicorn worker (documented in docs/architecture/dashboard_api.md). This is a
  single-user local dashboard; a durable queue would be more machinery than the
  problem needs.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | succeeded | failed
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    detail: str | None = None
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobBusyError(RuntimeError):
    """Raised when a job is submitted while another is still running."""

    def __init__(self, active: Job):
        super().__init__(f"A '{active.kind}' job is already running.")
        self.active = active


class JobRunner:
    """Runs one action at a time and keeps a bounded history of results."""

    def __init__(self, history: int = 20):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard-action")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._history = history

    def active(self) -> Job | None:
        with self._lock:
            return next(
                (self._jobs[i] for i in reversed(self._order) if self._jobs[i].status in {"queued", "running"}),
                None,
            )

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order)]

    def submit(self, kind: str, work: Callable[[], Any]) -> Job:
        """Queue `work`, rejecting the call when another job is in flight."""
        with self._lock:
            running = next(
                (self._jobs[i] for i in reversed(self._order) if self._jobs[i].status in {"queued", "running"}),
                None,
            )
            if running is not None:
                raise JobBusyError(running)
            job = Job(id=uuid.uuid4().hex, kind=kind)
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Trim completed history, never the job just created.
            while len(self._order) > self._history:
                evicted = self._order.pop(0)
                self._jobs.pop(evicted, None)
        self._executor.submit(self._run, job, work)
        return job

    def _run(self, job: Job, work: Callable[[], Any]) -> None:
        with self._lock:
            job.status = "running"
            job.started_at = _now()
        try:
            result = work()
        except Exception as exc:  # Surfaced to the UI as a failed job, not a 500.
            with self._lock:
                job.status = "failed"
                job.detail = f"{type(exc).__name__}: {exc}"
                job.finished_at = _now()
            return
        with self._lock:
            job.status = "succeeded"
            job.result = result
            job.finished_at = _now()
