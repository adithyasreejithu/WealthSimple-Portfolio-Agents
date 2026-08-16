"""Deterministic validator for `DecisionProposal` artifacts -- Phase 11's
counterpart to `thesis_validation.py`.

Mirrors that module's split: `models.DecisionProposal` enforces everything a
single document can check about itself (shape, the locked five-action enum,
`trade_executed`/`human_approval_required` pinned); this module adds the
checks that need the run's other files -- are the cited thesis and policy
worksheet the actual bytes on disk (not stale or hand-edited), and, the one
check that makes the Phase 11 gate a guarantee rather than a hope: if the
policy worksheet the agent read recorded a failing check for this security,
the proposed action may not be `Buy`/`Add` -- that is re-derived from the
worksheet on disk, never trusted from the agent's own copy in
`policy_checks`.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

import config

from . import audit as audit_module
from . import evidence as evidence_module
from .models import DecisionProposal
from .paths import PathEscapeError, relative_to_run, resolve_in_run, utc_now_iso

# Actions an *owned* security with any failing policy check may still be
# proposed -- a failing single-name or group-allocation-target check means
# "reduce or hold," never "add more."
_ACTIONS_ALLOWED_ON_POLICY_FAILURE = frozenset({"Hold", "Trim", "Sell"})

# Actions that only make sense against an existing position -- you cannot
# trim or sell what you do not hold.
_ACTIONS_REQUIRING_OWNERSHIP = frozenset({"Trim", "Sell"})

# Phase 11 extension: `models.PortfolioAction`/`models.WishlistAction`,
# mirrored here as plain frozensets for runtime membership checks (the
# pydantic Literal already constrains the field's shape; these constrain
# which of the two vocabularies applies to *this* security, which needs the
# policy worksheet, not just the document itself).
_OWNED_ACTIONS = frozenset({"Buy", "Hold", "Trim", "Sell", "Add"})
_NOT_OWNED_ACTIONS = frozenset({"Buy", "Watch", "Wait", "Pass"})

# A not-owned security with a failing policy check (e.g. its group is over
# cap) cannot be proposed `Buy` -- that is exactly the `Watch` semantics
# (thesis attractive, policy blocks entry right now), enforced here rather
# than left to the agent's judgment.
_NOT_OWNED_ACTIONS_ALLOWED_ON_POLICY_FAILURE = frozenset({"Watch", "Wait", "Pass"})


def _load_portfolio_policy() -> dict[str, Any]:
    try:
        raw = Path(config.POLICY_FILE).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}


_PORTFOLIO_POLICY: dict[str, Any] = _load_portfolio_policy()
PORTFOLIO_POLICY_VERSION: str = str(_PORTFOLIO_POLICY.get("version") or "v1.1")


def check_thesis_ref(decision: DecisionProposal, run_dir: Path) -> list[str]:
    """`thesis_ref` must resolve inside the run and its live sha256 must
    still equal the recorded hash -- a stale or hand-edited thesis cannot be
    cited without detection, same reasoning as `thesis_validation.check_worksheet_ref`."""
    try:
        resolved = resolve_in_run(run_dir, decision.thesis_ref.path)
    except PathEscapeError as exc:
        return [f"thesis_ref: {exc}"]
    if not resolved.is_file():
        return [f"thesis_ref: artifact missing on disk ({decision.thesis_ref.path})"]
    live_hash = "sha256:" + evidence_module.content_hash(resolved)
    if live_hash != decision.thesis_ref.hash:
        return [
            f"thesis_ref.hash no longer matches the thesis on disk "
            f"(recorded {decision.thesis_ref.hash}, actual {live_hash})"
        ]
    return []


def check_policy_worksheet_ref(decision: DecisionProposal, run_dir: Path) -> list[str]:
    """Same check as `check_thesis_ref`, for `policy_worksheet_ref`."""
    try:
        resolved = resolve_in_run(run_dir, decision.policy_worksheet_ref.path)
    except PathEscapeError as exc:
        return [f"policy_worksheet_ref: {exc}"]
    if not resolved.is_file():
        return [f"policy_worksheet_ref: artifact missing on disk ({decision.policy_worksheet_ref.path})"]
    live_hash = "sha256:" + evidence_module.content_hash(resolved)
    if live_hash != decision.policy_worksheet_ref.hash:
        return [
            f"policy_worksheet_ref.hash no longer matches the policy worksheet on disk "
            f"(recorded {decision.policy_worksheet_ref.hash}, actual {live_hash})"
        ]
    return []


def _read_json_ref(run_dir: Path, path: str) -> dict[str, Any] | None:
    """Load a ref-cited JSON document by its run-relative path, `None` on any
    problem (escape, absence, malformed JSON) -- shared by
    `_read_policy_worksheet` and `_read_thesis`; hash integrity is already
    covered separately by `check_thesis_ref`/`check_policy_worksheet_ref`, so
    this function's only job is getting the bytes."""
    try:
        resolved = resolve_in_run(run_dir, path)
    except PathEscapeError:
        return None
    if not resolved.is_file():
        return None
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_policy_worksheet(run_dir: Path, path: str) -> dict[str, Any] | None:
    return _read_json_ref(run_dir, path)


