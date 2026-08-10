"""Pydantic models for the Investment Analyst rebuild's two artifact schemas.

`investment-thesis.v1` (the analyst's sole output) and `analysis-scope.v1`
(the request-side contract it is produced against) are defined in
`docs/architecture/investment_thesis_schema.md` and
`docs/architecture/analysis_scope_schema.md`. This module turns those drafts
into enforced pydantic models; `thesis_validation.py` adds the checks that
need the run's other files (evidence registry, scope contract, TRACE
completeness) and so cannot live on a single document's model.

Follows `models.py`'s existing split: shape, enums, and single-document
invariants live here as pydantic validators; every model sets
`extra="forbid"` so an unrecognized key is a hard failure, not a silent
no-op. The 16 report-section ids, the ETF not-applicable subset, the
evidence-domain vocabulary, and the scenario-probability/TRACE-threshold
tunables are all read from
`Knowledge-Base/taxonomy/investment-analysis-policy.yml` at import time
rather than hardcoded twice -- that policy file is the single source of
truth per its own header, this module and `thesis_validation.py` only read
it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import config

SCHEMA_VERSION_THESIS = "investment-thesis.v1"
SCHEMA_VERSION_SCOPE = "analysis-scope.v1"

_POLICY_PATH = config.KNOWLEDGE_BASE_FOLDER / "taxonomy" / "investment-analysis-policy.yml"

_FALLBACK_SECTION_IDS: tuple[str, ...] = (
    "executive_conclusion", "thesis_and_variant_perception", "company_profile",
    "business_quality", "financial_trajectory", "expectations_and_results",
    "valuation", "price_and_market_context", "options_and_positioning",
    "insider_institutional_capital_allocation", "market_and_macro_sensitivity",
    "catalysts", "risks_and_disconfirming_evidence", "scenarios",
    "portfolio_context", "conclusion_and_triggers",
)
_FALLBACK_ETF_NOT_APPLICABLE = (
    "expectations_and_results", "insider_institutional_capital_allocation",
)
_FALLBACK_EVIDENCE_DOMAINS: tuple[str, ...] = (
    "position", "ledger", "prices", "financials", "earnings", "dividends",
    "classification", "portfolio_context", "overview", "valuation", "analyst",
    "options", "news", "insider", "institutional", "funds", "market_context",
    "company_filings",
)


def _load_policy() -> dict[str, Any]:
    try:
        raw = _POLICY_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}


def _flatten_evidence_domains(policy: dict[str, Any]) -> tuple[str, ...]:
    registry = policy.get("evidence_domain_registry") or {}
    domains: list[str] = []
    for group in registry.values():
        if isinstance(group, dict):
            domains.extend(group.get("domains") or [])
    seen: dict[str, None] = dict.fromkeys(domains)
    return tuple(seen) or _FALLBACK_EVIDENCE_DOMAINS


_POLICY: dict[str, Any] = _load_policy()

SECTION_IDS: tuple[str, ...] = tuple(
    entry["id"] for entry in (_POLICY.get("section_registry") or []) if "id" in entry
) or _FALLBACK_SECTION_IDS

ETF_NOT_APPLICABLE_SECTIONS: tuple[str, ...] = tuple(
    _POLICY.get("etf_not_applicable_sections") or _FALLBACK_ETF_NOT_APPLICABLE
)

EVIDENCE_DOMAINS: tuple[str, ...] = _flatten_evidence_domains(_POLICY)

SCENARIO_PROBABILITY_TOLERANCE: float = float(
    (_POLICY.get("scenario_rules") or {}).get("probability_sum_tolerance", 0.001)
)

TRACE_BLOCKING_THRESHOLD: float = float(
    (_POLICY.get("trace_policy") or {}).get("blocking_threshold", 50.0)
)
TRACE_WARNING_THRESHOLD: float = float(
    (_POLICY.get("trace_policy") or {}).get("warning_threshold", 80.0)
)

POLICY_VERSION: str = str(_POLICY.get("version") or "v1.0")


# --- shared vocabulary ---------------------------------------------------

Confidence = Literal["high", "medium", "low"]
CONFIDENCE_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

# TRACE-style per-domain grading, mirrors `src/skill_trace.py`'s three-way
# split so a domain's outcome is graded consistently everywhere it is read.
EvidenceCompleteness = Literal["ok", "missing", "not_applicable"]

AssetTrack = Literal["equity", "etf"]
AnalysisMode = Literal[
    "initial_research", "scheduled_review", "earnings_update", "material_event",
    "price_move_review", "thesis_monitor", "portfolio_decision",
]
# decision-framework.yml's time_horizons enum, reused verbatim rather than
# introducing a second horizon vocabulary (schema doc §3 `decision_horizon`).
AnalysisHorizon = Literal["Short-term", "Medium-term", "Long-term"]

FundamentalRating = Literal["attractive", "neutral", "unattractive", "insufficient_evidence"]
ValuationStance = Literal["discounted", "reasonable", "demanding", "indeterminate"]
ThesisDirection = Literal["initial", "strengthening", "unchanged", "weakening", "broken"]

SectionState = Literal["unchanged", "changed", "not_evaluated", "not_applicable"]
ClaimType = Literal["fact", "inference", "assumption", "opinion"]
ClaimImportance = Literal["critical", "supporting"]
StatusVsPrior = Literal["new", "strengthened", "unchanged", "weakened", "invalidated"]
CapabilityLabel = Literal["snapshot_only", "current_snapshot_only", "history_available"]

ValuationMethodName = Literal[
    "earnings_multiple", "fcf_yield", "ev_revenue", "ev_ebitda",
    "dividend_or_dcf_distributable", "simplified_dcf", "reverse_dcf",
    "sum_of_the_parts", "nav",
]
ConditionOperator = Literal["gte", "lte", "eq", "qualitative"]
ConflictResolution = Literal["unresolved", "source_precedence", "period_mismatch", "restatement"]
ThesisValidationStatus = Literal["pending", "valid", "valid_with_warnings", "invalid"]

TriggerType = Literal[
    "schedule", "earnings", "material_event", "price_move", "user_request", "monitor_trigger",
]
CriticalGapPolicy = Literal["stop", "proceed_with_gap_disclosure"]

# decision-framework.yml's actions enum -- reserved for the Portfolio Manager
# (Phase 11). The Investment Analyst must never emit one of these anywhere.
DECISION_FRAMEWORK_ACTIONS = frozenset({"Buy", "Sell", "Hold", "Trim", "Add", "Watchlist", "Avoid"})

# investment_thesis_schema.md §2 -- reject anywhere in the tree, not only at
# the top level. Includes both the schema doc's exhaustive list and the two
# additional names the Phase 1 build task called out (`execution_price`,
# `broker_order`).
FORBIDDEN_FIELD_NAMES = frozenset({
    "target_weight", "target_portfolio_weight", "position_size", "capital_to_deploy",
    "shares_to_buy", "shares_to_sell", "dollar_amount",
    "trade_action", "order_type", "broker", "broker_order", "fill_price", "fill_quantity",
    "execution_venue", "execution_price",
    "portfolio_action",
})
# Field names that, if they carry a decision-framework action string, are
# smuggling a Portfolio Manager decision under a name this schema does not
# declare (the field itself would already be caught by `extra="forbid"`, but
# scanning the raw payload first gives a much clearer error).
_DECISION_LIKE_KEYS = frozenset({"action", "recommendation", "decision", "portfolio_action", "trade_action"})
_SCORE_KEY = "score"


def _walk(node: Any, path: str = ""):
    """Yield (key, value, dotted_path) for every mapping key at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield key, value, child_path
            yield from _walk(value, child_path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")


def find_forbidden_fields(payload: Any) -> list[str]:
    """Scan a raw (pre-validation) payload for fields reserved to the
    Portfolio Manager artifact, per
    `docs/architecture/investment_thesis_schema.md` §2. Returns one message
    per violation found, empty if none."""
    problems: list[str] = []
    for key, value, path in _walk(payload):
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if lowered in FORBIDDEN_FIELD_NAMES:
            problems.append(f"forbidden field: {path} (reserved for Portfolio Manager)")
        elif lowered in _DECISION_LIKE_KEYS and isinstance(value, str) and value in DECISION_FRAMEWORK_ACTIONS:
            problems.append(
                f"forbidden field: {path} carries a decision-framework action "
                f"({value!r}, reserved for Portfolio Manager)"
            )
        elif (
            lowered == _SCORE_KEY
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 1 <= value <= 10
        ):
            problems.append(f"forbidden field: {path} (unanchored 1-10 score; not part of this schema)")
    return problems


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SchemaTaggedModel(StrictModel):
    """Base for the two top-level artifacts, whose `schema` field would
    otherwise shadow `BaseModel.schema()` (pydantic v1's deprecated
    class-schema method, still present in v2 for compatibility). The
    document key stays `schema` via the alias; `populate_by_name` lets code
    in this package still construct one by the `schema_` attribute name."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)


# --- investment-thesis.v1 : shared sub-models -----------------------------


class SecurityRef(StrictModel):
    ticker: str
    provider_symbol: str
    asset_track: AssetTrack


class WorksheetRef(StrictModel):
    path: str
    hash: str

    @field_validator("hash")
    @classmethod
    def _hash_is_sha256(cls, value: str) -> str:
        if not value.startswith("sha256:"):
            raise ValueError(f"worksheet_ref.hash must be a sha256:... digest, got {value!r}")
        return value


class PriorThesisRef(StrictModel):
    exists: bool
    artifact_id: str | None = None
    version: int | None = None
    content_hash: str | None = None
    approved_at: datetime | None = None


class Conclusion(StrictModel):
    fundamental_rating: FundamentalRating
    valuation_stance: ValuationStance
    thesis_direction: ThesisDirection
    thesis_confidence: Confidence
    evidence_completeness_pct: float = Field(ge=0.0, le=100.0)
    investment_case: str
    case_against: str
    most_important_catalyst: str
    most_important_risk: str
    most_important_unknown: str
    analysis_horizon: AnalysisHorizon
    as_of: datetime


class KeyClaim(StrictModel):
    claim_id: str
    statement: str
    claim_type: ClaimType
    importance: ClaimImportance
    evidence_ids: list[str] = Field(default_factory=list)
    status_vs_prior: StatusVsPrior
    confidence: Confidence


class ThesisSection(StrictModel):
    narrative: str
    evidence_ids: list[str] = Field(default_factory=list)
    capability_label: CapabilityLabel | None = None


class Assumptions(StrictModel):
    growth: str | None = None
    margin: str | None = None
    discount_rate: str | None = None
    terminal: str | None = None


class ValueRange(StrictModel):
    low: float
    mid: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> "ValueRange":
        if not (self.low <= self.mid <= self.high):
            raise ValueError(f"value range not ordered low<=mid<=high: {self.low}, {self.mid}, {self.high}")
        return self


class ValuationMethodEntry(StrictModel):
    method: ValuationMethodName
    base_metric: str
    base_period: str
    normalization_adjustments: list[str] = Field(default_factory=list)
    assumptions: Assumptions
    currency: str
    resulting_equity_value_per_share: ValueRange
    sensitivity_note: str


class ValuationBlock(StrictModel):
    methods: list[ValuationMethodEntry] = Field(default_factory=list)
    interpretation: str


class Scenario(StrictModel):
    probability: float = Field(ge=0.0, le=1.0)
    horizon: AnalysisHorizon
    revenue_assumption: str
    margin_assumption: str
    dilution_assumption: str
    valuation_method: ValuationMethodName
    fair_value_per_share: float
    expected_return_pct: float
    conditions: list[str] = Field(default_factory=list)


class ScenarioSet(StrictModel):
    bull: Scenario
    base: Scenario
    bear: Scenario

    @model_validator(mode="after")
    def _probabilities_sum_to_one(self) -> "ScenarioSet":
        total = self.bull.probability + self.base.probability + self.bear.probability
        if abs(total - 1.0) > SCENARIO_PROBABILITY_TOLERANCE:
            raise ValueError(
                f"scenario probabilities sum to {total:.4f}, expected 1.0 "
                f"(+/- {SCENARIO_PROBABILITY_TOLERANCE})"
            )
        return self


class Condition(StrictModel):
    trigger_id: str
    metric: str | None = None
    operator: ConditionOperator
    threshold: float | None = None
    confirmation_periods: int | None = None
    affected_rating: FundamentalRating
    qualitative_description: str | None = None

    @model_validator(mode="after")
    def _qualitative_requires_description(self) -> "Condition":
        if self.operator == "qualitative" and not self.qualitative_description:
            raise ValueError(
                f"condition {self.trigger_id}: qualitative_description is required when operator is qualitative"
            )
        return self


class ConditionsBlock(StrictModel):
    upgrade_conditions: list[Condition] = Field(default_factory=list)
    downgrade_conditions: list[Condition] = Field(default_factory=list)
    invalidation_conditions: list[Condition] = Field(default_factory=list)
    monitoring_items: list[str] = Field(default_factory=list)


class ConflictValue(StrictModel):
    value: Any
    evidence_id: str


class KnownConflict(StrictModel):
    conflict_id: str
    field: str
    values: list[ConflictValue]
    resolution: ConflictResolution
    selected_value: Any | None = None
    notes: str = ""


class ChallengerBlock(StrictModel):
    required: bool
    completed: bool
    material_objections: list[str] = Field(default_factory=list)


class ThesisValidationState(StrictModel):
    status: ThesisValidationStatus
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- investment-thesis.v1 : top level -------------------------------------


class InvestmentThesis(SchemaTaggedModel):
    """The Investment Analyst's sole output artifact.

    Enforces everything checkable from this document alone: shape, enums,
    forbidden fields anywhere in the tree, section/state bidirectional
    consistency, and that every cited evidence_id is declared in
    `evidence_ids_used`. Checks that need the run's other files (does a cited
    evidence_id actually exist, does section coverage match this run's
    analysis-scope.v1, is thesis_confidence within the TRACE-driven cap) live
    in `thesis_validation.py`.
    """

    schema_: Literal["investment-thesis.v1"] = Field(default=SCHEMA_VERSION_THESIS, alias="schema")
    run_id: str
    artifact_id: str
    generated_at: datetime
    security: SecurityRef
    analysis_mode: AnalysisMode
    policy_version: str
    worksheet_ref: WorksheetRef
    prior_thesis: PriorThesisRef
    section_states: dict[str, SectionState]
    conclusion: Conclusion
    key_claims: list[KeyClaim]
    sections: dict[str, ThesisSection] = Field(default_factory=dict)
    valuation: ValuationBlock
    scenarios: ScenarioSet
    conditions: ConditionsBlock
    known_conflicts: list[KnownConflict] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(default_factory=list)
    challenger: ChallengerBlock
    validation: ThesisValidationState
    human_review_required: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            problems = find_forbidden_fields(data)
            if problems:
                raise ValueError("; ".join(problems))
        return data

    @field_validator("human_review_required")
    @classmethod
    def _review_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_review_required cannot be disabled")
        return True

    @model_validator(mode="after")
    def _section_states_match_registry(self) -> "InvestmentThesis":
        known = set(SECTION_IDS)
        given = set(self.section_states)
        if given != known:
            problems = []
            missing = known - given
            extra = given - known
            if missing:
                problems.append(f"missing section_states for: {sorted(missing)}")
            if extra:
                problems.append(f"unknown section ids in section_states: {sorted(extra)}")
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _sections_only_for_changed_states(self) -> "InvestmentThesis":
        changed = {sid for sid, state in self.section_states.items() if state == "changed"}
        present = set(self.sections)
        if changed != present:
            problems = []
            missing_content = changed - present
            unexpected_content = present - changed
            if missing_content:
                problems.append(
                    f"section_states mark changed but sections has no entry: {sorted(missing_content)}"
                )
            if unexpected_content:
                problems.append(
                    f"sections has narrative content for a non-changed section: {sorted(unexpected_content)}"
                )
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _evidence_citations_are_declared(self) -> "InvestmentThesis":
        cited: set[str] = set()
        for claim in self.key_claims:
            cited.update(claim.evidence_ids)
        for section in self.sections.values():
            cited.update(section.evidence_ids)
        for conflict in self.known_conflicts:
            cited.update(value.evidence_id for value in conflict.values)
        undeclared = cited - set(self.evidence_ids_used)
        if undeclared:
            raise ValueError(
                f"evidence_ids cited in the artifact but missing from evidence_ids_used: {sorted(undeclared)}"
            )
        return self

    def cited_evidence_ids(self) -> set[str]:
        """Every evidence_id cited anywhere in this artifact (including
        `evidence_ids_used` itself)."""
        cited: set[str] = set(self.evidence_ids_used)
        for claim in self.key_claims:
            cited.update(claim.evidence_ids)
        for section in self.sections.values():
            cited.update(section.evidence_ids)
        for conflict in self.known_conflicts:
            cited.update(value.evidence_id for value in conflict.values)
        return cited


# --- analysis-scope.v1 -----------------------------------------------------


class ScopeTrigger(StrictModel):
    type: TriggerType
    occurred_at: datetime | None = None
    detail: str | None = None


class AnalysisScope(SchemaTaggedModel):
    """The deterministic, per-run scope contract an analysis-thesis is
    produced against. `mode` is the only agent/human input the scope mapper
    (Phase 3) needs; every list here is looked up from
    `investment-analysis-policy.yml`'s `mode_section_map`, never hand-picked.
    """

    schema_: Literal["analysis-scope.v1"] = Field(default=SCHEMA_VERSION_SCOPE, alias="schema")
    run_id: str
    subject: str
    asset_track: AssetTrack
    mode: AnalysisMode
    trigger: ScopeTrigger
    decision_horizon: AnalysisHorizon
    evaluate_sections: list[str] = Field(default_factory=list)
    preserve_sections: list[str] = Field(default_factory=list)
    not_applicable_sections: list[str] = Field(default_factory=list)
    required_evidence_domains: list[str] = Field(default_factory=list)
    optional_evidence_domains: list[str] = Field(default_factory=list)
    critical_gap_policy: CriticalGapPolicy

    @model_validator(mode="after")
    def _sections_partition_registry(self) -> "AnalysisScope":
        known = set(SECTION_IDS)
        buckets = {
            "evaluate_sections": self.evaluate_sections,
            "preserve_sections": self.preserve_sections,
            "not_applicable_sections": self.not_applicable_sections,
        }
        owner: dict[str, str] = {}
        problems: list[str] = []
        for bucket_name, sections in buckets.items():
            for section in sections:
                if section not in known:
                    problems.append(f"{bucket_name} contains unknown section id: {section}")
                    continue
                if section in owner:
                    problems.append(
                        f"section {section} appears in both {owner[section]} and {bucket_name}"
                    )
                    continue
                owner[section] = bucket_name
        missing = known - set(owner)
        if missing:
            problems.append(f"sections missing a state (not in any bucket): {sorted(missing)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _evidence_domains_are_known(self) -> "AnalysisScope":
        known = set(EVIDENCE_DOMAINS)
        unknown = {
            domain
            for domain in (*self.required_evidence_domains, *self.optional_evidence_domains)
            if domain not in known
        }
        if unknown:
            raise ValueError(f"unknown evidence domain(s): {sorted(unknown)}")
        return self
