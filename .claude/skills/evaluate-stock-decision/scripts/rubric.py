"""Load, validate, and apply the hand-curated decision rubric.

This is the shared module for the evaluate-stock-decision skill. It is the
single implementation of the rubric contract: parsing
`Knowledge-Base/taxonomy/decision-rubric.yml`, checking it against the enums in
`decision-framework.yml`, and providing the deterministic math (weight
renormalization, weighted score, verdict-band lookup, confidence rules) that
both `scoring_worksheet.py` and `validate_recommendation.py` depend on.

Nothing here fetches data or reaches the network -- it is pure stdlib + PyYAML.
The judgment (scoring each criterion against evidence) is the LLM's job; this
module only enforces the structure and recomputes the arithmetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import KNOWLEDGE_BASE_FOLDER  # noqa: E402

TAXONOMY_DIR = KNOWLEDGE_BASE_FOLDER / "taxonomy"
DEFAULT_RUBRIC_PATH = TAXONOMY_DIR / "decision-rubric.yml"
DEFAULT_FRAMEWORK_PATH = TAXONOMY_DIR / "decision-framework.yml"

SCORE_MIN = 1
SCORE_MAX = 5
WEIGHT_SUM_TOLERANCE = 1e-6

# applies_when values the rubric may use on gates/dimensions. A gate/dimension
# may carry a single value or a list of values (all conditions must hold, e.g.
# `[income_role, equity_only]` for a gate that only applies to income equities).
APPLIES_ALWAYS = "always"
APPLIES_DIVIDEND_PAYER = "dividend_payer"
APPLIES_INCOME_ROLE = "income_role"
APPLIES_EQUITY_ONLY = "equity_only"
APPLIES_ETF_ONLY = "etf_only"
VALID_APPLIES_WHEN = (
    APPLIES_ALWAYS,
    APPLIES_DIVIDEND_PAYER,
    APPLIES_INCOME_ROLE,
    APPLIES_EQUITY_ONLY,
    APPLIES_ETF_ONLY,
)


def _applies_conditions(entry: dict) -> list[str]:
    """Normalize an entry's `applies_when` to a list of conditions (all must hold)."""
    applies = entry.get("applies_when", APPLIES_ALWAYS)
    if isinstance(applies, list):
        return [str(item) for item in applies]
    return [str(applies)]