def _read_thesis(run_dir: Path, path: str) -> dict[str, Any] | None:
    return _read_json_ref(run_dir, path)


def check_action_consistent_with_policy(
    decision: DecisionProposal, policy_worksheet: dict[str, Any]
) -> list[str]:
    """The Phase 11 gate, enforced deterministically: any policy check the
    worksheet recorded as `fail` forces the action into the allowed-on-
    failure set for this security's ownership -- owned: `Hold`/`Trim`/`Sell`
    (`Buy`/`Add` is a validation error); not-owned: `Watch`/`Wait`/`Pass`
    (`Buy` is a validation error, which is exactly the `Watch` semantics --
    thesis attractive, policy blocks entry right now). Never left to the
    agent's judgment."""
    failed = [
        check.get("name")
        for check in policy_worksheet.get("policy_checks", [])
        if check.get("result") == "fail"
    ]
    if not failed:
        return []
    currently_held = bool(policy_worksheet.get("subject", {}).get("currently_held"))
    allowed = _ACTIONS_ALLOWED_ON_POLICY_FAILURE if currently_held else _NOT_OWNED_ACTIONS_ALLOWED_ON_POLICY_FAILURE
    if decision.proposed_action not in allowed:
        return [
            f"proposed_action {decision.proposed_action!r} is not allowed: "
            f"policy worksheet has failing check(s) {failed} for this "
            f"{'owned' if currently_held else 'not-owned'} security"
        ]
    return []


def check_action_matches_ownership_vocabulary(
    decision: DecisionProposal, policy_worksheet: dict[str, Any]
) -> list[str]:
    """`proposed_action` must come from the vocabulary this security's
    ownership selects: owned -> `Buy`/`Hold`/`Trim`/`Sell`/`Add`; not-owned ->
    `Buy`/`Watch`/`Wait`/`Pass`. Re-derived from the policy worksheet's own
    `subject.currently_held`, same discipline as every other check in this
    module -- the agent is told about the two vocabularies in its
    instructions, but the guarantee comes from re-derivation here."""
    currently_held = bool(policy_worksheet.get("subject", {}).get("currently_held"))
    allowed = _OWNED_ACTIONS if currently_held else _NOT_OWNED_ACTIONS
    if decision.proposed_action not in allowed:
        return [
            f"proposed_action {decision.proposed_action!r} is not valid for "
            f"{'an owned' if currently_held else 'a not-owned'} security "
            f"(subject.currently_held={currently_held}); allowed: {sorted(allowed)}"
        ]
    return []


def check_action_requires_ownership(
    decision: DecisionProposal, policy_worksheet: dict[str, Any]
) -> list[str]:
    """A security that is not currently held may not be proposed for `Trim`
    or `Sell` -- there is nothing to trim or sell.

    Re-derived from the policy worksheet's own `subject.currently_held`
    (computed fresh from `analytics.get_holdings()` at worksheet-build time),
    never trusted from the agent's own reading of the `security-status`
    skill's digest -- same discipline as `check_action_consistent_with_policy`:
    the agent is told about this gate in its instructions, but the guarantee
    comes from re-derivation here, not from the agent following instructions
    correctly.
    """
    currently_held = bool(policy_worksheet.get("subject", {}).get("currently_held"))
    if not currently_held and decision.proposed_action in _ACTIONS_REQUIRING_OWNERSHIP:
        return [
            f"proposed_action {decision.proposed_action!r} is not allowed: "
            "the policy worksheet reports this security as not currently held "
            "(subject.currently_held=false) -- Trim/Sell require an existing position"
        ]
    return []


