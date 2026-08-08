"""Cross-file checks a single schema cannot make.

pydantic validates each document in isolation. This module answers the
questions that need the whole run: do cited evidence IDs exist, do referenced
paths resolve inside the run, is anything claiming a trade was executed.

Follows the repo's existing validator style (see
`.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py`):
collect and return every problem as a list of strings rather than raising on
the first, so one pass reports everything a caller has to fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from . import audit as audit_module
from . import evidence as evidence_module
from . import manifest as manifest_module
from . import state as state_module
from .models import AgentOutput, DecisionProposal, Request, RunMetadata
from .paths import (
    METADATA_FILENAME,
    REQUEST_FILENAME,
    PathEscapeError,
    resolve_in_run,
)

# Any of these appearing as a key anywhere in an agent output or proposal means
# something is representing a placed order. The system is research-only, so
# their presence fails the run rather than being ignored.
FORBIDDEN_EXECUTION_KEYS = frozenset(
    {"order_id", "executed_at", "execution_id", "fill_price", "fill_quantity",
     "filled_at", "broker_order_id", "broker_account", "broker_reference"}
)
_FORBIDDEN_PREFIXES = ("broker_", "fill_")


def _walk_keys(node: Any):
    """Yield every mapping key at any depth, so a forbidden field cannot hide
    inside a nested findings/risks structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def find_execution_keys(payload: Any) -> list[str]:
    found: set[str] = set()
    for key in _walk_keys(payload):
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if lowered in FORBIDDEN_EXECUTION_KEYS or lowered.startswith(_FORBIDDEN_PREFIXES):
            found.add(key)
    return sorted(found)


def _load_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name}: unreadable or malformed JSON ({exc})"


def _load_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{path.name}: unreadable or malformed YAML ({exc})"


def validate_run(run_dir: Path) -> dict[str, Any]:
    """Full run check. Returns `{ok, errors, warnings, counts}`.

    Errors mean the run is not trustworthy; warnings mean it is incomplete but
    coherent (an unfinished run legitimately has no agent outputs yet).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not run_dir.is_dir():
        return {"ok": False, "errors": [f"run directory not found: {run_dir}"], "warnings": [], "counts": {}}

    # --- request
    request: Request | None = None
    request_path = run_dir / REQUEST_FILENAME
    if not request_path.is_file():
        errors.append(f"missing {REQUEST_FILENAME}")
    else:
        raw, load_error = _load_yaml(request_path)
        if load_error:
            errors.append(load_error)
        else:
            try:
                request = Request.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"{REQUEST_FILENAME}: {exc.error_count()} schema error(s): {exc}")

    # --- metadata
    metadata: RunMetadata | None = None
    metadata_path = run_dir / METADATA_FILENAME
    if not metadata_path.is_file():
        errors.append(f"missing {METADATA_FILENAME}")
    else:
        raw, load_error = _load_json(metadata_path)
        if load_error:
            errors.append(load_error)
        else:
            try:
                metadata = RunMetadata.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"{METADATA_FILENAME}: {exc.error_count()} schema error(s): {exc}")

    if request and metadata and request.run_id and request.run_id != metadata.run_id:
        errors.append(
            f"run_id mismatch: request says {request.run_id!r}, metadata says {metadata.run_id!r}"
        )

    # --- evidence registry
    malformed_evidence = evidence_module.count_malformed_lines(run_dir)
    if malformed_evidence:
        errors.append(f"evidence registry has {malformed_evidence} malformed line(s)")

    records = evidence_module.read_records(run_dir)
    seen_ids: set[str] = set()
    for index, record in enumerate(records):
        label = record.get("evidence_id") or f"line {index + 1}"
        evidence_id = record.get("evidence_id")
        if not evidence_id:
            errors.append(f"evidence {label}: missing evidence_id")
        elif evidence_id in seen_ids:
            errors.append(f"duplicate evidence_id: {evidence_id}")
        else:
            seen_ids.add(evidence_id)

        artifact_rel = record.get("artifact_path")
        if artifact_rel:
            try:
                resolved = resolve_in_run(run_dir, artifact_rel)
            except PathEscapeError as exc:
                errors.append(f"evidence {label}: {exc}")
            else:
                if not resolved.is_file():
                    errors.append(f"evidence {label}: artifact missing on disk ({artifact_rel})")
                elif record.get("content_hash"):
                    if evidence_module.content_hash(resolved) != record["content_hash"]:
                        errors.append(f"evidence {label}: content hash does not match artifact")
        elif record.get("status") == "available":
            errors.append(f"evidence {label}: status 'available' but no artifact_path")

    if not records:
        warnings.append("no evidence registered")

    # --- manifest
    manifest_raw = manifest_module.read(run_dir)
    if manifest_raw is None:
        warnings.append("no context manifest built yet")
    else:
        for entry in manifest_raw.get("evidence") or []:
            entry_id = entry.get("evidence_id") if isinstance(entry, dict) else None
            if entry_id and entry_id not in seen_ids:
                errors.append(f"manifest cites unregistered evidence_id: {entry_id}")
        for key in ("input_paths", "calculation_paths"):
            for rel in manifest_raw.get(key) or []:
                try:
                    resolve_in_run(run_dir, rel)
                except PathEscapeError as exc:
                    errors.append(f"manifest {key}: {exc}")

    # --- agent outputs
    outputs_dir = run_dir / "agent_outputs"
    output_count = 0
    if outputs_dir.is_dir():
        for path in sorted(outputs_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".json", ".yaml", ".yml"):
                continue
            output_count += 1
            raw, load_error = (
                _load_json(path) if path.suffix.lower() == ".json" else _load_yaml(path)
            )
            if load_error:
                errors.append(load_error)
                continue
            try:
                output = AgentOutput.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"agent_outputs/{path.name}: {exc.error_count()} schema error(s): {exc}")
                continue
            for cited in output.evidence_ids_used:
                if cited not in seen_ids:
                    errors.append(
                        f"agent_outputs/{path.name}: cites unregistered evidence_id {cited}"
                    )
            for forbidden in find_execution_keys(raw):
                errors.append(
                    f"agent_outputs/{path.name}: forbidden trade-execution field {forbidden!r}"
                )

    # --- decision proposals
    final_dir = run_dir / "final"
    if final_dir.is_dir():
        for path in sorted(final_dir.glob("*.json")):
            raw, load_error = _load_json(path)
            if load_error:
                errors.append(load_error)
                continue
            if not isinstance(raw, dict) or "proposed_action" not in raw:
                continue
            try:
                DecisionProposal.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"final/{path.name}: {exc.error_count()} schema error(s): {exc}")
            for forbidden in find_execution_keys(raw):
                errors.append(f"final/{path.name}: forbidden trade-execution field {forbidden!r}")

    # --- audit log
    malformed_audit = audit_module.count_malformed_lines(run_dir)
    if malformed_audit:
        warnings.append(f"audit log has {malformed_audit} malformed line(s)")
    events = audit_module.read_events(run_dir)
    if not events:
        errors.append("audit log is empty; every run must record run_created")

    if metadata and metadata.status in state_module.TERMINAL_STATUSES and not records:
        warnings.append(f"run reached {metadata.status} with no evidence registered")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "evidence": len(records),
            "agent_outputs": output_count,
            "audit_events": len(events),
        },
    }
