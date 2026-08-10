# Phase 0 Handoff — Key Changes for Future Sessions

**Date:** 2026-08-09  
**Status:** Phase 0 complete and committed to Agent-Development branch  
**Commit:** `37462b9 Phase 0: Contract and responsibility freeze`

---

## Critical Architecture Change

**Configuration files now live in `config/` at the repo root, NOT in `Knowledge-Base/`.**

The Knowledge-Base is reserved for research content only (stock theses, market analysis, research notes). Operational infrastructure (policy files, decision rules, configuration) belongs in `config/policies/`.

### What moved:
- `investment-analysis-policy.yml` was created at **`config/policies/investment-analysis-policy.yml`** (not in KB)

### Schema docs updated:
- `docs/architecture/investment_thesis_schema.md` — reference updated
- `docs/architecture/analysis_scope_schema.md` — reference updated
- `docs/plans/implementation/phase-0/deliverables-checklist.md` — architectural note added

### For future phases:
When adding new policy files, place them in `config/policies/` following this example.

A future architectural refactor may migrate existing policy files (`Knowledge-Base/policy_v1_1.yaml`, legacy decision-rubric.yml) to this structure for consistency, but that is deferred past Phase 0.

---

## Phase 0 Deliverables Summary

**8 files created:**
1. `docs/architecture/investment_thesis_schema.md` — `investment-thesis.v1` schema spec
2. `docs/architecture/analysis_scope_schema.md` — `analysis-scope.v1` schema spec
3. **`config/policies/investment-analysis-policy.yml`** — v2 workflow policy (TRACE thresholds, mode mappings, confidence caps)
4. `docs/architecture/investment_analyst_examples/valid-initial-thesis.yaml`
5. `docs/architecture/investment_analyst_examples/valid-incremental-patch.yaml`
6. `docs/architecture/investment_analyst_examples/invalid-portfolio-action.yaml`
7. `docs/architecture/investment_analyst_examples/analysis-scope-initial-research.yaml`
8. `docs/architecture/investment_analyst_examples/analysis-scope-earnings-update.yaml`

**Plus implementation tracking:**
- `docs/plans/implementation/phase-0/deliverables-checklist.md` — full inventory + decision rationale
- `docs/plans/implementation/phase-0/README.md` — folder structure guidance

---

## Two Open Decisions — Now Resolved

### Decision #1: TRACE Thresholds
- **Blocking threshold:** 50.0% completeness on `required_evidence_domains`
- **Warning threshold:** 80.0% completeness on `required_evidence_domains`
- **Override authority:** Human operator only, via explicit flag + reason, logged to audit
- **Location:** `config/policies/investment-analysis-policy.yml` §6

### Decision #2: Portfolio Manager Vocabulary
- **Chosen:** Reuse `decision-framework.yml` unchanged (`Buy|Sell|Hold|Trim|Add|Watchlist|Avoid`)
- **Not chosen:** New vocabulary (`initiate|add|hold|trim|exit|avoid`) — it's a synonym set, not a boundary
- **Consequence:** No new taxonomy file created; analyst still never emits portfolio actions
- **Location:** `config/policies/investment-analysis-policy.yml` §2

---

## Ready for Phase 1

Phase 0 gate condition is met. Phase 1 begins when approved and can now implement:
- `src/workspace/analysis_models.py` (pydantic models from the schemas)
- `src/workspace/thesis_validation.py` (validator using the policy)
- Unit tests loading the example fixtures

See `docs/plans/implementation/phase-0/deliverables-checklist.md` for full acceptance criteria and next-phase details.

---

## Key Files to Remember

| File | Purpose |
|---|---|
| `config/policies/investment-analysis-policy.yml` | Operational policy for v2 analyst (read by Phase 1 validator, Phase 3 worksheet builder) |
| `docs/architecture/investment_thesis_schema.md` | Contract that `investment-thesis.v1` artifacts must match |
| `docs/architecture/analysis_scope_schema.md` | Contract that `analysis-scope.v1` scope specifications must match |
| `docs/plans/implementation/phase-0/deliverables-checklist.md` | Full inventory of what Phase 0 created and why |
| `docs/plans/investment-analyst-rebuild-roadmap.md` | Master build plan (unchanged from your input) |

All YAML files parse cleanly and all schema references are correct as of this handoff.