# --- Phase 11 extension: advisory order-mechanics guidance -----------------

# Actions that represent live buy/sell intent -- `order_guidance` is required
# for these and forbidden for every other action (`Hold`/`Watch`/`Wait`/
# `Pass`, none of which propose a trade). `Buy` is shared by both action
# vocabularies (owned and not-owned).
_ACTIONS_REQUIRING_ORDER_GUIDANCE = frozenset({"Buy", "Add", "Trim", "Sell"})

# A cited price must match the value its `source` path resolves to within
# this tolerance -- relative, with an absolute floor so a low-priced security
# doesn't get an unreasonably tight (or a near-zero, div-by-zero-prone)
# window. Chosen once, here, rather than left as a per-call magic number.
_PRICE_TOLERANCE_RELATIVE = 0.001  # 0.1%
_PRICE_TOLERANCE_ABSOLUTE = 0.01  # $0.01 floor

_CITATION_BRACKET = re.compile(r"^(\w+)\[(\w+)\]$")


def check_order_guidance_present(decision: DecisionProposal) -> list[str]:
    """`order_guidance` is required for a live buy/sell action (`Buy`/`Add`/
    `Trim`/`Sell`) and forbidden for a non-trade outcome (`Hold`/`Watch`/
    `Wait`/`Pass`) -- pure shape, no worksheet or thesis needed."""
    requires = decision.proposed_action in _ACTIONS_REQUIRING_ORDER_GUIDANCE
    if requires and decision.order_guidance is None:
        return [f"proposed_action {decision.proposed_action!r} requires order_guidance"]
    if not requires and decision.order_guidance is not None:
        return [f"proposed_action {decision.proposed_action!r} must not carry order_guidance"]
    return []


def _resolve_citation(root: dict[str, Any], path: str) -> tuple[Any, str | None]:
    """Navigate a dotted citation path (already stripped of its `thesis.`/
    `policy_worksheet.` root) inside an already-loaded document.

    `name[key]` selects the entry of the list under `name` whose own
    `method` field equals `key` -- the shape `valuation.build_valuation_methods`
    produces (`[{"method": "fcf_yield", ...}, ...]`), the only list this
    citation grammar needs to index into today. Returns `(value, None)` on
    success, `(None, error_message)` on any failure to resolve.
    """
    current: Any = root
    for segment in path.split("."):
        if not segment:
            return None, f"empty path segment in {path!r}"
        bracket = _CITATION_BRACKET.match(segment)
        if bracket:
            key, method_value = bracket.groups()
            if not isinstance(current, dict) or key not in current:
                return None, f"{key!r} not found while resolving {path!r}"
            container = current[key]
            if not isinstance(container, list):
                return None, f"{key!r} is not a list while resolving {path!r}"
            match = next(
                (item for item in container if isinstance(item, dict) and item.get("method") == method_value),
                None,
            )
            if match is None:
                return None, f"no entry with method={method_value!r} in {key!r} while resolving {path!r}"
            current = match
        else:
            if not isinstance(current, dict) or segment not in current:
                return None, f"{segment!r} not found while resolving {path!r}"
            current = current[segment]
    return current, None


def _resolve_price_source(
    source: str, *, thesis: dict[str, Any] | None, policy_worksheet: dict[str, Any] | None
) -> tuple[float | None, str | None]:
    if source.startswith("thesis."):
        if thesis is None:
            return None, "thesis is not available to check this citation against"
        value, error = _resolve_citation(thesis, source[len("thesis.") :])
    elif source.startswith("policy_worksheet."):
        if policy_worksheet is None:
            return None, "policy_worksheet is not available to check this citation against"
        value, error = _resolve_citation(policy_worksheet, source[len("policy_worksheet.") :])
    else:
        return None, f"source must start with 'thesis.' or 'policy_worksheet.', got {source!r}"
    if error:
        return None, error
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None, f"{source!r} does not resolve to a number (got {value!r})"
    return float(value), None


