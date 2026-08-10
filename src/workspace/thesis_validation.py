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

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import evidence as evidence_module
from .analysis_models import (
    CONFIDENCE_ORDER,
    TRACE_BLOCKING_THRESHOLD,
    TRACE_WARNING_THRESHOLD,
    AnalysisScope,
    EvidenceCompleteness,
    InvestmentThesis,
)
from .paths import PathEscapeError, resolve_in_run


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
