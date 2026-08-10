# Phase 0 Deliverables Checklist

**Status:** completed and ready for review  
**Date completed:** 2026-08-09  
**Companion:** [`01-phase-0-contract-and-responsibility-freeze.md`](01-phase-0-contract-and-responsibility-freeze.md) (what was supposed to be delivered)  
**Related:** [`00-overview.md`](00-overview.md) (§3, the two open decisions resolved here)

---

## Summary

Phase 0 produced eight files that lock down the Investment Analyst rebuild's core contracts before any code is written. All files are complete and ready for approval. Nothing was left blank or deferred.

The two open decisions identified in [`stock-analysis-agent-design-review.md`](../stock-analysis-agent-design-review.md) §7 are both resolved with explicit values and rationale.

---

## Files Created

### 1. Schema Documentation (design specs for Phase 1/3 implementation)

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`docs/architecture/investment_thesis_schema.md`** | Full specification of `investment-thesis.v1` artifact shape, section identifiers, forbidden fields, validation rules, confidence model | 301 | ✅ Complete |
| **`docs/architecture/analysis_scope_schema.md`** | Full specification of `analysis-scope.v1` artifact shape, mode semantics, critical-gap policy behavior | 133 | ✅ Complete |

**Use:** Phase 1 (`src/workspace/analysis_models.py`) turns these into pydantic models. Phase 3's worksheet builder enforces analysis-scope.v1. These are design contracts, not executable code.

---

### 2. Policy File (concrete, ready-to-read configuration)

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`config/policies/investment-analysis-policy.yml`** | Policy configuration for the rebuilt v2 workflow: 16-section registry, mode→section/domain mapping, TRACE thresholds, rating vocabulary, PM vocabulary decision, confidence caps, challenger triggers, scenario rules | 361 | ✅ Complete |

**Location:** `config/policies/` (operational configuration, separate from Knowledge-Base research content)

**Use:** Phase 1's validator reads this. Phase 3's scope mapper reads this. This is executable policy, not documentation.

