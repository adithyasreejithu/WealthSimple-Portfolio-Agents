"""Append-only audit log for one run.

JSON Lines, one event per line, opened in append mode and never rewritten:
the log is the record of what happened, so an operation that could edit an
earlier line would defeat its purpose. Readers tolerate a malformed trailing
line (a run interrupted mid-write) rather than refusing to load the history.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .paths import AUDIT_FILENAME, utc_now_iso

# Keys that must never reach the log, whatever a caller passes in `details`.
# The audit trail is meant to be shareable evidence that a run behaved; it is
# not a place to accumulate secrets or whole model prompts.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {"password", "token", "secret", "api_key", "apikey", "credential", "credentials",
     "prompt", "system_prompt", "messages", "authorization", "cookie"}
)


def new_event_id() -> str:
    return f"evt_{secrets.token_hex(8)}"


def _scrub(details: dict[str, Any] | None) -> dict[str, Any]:
    """Drop forbidden keys instead of redacting them.

    A `"password": "***"` entry still tells a reader a password was involved
    and invites someone to log the real one "just this once"; omitting the key
    keeps the log honest about what it is for.
    """
    if not details:
        return {}
    return {
        key: value
        for key, value in details.items()
        if key.lower() not in _FORBIDDEN_DETAIL_KEYS
    }


def append_event(
    run_dir: Path,
    *,
    run_id: str,
    event: str,
    actor: str = "workflow_cli",
    status: str = "success",
    artifact: str | None = None,
    evidence_id: str | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Append one event and return it (callers log and assert on the record)."""
    record: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "event_id": new_event_id(),
        "run_id": run_id,
        "event": event,
        "actor": actor,
        "status": status,
    }
    if artifact is not None:
        record["artifact"] = artifact
    if evidence_id is not None:
        record["evidence_id"] = evidence_id
    scrubbed = _scrub(details)
    if scrubbed:
        record["details"] = scrubbed
    if error is not None:
        record["error"] = error

    path = run_dir / AUDIT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    """Load the run's history, skipping blank and unparseable lines.

    A truncated final line means the process died mid-append; that should not
    make the whole run unreadable, so it is skipped rather than raised on.
    `validate` reports the count separately.
    """
    path = run_dir / AUDIT_FILENAME
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def count_malformed_lines(run_dir: Path) -> int:
    """How many audit lines could not be parsed -- surfaced by `validate`."""
    path = run_dir / AUDIT_FILENAME
    if not path.is_file():
        return 0
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(parsed, dict):
            malformed += 1
    return malformed
