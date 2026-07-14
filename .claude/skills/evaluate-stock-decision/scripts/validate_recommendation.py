"""Validate a completed stock-recommendation artifact against the rubric.

The stock-analyst agent proposes an action, confidence, and horizon; this script
is the check that they follow deterministically from the scores and the rubric,
so the LLM cannot quietly override the math. It verifies:

- schema and enums (action/confidence/time_horizon come from decision-framework);
- every applicable gate and dimension is addressed, scores are in range;
- every cited `source:field` resolves to a real, non-null value in the source
  file it names (no fabricated evidence);
- the weighted score, the mapped action, and the confidence level all match what
  the rubric produces from the artifact's own scores and gate results;
- the required narratives (including the Analyst View) and the
  facts/assumptions/opinions split are present.

Exit code is non-zero with an itemized list when anything fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rubric as rubric_mod
import scoring_worksheet as ws

SCHEMA = "stock-recommendation.v1"
VALID_GATE_RESULTS = ("pass", "fail", "unknown")
UNKNOWN = "unknown"

REQUIRED_NARRATIVES = ("executive_summary", "analyst_view", "updated_thesis", "bull_case", "bear_case", "key_risks")


class ValidationError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _load_sources(artifact: dict, base: Path) -> dict[str, Any]:
    """Re-read each research source named in the artifact so citations can be
    verified. Paths are resolved relative to the artifact's own location."""
    loaded: dict[str, Any] = {}
    for name, spec in (artifact.get("research_sources") or {}).items():
        path = spec.get("path") if isinstance(spec, dict) else None
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        if candidate.exists():
            loaded[name] = _load_json(candidate)
    return loaded


def _resolve_citation(source: str, field: str, ticker: str, sources: dict[str, Any], derived_cache: dict) -> tuple[bool, Any]:
    """Return (resolved_ok, value) for a source:field citation."""
    _, _, path = field.partition(":") if ":" in field else ("", "", field)
    path = path or field
    if source == "derived":
        item = derived_cache.get("item", "unset")
        if item == "unset":
            payload = sources.get("yfinance")
            item = ws._find_research_item(payload, ticker) if payload is not None else None
            derived_cache["item"] = item
            derived_cache["metrics"] = ws.compute_derived_metrics(item)
        value = derived_cache["metrics"].get(path)
        return value is not None, value
    if source not in sources:
        return False, None
    if source == "yfinance":
        item = ws._find_research_item(sources["yfinance"], ticker)
        value = ws._resolve_path(item, path) if item else None
        return value is not None, value
    if source == "classification":
        holding = ws._find_holding(sources["classification"], ticker)
        key = path.split(".", 1)[1] if "." in path else path
        value = None
        if isinstance(holding, dict):
            value = holding.get(key)
            if value is None and isinstance(holding.get("fields"), dict):
                value = holding["fields"].get(key)
        return value is not None, value
    return False, None