class RubricError(ValueError):
    """Raised when the rubric file cannot be loaded or is structurally invalid."""


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RubricError(f"rubric file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RubricError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RubricError(f"{path}: expected a mapping at the top level")
    return data


def load_framework(path: Path | None = None) -> dict:
    return load_yaml(path or DEFAULT_FRAMEWORK_PATH)


def load_rubric(path: Path | None = None) -> dict:
    """Parse the rubric YAML (no validation). Use validate_rubric to check it."""
    return load_yaml(path or DEFAULT_RUBRIC_PATH)


def _evidence_sources(entry: dict) -> list[str]:
    fields = entry.get("evidence_fields") or []
    if not isinstance(fields, list):
        return []
    sources = []
    for field in fields:
        if isinstance(field, str) and ":" in field:
            sources.append(field.split(":", 1)[0])
    return sources


def _validate_applies_when(entry: dict, label: str) -> list[str]:
    """Validate an entry's applies_when (string or list). Return problems."""
    problems: list[str] = []
    conditions = _applies_conditions(entry)
    for cond in conditions:
        if cond not in VALID_APPLIES_WHEN:
            problems.append(f"{label}: unknown applies_when '{cond}'")
    if APPLIES_EQUITY_ONLY in conditions and APPLIES_ETF_ONLY in conditions:
        problems.append(f"{label}: applies_when cannot require both equity_only and etf_only")
    return problems


def validate_rubric(rubric: dict, framework: dict | None = None) -> list[str]:
    """Return a list of structural problems with the rubric (empty when valid).

    Checks: required sections present; source registry well-formed; every
    evidence field names a registered source; dimension weights sum to 1.0;
    verdict bands cover the score range and use known actions; enums cross-check
    against decision-framework.yml.
    """
    errors: list[str] = []
    framework = framework if framework is not None else {}
    actions = set(framework.get("actions") or [])
    confidence_levels = set(framework.get("confidence_levels") or [])
    horizons = set(framework.get("time_horizons") or [])

    for key in ("version", "sources", "gates", "dimensions", "scoring", "verdict_bands", "confidence_rules"):
        if key not in rubric:
            errors.append(f"missing required rubric section '{key}'")

    sources = rubric.get("sources")
    registered: set[str] = set()
    if not isinstance(sources, dict) or not sources:
        errors.append("'sources' must be a non-empty mapping")
    else:
        registered = set(sources.keys())
        for name, spec in sources.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("groups"), list):
                errors.append(f"source '{name}' must be a mapping with a 'groups' list")

    # Gates: structure + evidence sources registered.
    gates = rubric.get("gates")
    if not isinstance(gates, list) or not gates:
        errors.append("'gates' must be a non-empty list")
        gates = []
    gate_ids: set[str] = set()
    for gate in gates:
        gid = gate.get("id") if isinstance(gate, dict) else None
        if not gid:
            errors.append("every gate needs an 'id'")
            continue
        if gid in gate_ids:
            errors.append(f"duplicate gate id '{gid}'")
        gate_ids.add(gid)
        errors.extend(_validate_applies_when(gate, f"gate '{gid}'"))
        if not str(gate.get("fail_when", "")).strip():
            errors.append(f"gate '{gid}': missing 'fail_when' criteria")
        for src in _evidence_sources(gate):
            if src not in registered:
                errors.append(f"gate '{gid}': evidence source '{src}' is not registered under 'sources'")

    # Dimensions: weights sum to 1.0, anchors present, evidence sources registered.
    dimensions = rubric.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("'dimensions' must be a non-empty list")
        dimensions = []
    dim_ids: set[str] = set()
    for dim in dimensions:
        did = dim.get("id") if isinstance(dim, dict) else None
        if not did:
            errors.append("every dimension needs an 'id'")
            continue
        if did in dim_ids:
            errors.append(f"duplicate dimension id '{did}'")
        dim_ids.add(did)
        weight = dim.get("weight")
        if not isinstance(weight, (int, float)) or not (0 < weight <= 1):
            errors.append(f"dimension '{did}': weight must be a number in (0, 1]")
        errors.extend(_validate_applies_when(dim, f"dimension '{did}'"))
        anchors = dim.get("anchors")
        if not isinstance(anchors, dict) or not {"1", "3", "5"}.issubset(anchors.keys()):
            errors.append(f"dimension '{did}': anchors must define at least '1', '3', and '5'")
        for src in _evidence_sources(dim):
            if src not in registered:
                errors.append(f"dimension '{did}': evidence source '{src}' is not registered under 'sources'")

    # Weights must sum to 1.0 within each asset-class track: a single security is
    # only ever scored on the dimensions applicable to its track, and the weighted
    # average renormalizes over that subset. Both tracks are checked under the
    # most-permissive conditionals (dividend_payer/income_role true) so that every
    # dimension that could ever apply is counted once.
    if dimensions and not any("must be a number in (0, 1]" in e for e in errors):
        for track, is_etf in (("equity", False), ("etf", True)):
            total = sum(
                float(dim["weight"])
                for dim in dimensions
                if isinstance(dim, dict)
                and isinstance(dim.get("weight"), (int, float))
                and not isinstance(dim.get("weight"), bool)
                and is_applicable(dim, dividend_payer=True, income_role=True, is_etf=is_etf)
            )
            if abs(total - 1.0) > 1e-3:
                errors.append(f"{track}-track dimension weights must sum to 1.0, got {total:.4f}")

    # Verdict bands: cover the range, known actions, gate_fail present.
    bands = rubric.get("verdict_bands")
    if not isinstance(bands, dict):
        errors.append("'verdict_bands' must be a mapping")
    else:
        for state in ("not_held", "held"):
            entries = bands.get(state)
            if not isinstance(entries, list) or not entries:
                errors.append(f"verdict_bands.{state} must be a non-empty list")
                continue
            mins = []
            for entry in entries:
                if not isinstance(entry, dict) or "min" not in entry or "action" not in entry:
                    errors.append(f"verdict_bands.{state}: each band needs 'min' and 'action'")
                    continue
                mins.append(entry["min"])
                if actions and entry["action"] not in actions:
                    errors.append(f"verdict_bands.{state}: action '{entry['action']}' not in decision-framework actions")
            if mins and min(mins) > SCORE_MIN:
                errors.append(f"verdict_bands.{state}: lowest band min ({min(mins)}) must be <= {SCORE_MIN} to cover the range")
        gate_fail = bands.get("gate_fail")
        if not isinstance(gate_fail, dict) or "not_held" not in gate_fail or "held" not in gate_fail:
            errors.append("verdict_bands.gate_fail must set 'not_held' and 'held' actions")
        elif actions:
            for state in ("not_held", "held"):
                if gate_fail[state] not in actions:
                    errors.append(f"verdict_bands.gate_fail.{state}: action '{gate_fail[state]}' not in decision-framework actions")

    # Confidence rules: needed levels present and valid enums.
    conf = rubric.get("confidence_rules")
    if not isinstance(conf, dict):
        errors.append("'confidence_rules' must be a mapping")
    else:
        for level in ("High", "Medium", "Low"):
            if level not in conf:
                errors.append(f"confidence_rules missing level '{level}'")
        if confidence_levels:
            for level in conf:
                if level not in confidence_levels:
                    errors.append(f"confidence_rules level '{level}' not in decision-framework confidence_levels")
        for level in ("High", "Medium"):
            rule = conf.get(level)
            if not isinstance(rule, dict):
                continue
            threshold = rule.get("min_groups_ok", 0)
            if isinstance(threshold, dict) and not {"equity", "etf"}.issubset(threshold.keys()):
                errors.append(
                    f"confidence_rules.{level}.min_groups_ok mapping must define both 'equity' and 'etf'"
                )

    # Time horizon rules cross-check (optional section, but validate if present).
    horizon_rules = rubric.get("time_horizon_rules")
    if isinstance(horizon_rules, dict) and horizons:
        for role, horizon in (horizon_rules.get("default_by_role") or {}).items():
            if horizon not in horizons:
                errors.append(f"time_horizon_rules.default_by_role['{role}']: '{horizon}' not in decision-framework time_horizons")

    return errors