def _check_price_level(
    label: str,
    level: Any,
    *,
    thesis: dict[str, Any] | None,
    policy_worksheet: dict[str, Any] | None,
) -> list[str]:
    resolved, error = _resolve_price_source(level.source, thesis=thesis, policy_worksheet=policy_worksheet)
    if error:
        return [f"order_guidance.{label}.source {level.source!r} could not be verified: {error}"]
    tolerance = max(_PRICE_TOLERANCE_ABSOLUTE, abs(resolved) * _PRICE_TOLERANCE_RELATIVE)
    if abs(level.price - resolved) > tolerance:
        return [
            f"order_guidance.{label}.price {level.price} does not match the value at "
            f"{level.source!r} ({resolved}) within tolerance {tolerance:.4f}"
        ]
    return []


def check_order_guidance_prices_are_grounded(
    decision: DecisionProposal,
    *,
    thesis: dict[str, Any] | None,
    policy_worksheet: dict[str, Any] | None,
) -> list[str]:
    """The "no invented numbers" guarantee: every price cited in
    `order_guidance` must resolve, via its `source` citation path, to a
    matching value already present in the cited thesis or policy worksheet on
    disk -- re-derived here, never trusted from the agent's own
    `PriceLevel.price`. Applies `check_thesis_ref`'s "recheck from the source,
    never trust the agent's copy" discipline to prices instead of hashes."""
    if decision.order_guidance is None:
        return []
    guidance = decision.order_guidance
    problems: list[str] = []
    problems.extend(
        _check_price_level("reference_price", guidance.reference_price, thesis=thesis, policy_worksheet=policy_worksheet)
    )
    if guidance.trigger_price is not None:
        problems.extend(
            _check_price_level("trigger_price", guidance.trigger_price, thesis=thesis, policy_worksheet=policy_worksheet)
        )
    if guidance.limit_price is not None:
        problems.extend(
            _check_price_level("limit_price", guidance.limit_price, thesis=thesis, policy_worksheet=policy_worksheet)
        )
    return problems


