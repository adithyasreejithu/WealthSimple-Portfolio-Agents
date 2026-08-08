"""The run's evidence registry (`evidence/sources.jsonl`).

Append-only, like the audit log. The central rule this module enforces is that
a gap is recorded, never filled: registering evidence as `missing` is a
first-class operation with the same weight as registering a file, so a later
stage can tell "we looked and it was not there" from "nobody looked."
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import EvidenceRecord
from .paths import EVIDENCE_REGISTRY_RELPATH, WorkspaceError, relative_to_run, resolve_in_run

_HASH_CHUNK_BYTES = 1024 * 1024
_EVIDENCE_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


class EvidenceError(WorkspaceError):
    """A registration that would corrupt the registry (duplicate, bad path)."""


def new_evidence_id() -> str:
    return f"ev_{secrets.token_hex(6)}"


def registry_path(run_dir: Path) -> Path:
    return run_dir / EVIDENCE_REGISTRY_RELPATH


def content_hash(path: Path) -> str:
    """sha256 of a file's bytes, streamed so a large bundle is not held in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_records(run_dir: Path) -> list[dict[str, Any]]:
    path = registry_path(run_dir)
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def known_evidence_ids(run_dir: Path) -> set[str]:
    """The set an agent output's `evidence_ids_used` is checked against."""
    return {record.get("evidence_id") for record in read_records(run_dir) if record.get("evidence_id")}


def count_malformed_lines(run_dir: Path) -> int:
    path = registry_path(run_dir)
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


def register(
    run_dir: Path,
    *,
    run_id: str,
    evidence_type: str,
    source_name: str,
    status: str,
    artifact: Path | None = None,
    evidence_id: str | None = None,
    source_url: str | None = None,
    retrieved_at: str | None = None,
    as_of: str | None = None,
    freshness: str | None = None,
    collection_method: str | None = None,
    notes: list[str] | None = None,
    warnings: list[str] | None = None,
) -> EvidenceRecord:
    """Append one evidence record.

    `artifact`, when given, must already sit inside the run directory -- the
    registry stores a run-relative path so the record survives archiving, and
    a file outside the run could not be moved with it. The content hash is
    computed here rather than trusted from the caller, so the registry's claim
    about a file is always one this process verified.
    """
    if artifact is not None:
        resolved = resolve_in_run(run_dir, relative_to_run(run_dir, artifact))
        if not resolved.is_file():
            raise EvidenceError(f"artifact does not exist: {artifact}")
        artifact_rel: str | None = relative_to_run(run_dir, resolved)
        digest: str | None = content_hash(resolved)
    else:
        artifact_rel = None
        digest = None

    chosen_id = evidence_id or new_evidence_id()
    if not _EVIDENCE_ID_PATTERN.fullmatch(chosen_id):
        raise EvidenceError(f"invalid evidence_id: {chosen_id!r}")

    existing = read_records(run_dir)
    if any(record.get("evidence_id") == chosen_id for record in existing):
        raise EvidenceError(f"evidence_id already registered: {chosen_id}")
    # Re-registering the same artifact under a fresh ID would leave two rows
    # claiming the same bytes, and a manifest could then cite either -- so the
    # path is treated as a second identity for the record.
    if artifact_rel is not None and any(
        record.get("artifact_path") == artifact_rel for record in existing
    ):
        raise EvidenceError(f"artifact already registered: {artifact_rel}")

    try:
        record = EvidenceRecord(
            evidence_id=chosen_id,
            run_id=run_id,
            evidence_type=evidence_type,
            source_name=source_name,
            source_url=source_url,
            artifact_path=artifact_rel,
            retrieved_at=retrieved_at,
            as_of=as_of,
            status=status,
            freshness=freshness,
            content_hash=digest,
            collection_method=collection_method,
            notes=list(notes or []),
            warnings=list(warnings or []),
        )
    except ValidationError as exc:
        raise EvidenceError(f"invalid evidence record: {exc}") from exc

    path = registry_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json(exclude_none=False) + "\n")
    return record


def missing_summary(run_dir: Path) -> list[str]:
    """Human-readable lines for every gap, used to fill the manifest's
    `missing_information` so a downstream stage sees the gaps without having
    to parse the registry itself."""
    lines: list[str] = []
    for record in read_records(run_dir):
        status = record.get("status")
        if status in ("missing", "pending", "stale", "invalid", "partial"):
            lines.append(
                f"{record.get('evidence_type', 'unknown')} from "
                f"{record.get('source_name', 'unknown')}: {status}"
            )
    return lines