# --- Application helpers (shared math) -------------------------------------

def is_applicable(entry: dict, *, dividend_payer: bool, income_role: bool, is_etf: bool = False) -> bool:
    """Whether a gate/dimension applies given the position's context.

    All conditions in a (possibly list-valued) `applies_when` must hold.
    `equity_only` applies to non-funds, `etf_only` to funds.
    """
    for cond in _applies_conditions(entry):
        if cond == APPLIES_ALWAYS:
            continue
        if cond == APPLIES_DIVIDEND_PAYER and not dividend_payer:
            return False
        if cond == APPLIES_INCOME_ROLE and not income_role:
            return False
        if cond == APPLIES_EQUITY_ONLY and is_etf:
            return False
        if cond == APPLIES_ETF_ONLY and not is_etf:
            return False
    return True


def applicable_dimensions(
    rubric: dict, *, dividend_payer: bool, income_role: bool, is_etf: bool = False
) -> list[dict]:
    return [
        dim
        for dim in rubric.get("dimensions", [])
        if is_applicable(dim, dividend_payer=dividend_payer, income_role=income_role, is_etf=is_etf)
    ]


def applicable_gates(
    rubric: dict, *, dividend_payer: bool, income_role: bool, is_etf: bool = False
) -> list[dict]:
    return [
        gate
        for gate in rubric.get("gates", [])
        if is_applicable(gate, dividend_payer=dividend_payer, income_role=income_role, is_etf=is_etf)
    ]


def weighted_score(
    scores: dict[str, object], rubric: dict, *, dividend_payer: bool, income_role: bool, is_etf: bool = False
) -> float | None:
    """Weighted average over applicable, numerically-scored dimensions.

    `scores` maps dimension id -> 1..5 int or "unknown"/None. Unknown and
    non-applicable dimensions are excluded and the remaining weights are
    renormalized. Returns None if nothing scored.
    """
    numerator = 0.0
    weight_sum = 0.0
    for dim in applicable_dimensions(
        rubric, dividend_payer=dividend_payer, income_role=income_role, is_etf=is_etf
    ):
        raw = scores.get(dim["id"])
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            continue
        numerator += float(dim["weight"]) * float(raw)
        weight_sum += float(dim["weight"])
    if weight_sum <= 0:
        return None
    return numerator / weight_sum


def lookup_action(score: float | None, *, gate_failed: bool, held: bool, rubric: dict) -> str:
    """Map a weighted score (and gate outcome + position state) to an action."""
    bands = rubric["verdict_bands"]
    state = "held" if held else "not_held"
    if gate_failed:
        return bands["gate_fail"][state]
    if score is None:
        # No score and no gate failure: fall to the lowest band conservatively.
        score = float(SCORE_MIN)
    for entry in sorted(bands[state], key=lambda b: b["min"], reverse=True):
        if score >= entry["min"]:
            return entry["action"]
    return sorted(bands[state], key=lambda b: b["min"])[0]["action"]


def _min_groups_ok(rule: dict, *, is_etf: bool) -> int:
    """Resolve a confidence rule's min_groups_ok, which may be an int (both tracks)
    or a per-track mapping {equity: N, etf: N}."""
    threshold = rule.get("min_groups_ok", 0)
    if isinstance(threshold, dict):
        return int(threshold.get("etf" if is_etf else "equity", 0))
    return int(threshold)


def evaluate_confidence(
    *, unknown_dimensions: int, unknown_gates: int, groups_ok: int, rubric: dict, is_etf: bool = False
) -> str:
    """Return the highest confidence level whose thresholds are all satisfied."""
    conf = rubric["confidence_rules"]
    for level in ("High", "Medium"):
        rule = conf.get(level)
        if not isinstance(rule, dict):
            continue
        if unknown_dimensions > rule.get("max_unknown_dimensions", 0):
            continue
        if unknown_gates > rule.get("max_unknown_gates", 0):
            continue
        if groups_ok < _min_groups_ok(rule, is_etf=is_etf):
            continue
        return level
    return "Low"


def default_time_horizon(role: str | None, rubric: dict) -> str:
    rules = rubric.get("time_horizon_rules") or {}
    by_role = rules.get("default_by_role") or {}
    if role and role in by_role:
        return by_role[role]
    return by_role.get("Unassigned", "Medium-term")