def validate_recommendation(artifact: dict, rubric: dict, framework: dict, sources: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ticker = str(artifact.get("ticker", "")).upper()

    if artifact.get("schema") != SCHEMA:
        errors.append(f"schema must be '{SCHEMA}', got '{artifact.get('schema')}'")
    if not ticker:
        errors.append("missing 'ticker'")

    position = artifact.get("position") or {}
    held = bool(position.get("held"))
    dividend_payer = bool(position.get("dividend_payer"))
    income_role = bool(position.get("income_role"))

    actions = set(framework.get("actions") or [])
    confidences = set(framework.get("confidence_levels") or [])
    horizons = set(framework.get("time_horizons") or [])

    derived_cache: dict = {}

    # --- gates ---
    expected_gates = {g["id"]: g for g in rubric_mod.applicable_gates(rubric, dividend_payer=dividend_payer, income_role=income_role)}
    seen_gates = {}
    for entry in artifact.get("gates") or []:
        gid = entry.get("id")
        seen_gates[gid] = entry
        result = entry.get("result")
        if result not in VALID_GATE_RESULTS:
            errors.append(f"gate '{gid}': result must be one of {VALID_GATE_RESULTS}, got '{result}'")
        if result in ("pass", "fail") and not _nonempty(entry.get("evidence")):
            errors.append(f"gate '{gid}': a '{result}' result needs at least one evidence citation")
        for cite in entry.get("evidence") or []:
            ok, _ = _resolve_citation(cite.get("source"), cite.get("field", ""), ticker, sources, derived_cache)
            if not ok and sources:
                errors.append(f"gate '{gid}': citation '{cite.get('field')}' does not resolve to a value in source '{cite.get('source')}'")
    for gid in expected_gates:
        if gid not in seen_gates:
            errors.append(f"missing required gate '{gid}'")

    gate_failed = any((seen_gates.get(gid) or {}).get("result") == "fail" for gid in expected_gates)
    unknown_gates = sum(1 for gid in expected_gates if (seen_gates.get(gid) or {}).get("result") == UNKNOWN)

    # --- dimensions ---
    expected_dims = {d["id"]: d for d in rubric_mod.applicable_dimensions(rubric, dividend_payer=dividend_payer, income_role=income_role)}
    seen_dims = {}
    scores: dict[str, Any] = {}
    for entry in artifact.get("dimensions") or []:
        did = entry.get("id")
        seen_dims[did] = entry
        score = entry.get("score")
        if score == UNKNOWN:
            scores[did] = UNKNOWN
        elif isinstance(score, int) and not isinstance(score, bool) and rubric_mod.SCORE_MIN <= score <= rubric_mod.SCORE_MAX:
            scores[did] = score
            if not _nonempty(entry.get("evidence")):
                errors.append(f"dimension '{did}': a numeric score needs at least one evidence citation")
        else:
            errors.append(f"dimension '{did}': score must be an integer {rubric_mod.SCORE_MIN}-{rubric_mod.SCORE_MAX} or '{UNKNOWN}', got '{score}'")
        for cite in entry.get("evidence") or []:
            ok, _ = _resolve_citation(cite.get("source"), cite.get("field", ""), ticker, sources, derived_cache)
            if not ok and sources:
                errors.append(f"dimension '{did}': citation '{cite.get('field')}' does not resolve to a value in source '{cite.get('source')}'")
    for did in expected_dims:
        if did not in seen_dims:
            errors.append(f"missing required dimension '{did}'")

    unknown_dims = sum(1 for did in expected_dims if scores.get(did) == UNKNOWN)

    # --- recomputed math ---
    recomputed_score = rubric_mod.weighted_score(scores, rubric, dividend_payer=dividend_payer, income_role=income_role)
    stated_score = artifact.get("weighted_score")
    if recomputed_score is None:
        if stated_score is not None:
            errors.append("weighted_score should be null (no dimensions scored) but artifact set a value")
    elif not isinstance(stated_score, (int, float)) or abs(float(stated_score) - recomputed_score) > 1e-2:
        errors.append(f"weighted_score {stated_score} does not match recomputed {round(recomputed_score, 4)}")

    proposed = artifact.get("proposed") or {}
    expected_action = rubric_mod.lookup_action(recomputed_score, gate_failed=gate_failed, held=held, rubric=rubric)
    if proposed.get("action") != expected_action:
        errors.append(f"proposed action '{proposed.get('action')}' does not match rubric verdict '{expected_action}' (score={recomputed_score}, gate_failed={gate_failed}, held={held})")
    if actions and proposed.get("action") not in actions:
        errors.append(f"proposed action '{proposed.get('action')}' not in decision-framework actions")

    groups_ok = _groups_ok(artifact, derived_cache)
    expected_conf = rubric_mod.evaluate_confidence(unknown_dimensions=unknown_dims, unknown_gates=unknown_gates, groups_ok=groups_ok, rubric=rubric)
    if proposed.get("confidence") != expected_conf:
        errors.append(f"proposed confidence '{proposed.get('confidence')}' does not match rubric confidence '{expected_conf}' (unknown_dims={unknown_dims}, unknown_gates={unknown_gates}, groups_ok={groups_ok})")
    if confidences and proposed.get("confidence") not in confidences:
        errors.append(f"proposed confidence '{proposed.get('confidence')}' not in decision-framework confidence_levels")
    if horizons and proposed.get("time_horizon") not in horizons:
        errors.append(f"proposed time_horizon '{proposed.get('time_horizon')}' not in decision-framework time_horizons")

    # --- narratives ---
    narratives = artifact.get("narratives") or {}
    for key in REQUIRED_NARRATIVES:
        if not _nonempty(narratives.get(key)):
            errors.append(f"narratives.{key} must be present and non-empty")
    if not isinstance(narratives.get("section_updates"), dict) or not narratives.get("section_updates"):
        errors.append("narratives.section_updates must be a non-empty mapping of thesis section -> prose")

    fao = artifact.get("facts_assumptions_opinions") or {}
    for key in ("facts", "assumptions", "opinions"):
        if not isinstance(fao.get(key), list):
            errors.append(f"facts_assumptions_opinions.{key} must be a list")

    return errors


def _groups_ok(artifact: dict, derived_cache: dict) -> int:
    spec = (artifact.get("research_sources") or {}).get("yfinance")
    if isinstance(spec, dict) and isinstance(spec.get("groups_ok"), list):
        return len(spec["groups_ok"])
    metrics = derived_cache.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("groups_ok_count"), int):
        return metrics["groups_ok_count"]
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a completed stock-recommendation artifact.")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        artifact = _load_json(args.path)
        rubric = rubric_mod.load_rubric(args.rubric)
        framework = rubric_mod.load_framework()
        rubric_errors = rubric_mod.validate_rubric(rubric, framework)
        if rubric_errors:
            for err in rubric_errors:
                print(f"rubric error: {err}", file=sys.stderr)
            return 1
        sources = _load_sources(artifact, args.path.resolve().parent)
        errors = validate_recommendation(artifact, rubric, framework, sources)
    except (ValidationError, rubric_mod.RubricError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(f"Valid: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