def validate_decision(
    payload: dict[str, Any],
    *,
    run_dir: Path | None = None,
    policy_worksheet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full check of one `DecisionProposal` artifact.

    Returns `{ok, status, errors, warnings, decision}`, following
    `thesis_validation.validate_thesis`'s shape and status rules
    (`invalid` if any error, else `valid_with_warnings` if any warning, else
    `valid`)."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        decision = DecisionProposal.model_validate(payload)
    except ValidationError as exc:
        errors.append(f"schema: {exc.error_count()} error(s): {exc}")
        return {"ok": False, "status": "invalid", "errors": errors, "warnings": warnings, "decision": None}

    resolved_policy_worksheet = policy_worksheet
    resolved_thesis: dict[str, Any] | None = None
    if run_dir is not None:
        thesis_ref_errors = check_thesis_ref(decision, run_dir)
        errors.extend(thesis_ref_errors)
        if not thesis_ref_errors:
            resolved_thesis = _read_thesis(run_dir, decision.thesis_ref.path)
        policy_worksheet_errors = check_policy_worksheet_ref(decision, run_dir)
        errors.extend(policy_worksheet_errors)
        if resolved_policy_worksheet is None and not policy_worksheet_errors:
            resolved_policy_worksheet = _read_policy_worksheet(run_dir, decision.policy_worksheet_ref.path)
    else:
        warnings.append("ref checks skipped: no run_dir supplied")

    if resolved_policy_worksheet is not None:
        errors.extend(check_action_matches_ownership_vocabulary(decision, resolved_policy_worksheet))
        errors.extend(check_action_consistent_with_policy(decision, resolved_policy_worksheet))
        errors.extend(check_action_requires_ownership(decision, resolved_policy_worksheet))
    else:
        warnings.append("policy consistency check skipped: no policy worksheet available")

    errors.extend(check_order_guidance_present(decision))
    if decision.order_guidance is not None:
        if resolved_thesis is not None or resolved_policy_worksheet is not None:
            errors.extend(
                check_order_guidance_prices_are_grounded(
                    decision, thesis=resolved_thesis, policy_worksheet=resolved_policy_worksheet
                )
            )
        else:
            warnings.append("order_guidance price grounding skipped: neither thesis nor policy worksheet available")

    status = "invalid" if errors else ("valid_with_warnings" if warnings else "valid")
    return {"ok": not errors, "status": status, "errors": errors, "warnings": warnings, "decision": decision}


# --- Phase 11: CLI-facing entry points -------------------------------------
#
# The Portfolio Manager agent only has Bash/Read/Write -- these two functions
# are the entire bodies of `run check-decision` / `run save-decision` in
# `cli.py`, mirroring `thesis_validation.py`'s equivalent pair.


def check_decision_draft(payload: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """Side-effect-free validation of a draft `DecisionProposal` payload --
    the entire body of `run check-decision`. Writes nothing, registers
    nothing, appends no audit event."""
    result = validate_decision(payload, run_dir=run_dir)
    result.pop("decision", None)
    return result


def save_decision(
    payload: dict[str, Any],
    *,
    run_dir: Path,
    run_id: str,
    ticker: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The entire body of `run save-decision`. Re-validates independently --
    never trusts that a prior `check_decision_draft` call saw the same bytes.

    On failure: writes nothing, registers nothing; appends one `validated`
    (failure) audit event.

    On success: authoritatively overwrites `policy_version` (never trusts
    the agent's typed placeholder -- mirrors `thesis_validation.save_thesis`),
    writes the artifact to `final/` (a proposal, never an order -- see
    `docs/architecture/run_workspace.md`'s "How information moves"), registers
    it as `decision_proposal` evidence, and appends `pm_drafted` +
    `validated` (success) audit events.
    """
    result = validate_decision(payload, run_dir=run_dir)
    decision = result.pop("decision", None)

    if result["ok"] and decision is not None and decision.subject.identifiers.ticker != ticker:
        result["ok"] = False
        result["status"] = "invalid"
        result["errors"] = [
            *result["errors"],
            f"decision subject.identifiers.ticker {decision.subject.identifiers.ticker!r} "
            f"does not match --ticker {ticker!r}",
        ]

    if not result["ok"] or decision is None:
        audit_module.append_event(
            run_dir, run_id=run_id, event="validated", actor="portfolio_manager", status="failure",
            details={
                "ticker": ticker, "decision_status": result["status"],
                "error_count": len(result["errors"]), "warning_count": len(result["warnings"]),
            },
        )
        return result

    moment = now or datetime.now(timezone.utc)
    proposal_id = f"dp_{secrets.token_hex(6)}"
    final_payload = dict(payload)
    final_payload["policy_version"] = PORTFOLIO_POLICY_VERSION
    final_payload["proposal_id"] = proposal_id
    final_decision = DecisionProposal.model_validate(final_payload)

    stamp = moment.strftime("%Y-%m-%dT%H%M%SZ")
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = final_dir / f"{ticker}-{stamp}-decision.json"
    artifact_json = json.dumps(
        final_decision.model_dump(mode="json", by_alias=True, exclude_none=False),
        indent=2, sort_keys=False, ensure_ascii=False,
    )
    artifact_path.write_text(artifact_json, encoding="utf-8")

    record = evidence_module.register(
        run_dir, run_id=run_id, evidence_type="decision_proposal", source_name="portfolio_manager",
        status="available", artifact=artifact_path, retrieved_at=utc_now_iso(),
        collection_method="llm_judgment",
    )

    audit_module.append_event(
        run_dir, run_id=run_id, event="pm_drafted", actor="portfolio_manager",
        artifact=relative_to_run(run_dir, artifact_path), evidence_id=record.evidence_id,
        details={
            "ticker": ticker, "proposal_id": proposal_id, "status": result["status"],
            "proposed_action": final_decision.proposed_action,
        },
    )
    audit_module.append_event(
        run_dir, run_id=run_id, event="validated", actor="portfolio_manager", status="success",
        details={
            "ticker": ticker, "decision_status": result["status"],
            "error_count": 0, "warning_count": len(result["warnings"]),
        },
    )

    result["artifact_path"] = relative_to_run(run_dir, artifact_path)
    result["evidence_id"] = record.evidence_id
    result["proposal_id"] = proposal_id
    return result
