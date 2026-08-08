"""Schemas for every artifact a run writes.

pydantic handles shape: required fields, enums, types, timestamp parsing.
Semantic rules that need the run's other files to check -- does this evidence
ID exist, does this path stay inside the run, is this status change legal --
live in `validation.py`, `evidence.py`, and `state.py` instead, because a
model cannot see beyond its own document.

Every model sets `extra="forbid"`. A typo'd key in a hand-edited request or an
agent-authored output is a mistake worth catching at load time, not a field
that silently does nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import state as state_module

SCHEMA_VERSION = "1.0"
WORKFLOW_VERSION = "0.1.0"

EvidenceStatus = Literal["pending", "available", "partial", "missing", "stale", "invalid"]
ComponentAvailability = Literal["available", "unavailable", "deferred"]
AgentStatus = Literal["pending", "complete", "partial", "failed"]
ReviewStatus = Literal["pending", "approved", "rejected", "changes_requested"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- request -----------------------------------------------------------


class SubjectIdentifiers(StrictModel):
    """Every identifier is optional: a market-wide or portfolio-wide request
    legitimately has no ticker, and the spec requires that to be expressible
    rather than forced into a placeholder value."""

    ticker: str | None = None
    isin: str | None = None
    cusip: str | None = None
    name: str | None = None


class Subject(StrictModel):
    type: Literal["security", "portfolio", "market", "other"] = "other"
    identifiers: SubjectIdentifiers = Field(default_factory=SubjectIdentifiers)


class RequestBody(StrictModel):
    question: str
    context: str | None = None


class Restrictions(StrictModel):
    """Defaults are the safe answers. A request may not turn off
    `research_only`, `do_not_invent_data`, or `human_review_required`, and may
    not turn on `execute_trades` -- the field validators below refuse, so the
    guarantee cannot be weakened by editing a request file."""

    research_only: bool = True
    execute_trades: bool = False
    do_not_invent_data: bool = True
    human_review_required: bool = True

    @field_validator("research_only", "do_not_invent_data", "human_review_required")
    @classmethod
    def _must_stay_on(cls, value: bool, info) -> bool:
        if value is not True:
            raise ValueError(f"{info.field_name} cannot be disabled")
        return True

    @field_validator("execute_trades")
    @classmethod
    def _must_stay_off(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("execute_trades cannot be enabled; this system is research-only")
        return False


class Request(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str | None = None
    created_at: datetime | None = None
    mode: str
    subject: Subject = Field(default_factory=Subject)
    request: RequestBody
    required_analysis: list[str] = Field(default_factory=list)
    restrictions: Restrictions = Field(default_factory=Restrictions)


# --- run metadata ------------------------------------------------------


class RunMetadata(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    workflow_version: str = WORKFLOW_VERSION
    run_id: str
    mode: str
    status: str = state_module.CREATED
    trigger: str = "cli"
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    completed_at: datetime | None = None
    # Which pieces of the system were actually reachable for this run. A
    # missing agent or policy engine is recorded here as unavailable rather
    # than being silently skipped, so a reader can tell "not run" from
    # "ran and found nothing".
    available_components: dict[str, ComponentAvailability] = Field(default_factory=dict)
    planned_steps: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    human_review_status: ReviewStatus = "pending"
    archived: bool = False
    archived_at: datetime | None = None
    archive_path: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in state_module.STATUSES:
            raise ValueError(f"unknown status {value!r}")
        return value


# --- evidence ----------------------------------------------------------


class EvidenceRecord(StrictModel):
    """One row of `evidence/sources.jsonl`.

    `artifact_path` is run-relative so the record survives archiving, and is
    absent for evidence registered as missing -- which is the point of the
    `missing` status: the gap is recorded explicitly instead of the field
    being filled with a guess.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    evidence_id: str
    run_id: str
    evidence_type: str
    source_name: str
    source_url: str | None = None
    artifact_path: str | None = None
    retrieved_at: datetime | None = None
    as_of: datetime | None = None
    status: EvidenceStatus
    freshness: str | None = None
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"
    content_hash: str | None = None
    collection_method: str | None = None
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- context manifest --------------------------------------------------


class ManifestEvidence(StrictModel):
    evidence_id: str
    evidence_type: str
    status: EvidenceStatus
    path: str | None = None


class ContextManifest(StrictModel):
    """The contract between stages: what a receiving agent may use, and what
    it must treat as unavailable. `missing_information` is as load-bearing as
    the evidence list -- it is how a downstream stage knows not to invent."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    task: str
    generated_at: datetime
    target_stage: str | None = None
    input_paths: list[str] = Field(default_factory=list)
    evidence: list[ManifestEvidence] = Field(default_factory=list)
    calculation_paths: list[str] = Field(default_factory=list)
    prior_thesis_path: str | None = None
    required_outputs: list[str] = Field(default_factory=list)
    restrictions: Restrictions = Field(default_factory=Restrictions)
    missing_information: list[str] = Field(default_factory=list)
    validation_status: Literal["ok", "incomplete", "invalid"] = "incomplete"


# --- agent output ------------------------------------------------------


class PreliminaryAction(StrictModel):
    action: str | None = None
    confidence: str | None = None


class Handoff(StrictModel):
    next_stage: str | None = None


class AgentOutput(StrictModel):
    """Shared envelope every future specialist writes into `agent_outputs/`.

    Defined now, with no reasoning behind it, so the stages built later have a
    contract to target. `evidence_ids_used` is checked against the run's
    registry by `validation.py` -- an output citing evidence that was never
    registered is a fabrication and fails the run.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    agent: str
    status: AgentStatus
    generated_at: datetime
    findings: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(default_factory=list)
    preliminary_action: PreliminaryAction = Field(default_factory=PreliminaryAction)
    handoff: Handoff = Field(default_factory=Handoff)
    human_review_required: bool = True

    @field_validator("human_review_required")
    @classmethod
    def _review_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_review_required cannot be disabled")
        return True


# --- decision proposal -------------------------------------------------


class PolicyCheck(StrictModel):
    name: str
    result: Literal["pass", "fail", "warn", "unavailable"]
    detail: str | None = None


class DecisionProposal(StrictModel):
    """A proposal, never an order. `trade_executed` is pinned to False by the
    type system; `validation.py` additionally scans for broker/fill/order
    fields anywhere in the run, so the guarantee does not rest on this model
    alone."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    proposal_id: str
    run_id: str
    subject: Subject
    proposed_action: str
    confidence: str | None = None
    summary: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    review_status: ReviewStatus = "pending"
    human_approval_required: Literal[True] = True
    trade_executed: Literal[False] = False
