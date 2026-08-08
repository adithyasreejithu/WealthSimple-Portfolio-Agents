"""Builds `context_manifest.yaml` from the run's current state.

The manifest is the contract handed to a receiving stage: these inputs, this
evidence, these gaps, these restrictions. It is regenerated rather than
edited, so it always reflects what the run actually holds -- a manifest that
drifted from the registry would be worse than none, because a stage would
trust it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import evidence as evidence_module
from .models import ContextManifest, ManifestEvidence, Request
from .paths import MANIFEST_FILENAME, relative_to_run, utc_now_iso

# Evidence in these states is listed but must not be treated as usable data.
_UNUSABLE_STATUSES = frozenset({"missing", "pending", "invalid"})


def _relative_files(run_dir: Path, subdir: str) -> list[str]:
    directory = run_dir / subdir
    if not directory.is_dir():
        return []
    return sorted(
        relative_to_run(run_dir, path)
        for path in directory.rglob("*")
        if path.is_file()
    )


def build(
    run_dir: Path,
    request: Request,
    *,
    run_id: str,
    target_stage: str | None = None,
    prior_thesis_path: str | None = None,
) -> ContextManifest:
    """Assemble the manifest from what is on disk right now."""
    records = evidence_module.read_records(run_dir)
    entries = [
        ManifestEvidence(
            evidence_id=record.get("evidence_id", ""),
            evidence_type=record.get("evidence_type", "unknown"),
            status=record.get("status", "pending"),
            path=record.get("artifact_path"),
        )
        for record in records
        if record.get("evidence_id")
    ]

    missing = evidence_module.missing_summary(run_dir)
    if not records:
        missing.append("no evidence registered for this run")

    usable = [entry for entry in entries if entry.status not in _UNUSABLE_STATUSES]
    # `ok` means "a downstream stage can proceed": some usable evidence exists
    # and nothing is outstanding. Anything else stays `incomplete`, which is
    # the honest default -- the manifest never claims completeness it cannot
    # demonstrate from the registry.
    validation_status = "ok" if usable and not missing else "incomplete"

    return ContextManifest(
        run_id=run_id,
        task=request.request.question,
        generated_at=utc_now_iso(),
        target_stage=target_stage,
        input_paths=_relative_files(run_dir, "inputs"),
        evidence=entries,
        calculation_paths=_relative_files(run_dir, "calculations"),
        prior_thesis_path=prior_thesis_path,
        required_outputs=list(request.required_analysis),
        restrictions=request.restrictions,
        missing_information=missing,
        validation_status=validation_status,
    )


def write(run_dir: Path, manifest: ContextManifest) -> Path:
    path = run_dir / MANIFEST_FILENAME
    payload = manifest.model_dump(mode="json", exclude_none=False)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def read(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / MANIFEST_FILENAME
    if not path.is_file():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None