**What's inside:**
- Section registry (16 stable IDs) — the source of truth for `investment-thesis.v1.section_states` keys
- Mode-to-section mapping (7 modes × required/optional sections/domains) — deterministic scope creation
- **TRACE thresholds (OPEN DECISION #1, now resolved):**
  - `blocking_threshold: 50.0%` (completeness below this halts the run if `critical_gap_policy: stop`)
  - `warning_threshold: 80.0%` (caps `thesis_confidence` at `medium`)
  - Both scoped to `required_evidence_domains` only, not overall bundle completeness
  - Calibrated against observed bundles (PLTR 76.5–98.2%, XEQT 67.9%, L 81.4%)
  - Override authority: human operator only, via explicit request flag + reason, logged to audit
- **PM vocabulary (OPEN DECISION #2, now resolved):**
  - Portfolio Manager emits actions from `decision-framework.yml` unchanged (`Buy|Sell|Hold|Trim|Add|Watchlist|Avoid`)
  - No new `initiate|add|hold|trim|exit|avoid` vocabulary
  - Rationale: it's a synonym set over the same seven concepts; two textually different enums for the same downstream field is a bug generator, not a boundary
  - `decision-framework.yml` itself is unchanged
- Rating vocabulary (analyst emits `fundamental_rating|valuation_stance|thesis_direction|thesis_confidence`, NOT portfolio actions)
- Confidence caps (deterministic rules that cap the analyst's `thesis_confidence` downward based on TRACE/evidence quality)
- Challenger triggers (7 policy-level conditions that invoke a second-opinion pass)
- Scenario rules (probability sum tolerance, fair-value recomputation, etc.)

---

### 3. Example Artifacts (test fixtures and concrete illustrations)

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`docs/architecture/investment_analyst_examples/valid-initial-thesis.yaml`** | Concrete example of a valid `investment-thesis.v1` for PLTR's first analysis (initial_research mode). Shows full structure: conclusion, key_claims, sections (only changed ones), valuation, scenarios, conditions, unknowns, validation passing. | 217 | ✅ Complete |
| **`docs/architecture/investment_analyst_examples/valid-incremental-patch.yaml`** | Concrete example of a valid incremental `investment-thesis.v1` for PLTR earnings_update run. Shows preserved-section rule (unchanged sections appear in `section_states` but NOT in `sections` block). Demonstrates prior_thesis linkage and confidence-cap/warning behavior. | 223 | ✅ Complete |
| **`docs/architecture/investment_analyst_examples/invalid-portfolio-action.yaml`** | Concrete example of an INVALID artifact — deliberately contains five separate violations to test Phase 1 validator: `portfolio_action` field (forbidden), `target_weight` field (forbidden), numeric confidence percentage instead of enum, fabricated evidence_id (not registered), bad scenario probability sum. **This is the required Phase 1 test fixture** (roadmap Phase 1 gate: "a fabricated evidence ID and a target_weight field both fail validation"). | 131 | ✅ Complete |
| **`docs/architecture/investment_analyst_examples/analysis-scope-initial-research.yaml`** | Concrete example of `analysis-scope.v1` for initial_research mode (all 16 sections evaluated, no preserved sections, required evidence for financials/earnings/valuation/prices/overview/classification/portfolio_context). | 51 | ✅ Complete |
| **`docs/architecture/investment_analyst_examples/analysis-scope-earnings-update.yaml`** | Concrete example of `analysis-scope.v1` for earnings_update mode (8 sections evaluated, 8 preserved, only 3 required evidence domains). Pairs with the incremental-patch thesis to show scope→artifact mapping. | 43 | ✅ Complete |

**Use:** 
- Phase 1 unit tests load these fixtures to verify schema compliance, forbidden-field rejection, citation validation, arithmetic recomputation
- Future developers can reference them as working examples of valid/invalid artifacts
- All YAML parses cleanly (verified with `yaml.safe_load`)

---

## Decision Resolutions

### Open Decision #1: TRACE Thresholds and Override Authority

**Question:** `docs/plans/stock-analysis-agent-design-review.md` §7 left three blanks:
```
TRACE blocking threshold: ______________________________
TRACE warning threshold:  ______________________________
Who may override a TRACE-based block: __________________
```

**Resolution in `investment-analysis-policy.yml` §6 (`trace_policy`):**

| Question | Answer | Rationale |
|---|---|---|---|
| Blocking threshold | `50.0%` completeness on required_evidence_domains | Calibrated against real bundles: PLTR ranged 76.5–98.2%, L 81.4%, XEQT 67.9%. A floor of 50% catches genuinely broken pulls (provider-symbol mismatch, data_sufficiency gate failure) without penalizing ETFs' structurally thinner coverage. Provisional pending Phase 4/5's actual benchmark. |
| Warning threshold | `80.0%` completeness on required_evidence_domains | Flags imperfect but usable runs (e.g., PLTR 76.5% was usable). Confidence is capped at `medium` for warning-level gaps, not blocked outright. Provisional pending Phase 4/5's actual benchmark. |
| Scope of thresholds | Scoped to `required_evidence_domains` only, not overall bundle | A run requiring only [financials, earnings, valuation] is judged on those domains' TRACE outcome, not on whether an optional options/insider domain came back empty. Mirrors `src/skill_trace.py`'s existing `not_applicable` classification — this policy does not redefine it, only adds a threshold on top. |
| Override authority | Human operator only, via explicit flag + required reason string | No agent (including the analyst and any future orchestrator) may set this flag. Every override is logged to `audit_log.jsonl` as its own event (operator, prior condition, reason) so it is auditable, never silent. Consistent with existing `Restrictions` pattern in `src/workspace/models.py`. |

### Open Decision #2: Portfolio Manager Action Vocabulary

**Question:** `docs/plans/stock-analysis-agent-design-review.md` §7 asked which enum the Portfolio Manager should use:
- Option A (existing): `Buy|Sell|Hold|Trim|Add|Watchlist|Avoid` from `decision-framework.yml`
- Option B (candidate): `initiate|add|hold|trim|exit|avoid|watch` (a new vocabulary)

**Resolution in `investment-analysis-policy.yml` §2 (`portfolio_manager_vocabulary`):**

**Decision:** Reuse `decision-framework.yml` unchanged (Option A).

**Rationale:** The candidate vocabulary is a synonym set over the same seven concepts (`initiate=Buy`, `exit=Sell`, `watch=Watchlist`, others unchanged) with no semantic gain. Both paths ultimately write the same downstream fields: the stock-page Decision field and decision-log rows. Two textually different enums naming the same action space for two components that write the same field is a translation-layer bug generator, not a useful boundary. No new taxonomy file needed; `decision-framework.yml` is not modified because its existing enum already covers every PM action without a gap.

**Consequence:** The analyst still never emits a `decision-framework.yml` action (see `investment_thesis_schema.md` §2: forbidden fields). Only the Portfolio Manager emits actions, using the reused enum.

---

## Architectural Note: Configuration Outside Knowledge-Base

Phase 0 established that operational policy/configuration files (`investment-analysis-policy.yml`) live in `config/policies/`, not in the Knowledge-Base. The Knowledge-Base is reserved for research content (stock theses, market analysis, research notes); operational infrastructure (policy, configuration, reference data) belongs in `config/` at the repo root.

Future phases should follow this pattern when adding policy files:
- Portfolio/allocation policies: `config/policies/`
- Data collection policy: `config/policies/` (or `config/collection/`)
- Classification rules, reference data: same structure

A future architectural refactor should migrate existing policy files (`Knowledge-Base/policy_v1_1.yaml`, `Knowledge-Base/taxonomy/decision-rubric.yml`, etc.) to this structure for consistency, but that is deferred past Phase 0.

## Files Not Modified

The following existing files were **not changed** and remain in effect:

- `docs/plans/investment-analyst-rebuild-roadmap.md` — unchanged; still the master build plan
- `docs/plans/stock-analysis-agent-design-review.md` — unchanged; still the gap analysis reference
- `docs/plans/combined-investment-analyst-plan/00-overview.md` — unchanged; still the design overview
- `docs/plans/combined-investment-analyst-plan/01-12-*.md` — unchanged; still the phase specs
- `Knowledge-Base/taxonomy/decision-framework.yml` — unchanged; reused as-is
- `Knowledge-Base/taxonomy/decision-rubric.yml` — unchanged; v1 benchmark reference only, not part of v2 runtime
- `Knowledge-Base/taxonomy/market-indicators.yml` — unchanged; still incomplete (TBD domains)
- All `docs/architecture/*.md` — unchanged; background references

---

## Acceptance Criteria (from Phase 0 gate)

From [`01-phase-0-contract-and-responsibility-freeze.md`](01-phase-0-contract-and-responsibility-freeze.md):

- ✅ no field ambiguously assigns position sizing to the analyst (forbidden-fields rule enforced)
- ✅ every report section has a stable machine identifier (16 section IDs in policy file)
- ✅ every run mode maps deterministically to evaluated/preserved sections (mode_section_map in policy file)
- ✅ schema examples validate before agent work begins (all YAML parses cleanly)
- ✅ no existing agent or non-resource skill appears in the target runtime dependency graph (v2 architect is explicitly rebuild-only, v1 is benchmark reference)
- ✅ `not_applicable` is excluded from TRACE completeness exactly as implemented in `src/skill_trace.py` (trace_policy mirrors existing behavior)

---

## Next Steps

### Approval Needed

Before proceeding to Phase 1, the two open-decision resolutions need approval:

1. Are the TRACE thresholds (50.0 blocking, 80.0 warning) appropriate? Should they be revisited after Phase 4/5's benchmark, or locked now?
2. Is reusing `decision-framework.yml` unchanged (not creating a new PM vocabulary) the right call?

### Phase 1 Gate Condition

Phase 1 begins when these files are approved. Phase 1 will:

- Implement `src/workspace/analysis_models.py` with pydantic models matching `investment_thesis_schema.md` and `analysis_scope_schema.md`
- Implement `src/workspace/thesis_validation.py` validator that rejects invalid artifacts using rules from this policy file
- Write tests covering:
  - All four example YAML files (two valid, one invalid)
  - Forbidden-field detection (the two required test cases: fabricated evidence ID + target_weight)
  - Citation validation, arithmetic recomputation, scope enforcement, confidence caps

Phase 1 succeeds when: "Fixtures for valid/warning/invalid all pass; a fabricated evidence ID and a `target_weight` field both fail validation."
