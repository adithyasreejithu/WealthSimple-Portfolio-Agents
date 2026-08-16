"""Run-scoped ephemeral storage for bulk fetched payloads.

`cache/` holds raw data a data-pull skill fetched but does not need to keep
once the run finishes -- full price history, financial statements, options
chains. It exists because `evidence/` never shrinks: `evidence.register`
computes a content hash and `validation.validate_run` re-verifies it forever
(`validation.py`'s evidence checks), so a registered artifact can never be
deleted without turning every future `run validate` on that run into a
permanent, unrecoverable error. Runs themselves are never deleted or
auto-archived either (`run.archive_run`'s docstring). Left unmanaged, every
bulk payload a data-pull skill ever fetched would sit in `workspace/runs/`
at full size indefinitely.

**A cache payload is never registered as evidence.** That is the whole
point of this module existing separately from `evidence/`: `write_payload`
stores bytes with no registry entry, so there is nothing for `purge` to
break by deleting them later.

`cache/` is deliberately the second disposable subdirectory alongside
`tmp/` (see `paths.RUN_SUBDIRS`), but with a different lifecycle: `tmp/`
only clears at archive time and can sit populated for weeks while a human
reviews a draft (`run.archive_run`), whereas `purge` runs automatically the
moment a run reaches a terminal status (`run.set_status`) -- see
`docs/architecture/run_workspace.md` for the full lifecycle and the
non-reproducibility trade this implies: a purged run can no longer be
re-derived from its original inputs, only re-fetched at today's prices. The
retained `cache_manifest.json` (one entry per deleted file: `filename`,
`sha256`, `bytes`, `source`, `fetched_at`, `purged_at`) makes that fact
detectable, not reversible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import audit as audit_module
from .paths import CACHE_MANIFEST_RELPATH, resolve_in_run, utc_now_iso

_INDEX_FILENAME = ".pending_index.json"
_MANIFEST_FILENAME = Path(CACHE_MANIFEST_RELPATH).name
_HASH_CHUNK_BYTES = 1024 * 1024
_RESERVED_FILENAMES = frozenset({_MANIFEST_FILENAME, _INDEX_FILENAME})


def cache_dir(run_dir: Path) -> Path:
    return run_dir / "cache"


def _manifest_path(run_dir: Path) -> Path:
    return run_dir / CACHE_MANIFEST_RELPATH


def _index_path(run_dir: Path) -> Path:
    return cache_dir(run_dir) / _INDEX_FILENAME


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_manifest(run_dir: Path) -> list[dict[str, Any]]:
    path = _manifest_path(run_dir)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def write_payload(run_dir: Path, name: str, data: bytes | str, *, source: str) -> Path:
    """Write one bulk payload into the run's `cache/` directory.

    `name` must be a plain filename, not a path -- callers own uniqueness
    (e.g. `<TICKER>-history.json`); this only guards against a caller
    accidentally nesting a subdirectory or escaping the run. `source` (e.g.
    `"yfinance"`) is recorded in a small sidecar index alongside a fetch
    timestamp, so `purge` can carry both into the retained manifest without
    the payload file itself needing any embedded metadata.
    """
    if name in _RESERVED_FILENAMES or Path(name).name != name:
        raise ValueError(f"cache payload name must be a plain filename, not a path: {name!r}")

    directory = cache_dir(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = resolve_in_run(run_dir, f"cache/{name}")
    payload_bytes = data.encode("utf-8") if isinstance(data, str) else data
    destination.write_bytes(payload_bytes)

    index_path = _index_path(run_dir)
    index = _read_json_object(index_path)
    index[name] = {"source": source, "fetched_at": utc_now_iso()}
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return destination


def purge(run_dir: Path, run_id: str) -> dict[str, Any]:
    """Delete every cached payload, retaining its provenance in the manifest.

    Idempotent by design: a `cache/` directory with nothing left to purge
    (already purged, or never populated this run) returns cleanly with
    `purged: 0` and appends no audit event. This matters because `purge` is
    called from every terminal `set_status` transition (`run.set_status`) --
    a run whose data-pull skills never wrote to `cache/` at all must not
    accumulate a `cache_purged` event on every status change.

    Manifest entries accumulate across multiple purges of the same run
    (keyed by filename) rather than being overwritten, in case a run somehow
    re-enters `in_progress` after a terminal state and its skills populate
    `cache/` again before the next purge.
    """
    directory = cache_dir(run_dir)
    existing_manifest = {
        entry["filename"]: entry for entry in _read_manifest(run_dir) if entry.get("filename")
    }

    if not directory.is_dir():
        return {"purged": 0, "manifest_entries": len(existing_manifest)}

    index = _read_json_object(_index_path(run_dir))
    payload_files = [
        item for item in directory.iterdir()
        if item.is_file() and item.name not in _RESERVED_FILENAMES
    ]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_entries: list[dict[str, Any]] = []
    for item in payload_files:
        meta = index.get(item.name, {})
        new_entries.append(
            {
                "filename": item.name,
                "sha256": _sha256_of(item),
                "bytes": item.stat().st_size,
                "source": meta.get("source"),
                "fetched_at": meta.get("fetched_at"),
                "purged_at": now,
            }
        )

    if not new_entries:
        return {"purged": 0, "manifest_entries": len(existing_manifest)}

    for item in payload_files:
        item.unlink()
    index_path = _index_path(run_dir)
    if index_path.is_file():
        index_path.unlink()

    for entry in new_entries:
        existing_manifest[entry["filename"]] = entry
    merged = list(existing_manifest.values())
    directory.mkdir(parents=True, exist_ok=True)
    _manifest_path(run_dir).write_text(json.dumps(merged, indent=2), encoding="utf-8")

    total_bytes = sum(entry["bytes"] for entry in new_entries)
    audit_module.append_event(
        run_dir,
        run_id=run_id,
        event="cache_purged",
        actor="workspace_cli",
        details={"purged": len(new_entries), "total_bytes": total_bytes},
    )
    return {"purged": len(new_entries), "manifest_entries": len(merged)}


def gc(*, older_than_days: int = 7, root: Path | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
    """Sweep every run under `root` (default: the live runs directory) and
    purge the cache of any run whose metadata is older than `older_than_days`
    and still has un-purged payloads.

    Reclaims a run that crashed before ever reaching a terminal `set_status`
    -- the only other place `purge` runs. `purge` is idempotent, so a run
    already purged (or one whose skills never wrote to `cache/`) is skipped
    from the report entirely rather than padding it with no-op entries.
    `dry_run` reports what would be purged without deleting anything, so it
    is safe to run against the real `workspace/runs/` to see its effect
    first.
    """
    from .paths import runs_root
    from . import run as run_module  # local import: avoids a cache<->run import cycle

    runs_dir = root or runs_root()
    if not runs_dir.is_dir():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    results: list[dict[str, Any]] = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            metadata = run_module.read_metadata(entry)
        except Exception:
            continue
        created = metadata.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created > cutoff:
            continue

        directory = cache_dir(entry)
        if not directory.is_dir():
            continue
        live_files = [
            item for item in directory.iterdir()
            if item.is_file() and item.name not in _RESERVED_FILENAMES
        ]
        if not live_files:
            continue

        if dry_run:
            results.append(
                {"run_id": metadata.run_id, "status": metadata.status, "would_purge": len(live_files)}
            )
            continue

        outcome = purge(entry, metadata.run_id)
        if outcome["purged"]:
            results.append({"run_id": metadata.run_id, "status": metadata.status, **outcome})
    return results
