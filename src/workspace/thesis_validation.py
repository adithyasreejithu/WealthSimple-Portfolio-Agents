"""Deterministic validator for `investment-thesis.v1` artifacts.

Mirrors the split `models.py` already documents for the rest of this
package: `analysis_models.py` enforces everything a single document can
check about itself (shape, enums, forbidden fields, internal
section/evidence consistency); this module adds the checks that need the
run's other files -- does a cited evidence_id actually exist and is its
artifact unmutated, does section coverage match this run's
`analysis-scope.v1`, is `thesis_confidence` within the TRACE/gate-driven cap
`investment-analysis-policy.yml`'s `confidence_caps` describes.

Follows the repo's existing validator style (`validation.py`,
`.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py`):
collect every problem into a list rather than raising on the first, so one
pass reports everything a caller has to fix.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import audit as audit_module
from . import evidence as evidence_module
from .analysis_models import (
    CONFIDENCE_ORDER,
    POLICY_VERSION,
    TRACE_BLOCKING_THRESHOLD,
    TRACE_WARNING_THRESHOLD,
    AnalysisScope,
    EvidenceCompleteness,
    InvestmentThesis,
)
from .paths import PathEscapeError, relative_to_run, resolve_in_run, utc_now_iso


def check_evidence_citations(thesis: InvestmentThesis, run_dir: Path) -> list[str]:
    """Every evidence_id cited anywhere in the artifact must resolve to a
    registered, intact record in this run's `evidence/sources.jsonl`.

    Three distinct failures, each reported by name rather than collapsed
    into one generic message: the id was never registered (fabrication),
    the id resolves to a `missing`-status placeholder (citing a gap the run
    already recorded as empty), or the artifact on disk no longer matches
    the hash recorded at registration time (mutated since it was cited).
    """
    problems: list[str] = []
    records = {
        record.get("evidence_id"): record
        for record in evidence_module.read_records(run_dir)
        if record.get("evidence_id")
    }
    for evidence_id in sorted(thesis.cited_evidence_ids()):
        record = records.get(evidence_id)
        if record is None:
            problems.append(f"cites unregistered evidence_id: {evidence_id}")
            continue
        if record.get("status") == "missing":
            problems.append(f"cites evidence_id registered as missing (empty source): {evidence_id}")
            continue
        artifact_rel = record.get("artifact_path")
        if not artifact_rel:
            continue
        try:
            resolved = resolve_in_run(run_dir, artifact_rel)
        except PathEscapeError as exc:
            problems.append(f"evidence {evidence_id}: {exc}")
            continue
        if not resolved.is_file():
            problems.append(f"evidence {evidence_id}: artifact missing on disk ({artifact_rel})")
        elif record.get("content_hash") and evidence_module.content_hash(resolved) != record["content_hash"]:
            problems.append(
                f"evidence {evidence_id}: artifact content_hash no longer matches the registry "
                "(mutated since it was cited)"
            )
    return problems


def check_worksheet_ref(thesis: InvestmentThesis, run_dir: Path) -> list[str]:
    """`worksheet_ref` is a path+hash pair, not an `evidence_id` --
    `check_evidence_citations` cannot see it. This closes that gap: the
    referenced worksheet must resolve inside the run and its live sha256 must
    still equal `worksheet_ref.hash`, so a stale or hand-edited worksheet
    cannot be cited without detection (the whole point of "Python calculates,
    the analyst copies verbatim" -- see `investment_worksheet.py`)."""
    try:
        resolved = resolve_in_run(run_dir, thesis.worksheet_ref.path)
    except PathEscapeError as exc:
        return [f"worksheet_ref: {exc}"]
    if not resolved.is_file():
        return [f"worksheet_ref: artifact missing on disk ({thesis.worksheet_ref.path})"]
    live_hash = "sha256:" + evidence_module.content_hash(resolved)
    if live_hash != thesis.worksheet_ref.hash:
        return [
            f"worksheet_ref.hash no longer matches the worksheet on disk "
            f"(recorded {thesis.worksheet_ref.hash}, actual {live_hash})"
        ]
    return []


def check_section_scope(thesis: InvestmentThesis, scope: AnalysisScope) -> list[str]:
    """Section coverage must match the run's `analysis-scope.v1` exactly:
    every `evaluate_sections` entry is `changed` with narrative content,
    every `preserve_sections` entry carries no replacement narrative, every
    `not_applicable_sections` entry is marked as such and absent from
    `sections`.
    """
    problems: list[str] = []
    if thesis.security.ticker != scope.subject:
        problems.append(
            f"thesis subject {thesis.security.ticker!r} does not match scope subject {scope.subject!r}"
        )
    if thesis.run_id != scope.run_id:
        problems.append(f"thesis run_id {thesis.run_id!r} does not match scope run_id {scope.run_id!r}")

    for section in scope.evaluate_sections:
        state = thesis.section_states.get(section)
        if state != "changed":
            problems.append(f"scope requires {section} to be evaluated but section_states has {state!r}")
        if section not in thesis.sections:
            problems.append(f"scope requires {section} to be evaluated but sections has no entry for it")

    for section in scope.preserve_sections:
        state = thesis.section_states.get(section)
        if state not in ("unchanged", "not_evaluated"):
            problems.append(f"scope preserves {section} but section_states has {state!r}")
        if section in thesis.sections:
            problems.append(f"scope preserves {section} but sections has replacement narrative content for it")

    for section in scope.not_applicable_sections:
        state = thesis.section_states.get(section)
        if state != "not_applicable":
            problems.append(f"scope marks {section} not_applicable but section_states has {state!r}")
        if section in thesis.sections:
            problems.append(f"scope marks {section} not_applicable but sections has content for it")

    return problems


def confidence_cap(
    evidence_completeness_pct: float,
    *,
    required_domains: list[str] | None = None,
    domain_status: dict[str, EvidenceCompleteness] | None = None,
) -> str | None:
    """The tightest ceiling `investment-analysis-policy.yml`'s
    `confidence_caps` rules impose, or `None` if the analyst's own
    `thesis_confidence` stands unmodified.

    `domain_status` maps a required evidence domain to its TRACE outcome; a
    required domain absent from this mapping is an *unknown* gate, which
    caps at `low` exactly like one TRACE already graded `missing` --
    unknown is not evidence of completeness, it is evidence nobody checked.
    """
    if required_domains:
        statuses = domain_status or {}
        for domain in required_domains:
            status = statuses.get(domain)
            if status is None or status == "missing":
                return "low"
    if evidence_completeness_pct < TRACE_BLOCKING_THRESHOLD:
        return "low"
    if evidence_completeness_pct < TRACE_WARNING_THRESHOLD:
        return "medium"
    return None


def check_confidence_cap(
    thesis: InvestmentThesis,
    *,
    required_domains: list[str] | None = None,
    domain_status: dict[str, EvidenceCompleteness] | None = None,
) -> list[str]:
    cap = confidence_cap(
        thesis.conclusion.evidence_completeness_pct,
        required_domains=required_domains,
        domain_status=domain_status,
    )
    if cap is None:
        return []
    stated = thesis.conclusion.thesis_confidence
    if CONFIDENCE_ORDER[stated] > CONFIDENCE_ORDER[cap]:
        return [
            f"thesis_confidence {stated!r} exceeds the policy cap {cap!r} for this run "
            f"(evidence_completeness_pct={thesis.conclusion.evidence_completeness_pct})"
        ]
    return []


def validate_thesis(
    payload: dict[str, Any],
    *,
    run_dir: Path | None = None,
    scope: AnalysisScope | dict[str, Any] | None = None,
    required_domains: list[str] | None = None,
    domain_status: dict[str, EvidenceCompleteness] | None = None,
) -> dict[str, Any]:
    """Full check of one `investment-thesis.v1` artifact.

    Returns `{ok, status, errors, warnings, thesis}`. `status` follows
    `investment_thesis_schema.md` §11 (`invalid` if any error, else
    `valid_with_warnings` if any warning, else `valid`). `thesis` is the
    parsed `InvestmentThesis`, or `None` if the payload failed to parse at
    all -- schema and forbidden-field failures make every other check
    impossible to run, so they short-circuit here.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        thesis = InvestmentThesis.model_validate(payload)
    except ValidationError as exc:
        errors.append(f"schema: {exc.error_count()} error(s): {exc}")
        return {"ok": False, "status": "invalid", "errors": errors, "warnings": warnings, "thesis": None}

    if run_dir is not None:
        errors.extend(check_evidence_citations(thesis, run_dir))
        errors.extend(check_worksheet_ref(thesis, run_dir))
    else:
        warnings.append("evidence citation check skipped: no run_dir supplied")

    resolved_scope: AnalysisScope | None = None
    if scope is not None:
        if isinstance(scope, AnalysisScope):
            resolved_scope = scope
        else:
            try:
                resolved_scope = AnalysisScope.model_validate(scope)
            except ValidationError as exc:
                errors.append(f"scope schema: {exc.error_count()} error(s): {exc}")
        if resolved_scope is not None:
            errors.extend(check_section_scope(thesis, resolved_scope))
            if required_domains is None:
                required_domains = resolved_scope.required_evidence_domains
    else:
        warnings.append("section scope check skipped: no scope supplied")

    errors.extend(
        check_confidence_cap(thesis, required_domains=required_domains, domain_status=domain_status)
    )

    status = "invalid" if errors else ("valid_with_warnings" if warnings else "valid")
    return {"ok": not errors, "status": status, "errors": errors, "warnings": warnings, "thesis": thesis}


# --- Phase 4: CLI-facing entry points -------------------------------------
#
# Phase 4's Investment Analyst agent never imports this module directly (it
# only has Bash/Read/Write) -- these two functions are the entire bodies of
# `run check-thesis` / `run save-thesis` in `cli.py`, which stays a thin
# argparse wrapper per its own docstring invariant.


def scope_from_worksheet_ref(
    run_dir: Path, payload: Any
) -> tuple[dict[str, Any] | None, dict[str, EvidenceCompleteness] | None, str | None]:
    """Best-effort: resolve `worksheet_ref.path` out of a raw (pre-validation)
    draft and read its `request_and_scope` / `evidence_health.domain_status`
    blocks, so a caller doesn't have to re-derive `AnalysisScope` by hand --
    the worksheet already carries `AnalysisScope.model_dump(mode="json",
    by_alias=True)` verbatim (`investment_worksheet.build_worksheet`).
    Returns `(scope, domain_status, warning)`; `warning` is set rather than
    raised when `worksheet_ref` is absent, unreadable, or malformed, so a
    badly-formed draft still gets useful schema/evidence feedback instead of
    an opaque crash here."""
    ref = payload.get("worksheet_ref") if isinstance(payload, dict) else None
    path = ref.get("path") if isinstance(ref, dict) else None
    if not path:
        return None, None, "no worksheet_ref.path in payload; scope/confidence-cap checks skipped"
    try:
        resolved = resolve_in_run(run_dir, path)
    except PathEscapeError as exc:
        return None, None, f"worksheet_ref.path: {exc}"
    if not resolved.is_file():
        return None, None, f"worksheet_ref.path does not resolve to a file: {path}"
    try:
        worksheet = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"worksheet_ref.path unreadable: {exc}"
    scope = worksheet.get("request_and_scope")
    domain_status = (worksheet.get("evidence_health") or {}).get("domain_status")
    if not isinstance(scope, dict):
        return None, domain_status, "worksheet has no request_and_scope block; scope check skipped"
    return scope, domain_status, None


def check_thesis_draft(payload: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """Side-effect-free validation of a draft `investment-thesis.v1` payload
    -- the entire body of `run check-thesis`, the iterate-until-valid loop an
    agent repeats while fixing a draft (mirrors
    `evaluate-stock-decision`/`validate_recommendation.py --precompute-only`).
    Writes nothing, registers nothing, appends no audit event."""
    scope, domain_status, warning = scope_from_worksheet_ref(run_dir, payload)
    result = validate_thesis(payload, run_dir=run_dir, scope=scope, domain_status=domain_status)
    result.pop("thesis", None)
    if warning:
        result["warnings"] = [*result["warnings"], warning]
        if result["ok"]:
            result["status"] = "valid_with_warnings"
    return result


def save_thesis(
    payload: dict[str, Any],
    *,
    run_dir: Path,
    run_id: str,
    ticker: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The entire body of `run save-thesis`. Re-validates independently --
    never trusts that a prior `check_thesis_draft` call saw the same bytes.

    On failure: writes nothing, registers nothing; appends one `validated`
    (failure) audit event so the attempt is on the record.

    On success: authoritatively overwrites `policy_version`, `artifact_id`,
    and `validation{}` (never trusts the agent's typed placeholders -- mirrors
    `stock-analyst.md`'s "the rubric verdict stands... never hand-tune it"),
    writes the artifact to `agent_outputs/`, registers it as
    `investment_thesis` evidence, and appends `analyst_drafted` + `validated`
    (success) audit events.
    """
    scope, domain_status, warning = scope_from_worksheet_ref(run_dir, payload)
    result = validate_thesis(payload, run_dir=run_dir, scope=scope, domain_status=domain_status)
    thesis = result.pop("thesis", None)
    if warning:
        result["warnings"] = [*result["warnings"], warning]
        if result["ok"]:
            result["status"] = "valid_with_warnings"

    if result["ok"] and thesis is not None and thesis.security.ticker != ticker:
        result["ok"] = False
        result["status"] = "invalid"
        result["errors"] = [
            *result["errors"],
            f"thesis security.ticker {thesis.security.ticker!r} does not match --ticker {ticker!r}",
        ]

    if not result["ok"] or thesis is None:
        audit_module.append_event(
            run_dir, run_id=run_id, event="validated", actor="investment_analyst", status="failure",
            details={
                "ticker": ticker, "thesis_status": result["status"],
                "error_count": len(result["errors"]), "warning_count": len(result["warnings"]),
            },
        )
        return result

    moment = now or datetime.now(timezone.utc)
    artifact_id = f"th_{secrets.token_hex(6)}"
    final_payload = dict(payload)
    final_payload["policy_version"] = POLICY_VERSION
    final_payload["artifact_id"] = artifact_id
    final_payload["validation"] = {"status": result["status"], "errors": [], "warnings": result["warnings"]}
    final_thesis = InvestmentThesis.model_validate(final_payload)

    stamp = moment.strftime("%Y-%m-%dT%H%M%SZ")
    outputs_dir = run_dir / "agent_outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = outputs_dir / f"{ticker}-{stamp}-thesis.json"
    artifact_json = json.dumps(
        final_thesis.model_dump(mode="json", by_alias=True, exclude_none=False),
        indent=2, sort_keys=False, ensure_ascii=False,
    )
    artifact_path.write_text(artifact_json, encoding="utf-8")

    record = evidence_module.register(
        run_dir, run_id=run_id, evidence_type="investment_thesis", source_name="investment_analyst",
        status="available", artifact=artifact_path, retrieved_at=utc_now_iso(),
        collection_method="llm_judgment",
    )

    audit_module.append_event(
        run_dir, run_id=run_id, event="analyst_drafted", actor="investment_analyst",
        artifact=relative_to_run(run_dir, artifact_path), evidence_id=record.evidence_id,
        details={
            "ticker": ticker, "artifact_id": artifact_id, "status": result["status"],
            "fundamental_rating": final_thesis.conclusion.fundamental_rating,
            "thesis_confidence": final_thesis.conclusion.thesis_confidence,
        },
    )
    audit_module.append_event(
        run_dir, run_id=run_id, event="validated", actor="investment_analyst", status="success",
        details={
            "ticker": ticker, "thesis_status": result["status"],
            "error_count": 0, "warning_count": len(result["warnings"]),
        },
    )

    result["artifact_path"] = relative_to_run(run_dir, artifact_path)
    result["evidence_id"] = record.evidence_id
    result["artifact_id"] = artifact_id
    return result
