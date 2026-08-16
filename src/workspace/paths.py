"""Run-directory naming and the single path-traversal chokepoint.

Everything that turns caller-supplied text into a filesystem location goes
through this module: run IDs are built here, and every relative path a
manifest or evidence record names is resolved here. Keeping both in one place
means there is exactly one function to audit for traversal safety
(`resolve_in_run`) rather than a guard duplicated at each call site.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import config

RUN_ID_TIMESTAMP_FORMAT = "%Y-%m-%dT%H%M%SZ"

# Directories created inside every run. `tmp/` and `cache/` are the two
# disposable ones -- see `docs/architecture/run_workspace.md`'s retention
# section. `tmp/` holds agent drafts, purged only at archive time; `cache/`
# holds bulk fetched payloads (raw price history, financials) a data-pull
# skill deposits for the life of the run and that `workspace.cache.purge`
# clears the moment the run reaches a terminal status -- well before archive.
RUN_SUBDIRS = ("inputs", "evidence", "calculations", "agent_outputs", "final", "tmp", "cache")

REQUEST_FILENAME = "request.yaml"
METADATA_FILENAME = "run_metadata.json"
MANIFEST_FILENAME = "context_manifest.yaml"
AUDIT_FILENAME = "audit_log.jsonl"
EVIDENCE_REGISTRY_RELPATH = "evidence/sources.jsonl"
# Written by `workspace.cache.purge` in place of the deleted payloads --
# retains {filename, sha256, bytes, source, fetched_at, purged_at} per file
# so provenance survives even though the bytes do not. Never registered as
# evidence (see `workspace/cache.py`'s module docstring for why).
CACHE_MANIFEST_RELPATH = "cache/cache_manifest.json"

# Run IDs become directory names, so the character class is deliberately
# narrower than the filesystem allows: no spaces, no path separators, nothing
# Windows reserves. Anything else in a user-supplied mode/subject is collapsed.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_SEGMENT_MAX_LEN = 40


class WorkspaceError(Exception):
    """Base class for every workspace failure that is the caller's fault."""


class PathEscapeError(WorkspaceError):
    """A supplied path resolved outside the run directory it must stay in."""


def sanitize_segment(value: str | None, *, fallback: str = "na") -> str:
    """Collapse arbitrary text into one safe run-ID segment.

    Used for the caller-controlled `mode` and `subject` parts of a run ID.
    Returns `fallback` rather than an empty string so a run ID never contains
    a doubled separator that would make it ambiguous to parse back apart.
    """
    if not value:
        return fallback
    cleaned = _UNSAFE_CHARS.sub("-", str(value)).strip("-._")
    cleaned = cleaned[:_SEGMENT_MAX_LEN].strip("-._")
    return cleaned or fallback


def generate_run_id(mode: str | None, subject: str | None, *, now: datetime | None = None) -> str:
    """Build a collision-resistant, human-readable run ID.

    Shape: `YYYY-MM-DDTHHMMSSZ_<mode>_<subject>_<short>`. The timestamp sorts
    lexicographically, the mode and subject make a directory listing readable
    without opening anything, and the random suffix keeps two runs created in
    the same second from colliding. Creation still refuses to overwrite an
    existing directory -- the suffix reduces collisions, it does not license
    ignoring them.
    """
    moment = now or datetime.now(timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime(RUN_ID_TIMESTAMP_FORMAT)
    return f"{stamp}_{sanitize_segment(mode)}_{sanitize_segment(subject)}_{secrets.token_hex(3)}"


def is_valid_run_id(run_id: str) -> bool:
    """Reject anything that could not have come from `generate_run_id`.

    Called before a run ID is joined onto the runs root, so a caller cannot
    reach a sibling directory by passing `../other-run` as an ID.
    """
    if not run_id or len(run_id) > 200:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", run_id)) and not run_id.startswith(".")


def runs_root() -> Path:
    return config.WORKSPACE_RUNS_FOLDER


def archive_root() -> Path:
    return config.WORKSPACE_ARCHIVE_FOLDER


def run_dir(run_id: str, *, root: Path | None = None) -> Path:
    """Locate one run's directory, refusing IDs that are not self-contained."""
    if not is_valid_run_id(run_id):
        raise WorkspaceError(f"invalid run_id: {run_id!r}")
    return (root or runs_root()) / run_id


def resolve_in_run(base: Path, relative: str | Path) -> Path:
    """Resolve `relative` against `base`, guaranteeing the result stays inside.

    The single traversal chokepoint. Absolute paths are rejected outright
    rather than silently reinterpreted, and resolution happens before the
    containment check so symlinks and `..` segments cannot smuggle the result
    out of the run. `base` itself is allowed (a manifest may reference the run
    root); anything above it is not.
    """
    candidate = Path(relative)
    if candidate.is_absolute() or (candidate.drive or candidate.root):
        raise PathEscapeError(f"path must be relative to the run directory: {relative!r}")

    base_resolved = base.resolve()
    resolved = (base_resolved / candidate).resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise PathEscapeError(f"path escapes the run directory: {relative!r}")
    return resolved


def relative_to_run(base: Path, target: Path) -> str:
    """Express `target` as a run-relative POSIX path for storage in artifacts.

    Artifacts record relative paths so a run stays valid after it is archived
    (which moves the directory) or copied to another machine.
    """
    resolved = target.resolve()
    base_resolved = base.resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise PathEscapeError(f"path is outside the run directory: {target}")
    return resolved.relative_to(base_resolved).as_posix()


def archive_dir(run_id: str, *, created_at: datetime, root: Path | None = None) -> Path:
    """Date-bucketed archive location, so the archive stays browsable at scale."""
    if not is_valid_run_id(run_id):
        raise WorkspaceError(f"invalid run_id: {run_id!r}")
    bucket = created_at.astimezone(timezone.utc).strftime("%Y-%m")
    return (root or archive_root()) / bucket / run_id


def utc_now_iso() -> str:
    """One timestamp format for every artifact this package writes."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
