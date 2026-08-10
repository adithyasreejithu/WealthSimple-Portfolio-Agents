# Phase 1 Deliverables Checklist

**Status:** completed and ready for review  
**Date completed:** 2026-08-09  
**Commits:** `f84db31` (Phase 1 deliverables), `190184c` (policy-path fix)  
**Companion:** [`../../combined-investment-analyst-plan/02-phase-1-thesis-models-and-validator.md`](../../combined-investment-analyst-plan/02-phase-1-thesis-models-and-validator.md) (what was supposed to be delivered)

---

## Summary

Phase 1 implemented the deterministic Python validator for `investment-thesis.v1` and `analysis-scope.v1` artifacts **before any agent code exists**. The validator enforces every rule from the Phase 0 contracts (schemas, policy thresholds, forbidden fields, section/evidence consistency, arithmetic recomputation) at both the model level (shape, enums, cross-field invariants) and cross-file level (evidence registry resolution, scope consistency, TRACE-driven confidence caps).

All deliverables are production-ready and tested. The phase met its gate condition: a fabricated evidence ID and a `target_weight` field both fail validation, demonstrating that no LLM-authored artifact can slip through containing Portfolio Manager fields or unregistered evidence citations.

---

## Files Created

### 1. Pydantic Models

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`src/workspace/analysis_models.py`** | Complete pydantic models for both artifact schemas: `InvestmentThesis` and `AnalysisScope` (top level), plus 25 sub-models (claims, sections, valuation, scenarios, conditions, conflicts, etc.). Reads policy file at import time (SECTION_IDS, ETF not-applicable set, evidence domains, TRACE/scenario tunables). Enforces forbidden-field rejection at the model level (Portfolio Manager fields anywhere in the tree). | 643 | ✅ Complete |

**Key design decisions:**
- `extra="forbid"` on every model, so typos are hard failures, not silent no-ops
- `schema_` field with `alias="schema"` to avoid shadowing pydantic's `BaseModel.schema()` method
- Forbidden fields detected via `find_forbidden_fields()` before-validator (scans the raw payload tree for any Portfolio Manager field or decision-framework action, reports all violations in one pass)
- All policy constants (16 section IDs, ETF exceptions, 16 evidence domains, TRACE thresholds, scenario tolerance) read from `config/policies/investment-analysis-policy.yml` at import time; hardcoded fallbacks only activate if the file fails to load
- Vocabulary enums (`Confidence`, `FundamentalRating`, `ValuationStance`, etc.) defined once, reused throughout

---

### 2. Cross-File Validator

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`src/workspace/thesis_validation.py`** | Deterministic validator that checks what a single document cannot: evidence citation resolution against `evidence/sources.jsonl` (including content-hash mutation detection), section coverage against `analysis-scope.v1`, and TRACE-driven `thesis_confidence` cap. Returns structured result: `{ok, status, errors, warnings, thesis}` with status following schema doc's three tiers (`invalid|valid_with_warnings|valid`). | 185 | ✅ Complete |

**Three main checks:**
1. **Evidence citation resolution** (`check_evidence_citations`): Every cited evidence_id must exist in the run's registry, not be marked as `missing`, and (if an artifact is registered) have an intact content hash. Reports all three violation types distinctly (fabricated, gap-citation, mutated).
2. **Section scope consistency** (`check_section_scope`): Validates against an `analysis-scope.v1` — each evaluated section is `changed` with narrative, each preserved section carries no replacement content, each not-applicable section is marked and has no sections entry.
3. **Confidence cap enforcement** (`check_confidence_cap`): Reads required domains and their TRACE status, applies policy rules (missing required domain → cap at `low`, completeness below 80% → cap at `medium`, unknown gate → cap at `low`), flags if the analyst's stated confidence exceeds the cap.

**Error collection pattern:**
- Mirrors existing `validation.py` and evaluate-stock-decision's `validate_recommendation.py`: collects every problem, returns them all, never raises on the first
- Allows callers (Phase 3 worksheet builder, Phase 4 analyst invocation) to surface a complete list of issues, not just the first blocker

---

### 3. Test Suite

| File | Purpose | Lines | Status |
|---|---|---|---|
| **`tests/test_investment_thesis_validation.py`** | 40 comprehensive tests organized into 9 test classes, covering every requirement in the Phase 1 gate: valid fixtures, forbidden fields (the two gate-required cases: fabricated evidence ID and `target_weight`), scenario probability math, section/evidence consistency, evidence-registry resolution, section-scope mismatches, confidence-cap behavior. Tests use synthetic fixtures (ticker `TEST`, no real data) and a temp `run_dir` fixture for evidence-registry tests. | 655 | ✅ Complete |

**Test coverage:**
- `ValidFixtureTest` (3 tests): valid/warning thesis parses; shape is sound
- `ForbiddenFieldTest` (6 tests): `target_weight`, `shares_to_buy`, `broker_order`, `portfolio_action`, `execution_price` rejected at model level; `find_forbidden_fields()` reports every violation
- `ScenarioMathTest` (5 tests): probabilities summing to 1.0 pass; 90%, 95%, and values outside tolerance fail; tolerance is read from policy (0.001)
- `SectionConsistencyTest` (3 tests): changed sections must have narrative, preserved sections must not, all 16 section IDs must have a state
- `EvidenceDeclarationTest` (1 test): citations must be in `evidence_ids_used`
- `AnalysisScopeTest` (3 tests): sections partition into evaluate/preserve/not_applicable, no duplicates, no unknown domains
- `EvidenceCitationResolutionTest` (4 tests): fabricated ID fails, mutated hash fails, missing-status citation fails, intact evidence passes
- `ValidateThesisEndToEndTest` (5 tests): valid thesis with scope/evidence passes; fabricated ID/target_weight/scope mismatch fail at full validation; missing run_dir/scope produce warnings not errors
- `ConfidenceCapTest` (7 tests): completeness thresholds (60→medium, 30→low, 95→none), unknown gate caps at low, failed gate caps at low, policy cap applied correctly

**All tests pass:** `uv run python -m unittest tests.test_investment_thesis_validation` → 40/40 ✅

---

### 4. Package Updates

| File | Purpose | Change | Status |
|---|---|---|---|
| **`src/workspace/__init__.py`** | Updated exports to expose the two new modules (`analysis_models`, `thesis_validation`) and their public types (`InvestmentThesis`, `AnalysisScope`, `validate_thesis`) following existing package conventions. | Added 4 imports, 5 names to `__all__` | ✅ Complete |
| **`src/config.py`** | Added `POLICIES_FOLDER = BASE_DIR / "config" / "policies"` constant, following Phase 0's architectural decision to keep operational configuration separate from research knowledge-base. Includes reference comment pointing to the handoff doc. | Added constant + comment | ✅ Complete |

---

### 5. Bug Fix

| Issue | Commit | Status |
|---|---|---|
| **Silent policy-loading failure after Phase 0 refactor** | `190184c` | ✅ Fixed |

The user's commit `24e6272` relocated `investment-analysis-policy.yml` from `Knowledge-Base/taxonomy/` to `config/policies/` and updated schema docs, but my Phase 1 code was still pointing at the old path. Since `_load_policy()` swallows `OSError`, this failed silently: all policy-derived constants (SECTION_IDS, TRACE thresholds, etc.) ran off hardcoded fallbacks instead of the real file. Tests passed because fallbacks matched current policy values, but any future policy edit would never have propagated.

**Fix:** Added `config.POLICIES_FOLDER`, updated `analysis_models.py` to use it, verified policy now loads (`_POLICY` is non-empty) and all values match the real file.

---

## Acceptance Criteria (from Phase 1 gate)

From [`../../combined-investment-analyst-plan/02-phase-1-thesis-models-and-validator.md`](../../combined-investment-analyst-plan/02-phase-1-thesis-models-and-validator.md):

- ✅ **Fixtures for valid/warning/invalid all pass:** `test_valid_thesis_parses`, `test_warning_thesis_with_capped_confidence_is_still_valid_shape`, `test_fabricated_evidence_id_fails_full_validation` + `test_target_weight_field_fails_full_validation`
- ✅ **Fabricated evidence ID fails validation:** `EvidenceCitationResolutionTest.test_fabricated_evidence_id_fails` + full-validation integration test
- ✅ **`target_weight` field fails validation:** `ForbiddenFieldTest.test_target_weight_is_rejected` + full-validation integration test
- ✅ **Scenario probabilities must sum to 1.0 ± tolerance:** `ScenarioMathTest` covers all edges (exact, inside/outside tolerance, default 0.001 from policy)
- ✅ **Confidence is capped when TRACE is incomplete or gates are unknown:** `ConfidenceCapTest` covers all three cap-trigger cases (completeness, failed domain, unknown domain)

---

## Quality Assurance

**Test suite:** 40/40 tests pass  
**Full repo test suite:** 937/937 tests pass (no regressions)  
**Linting:** No pydantic warnings (fixed via field alias for `schema`)  
**Policy file integration:** Policy actively loads from `config/policies/investment-analysis-policy.yml`, values confirmed against file  
**Forbidden field detection:** Scans entire payload tree, reports all violations  

---

## Known Issues & Handoffs

### 1. Probability Tolerance: 0.001 vs 0.02

The task description referenced ±0.02, but `config/policies/investment-analysis-policy.yml` specifies 0.001 (and the schema docs confirm 0.001). The code uses the policy file's value (0.001). If 0.02 is the intended tolerance, update it in the policy file; the validator will pick up the new value at the next import.

### 2. Database File in Git History (from Phase 0 refactor)

Commit `24e6272` accidentally committed `tests/tmpqinoi8gr/portfolio.duckdb` (11MB binary). CLAUDE.md explicitly forbids local database files in the repo. This requires either:
- A new commit deleting the file (leaves it in history, ~11MB increase is permanent)
- A history rewrite (destructive, use only with explicit user approval)

No action taken in Phase 1; flagged for you to decide.

---

## Files Modified by This Phase

### Created
- `src/workspace/analysis_models.py` — Pydantic models (643 lines)
- `src/workspace/thesis_validation.py` — Cross-file validator (185 lines)
- `tests/test_investment_thesis_validation.py` — Test suite (655 lines)
- `docs/plans/implementation/phase-1/README.md` — This folder's index
- `docs/plans/implementation/phase-1/deliverables-checklist.md` — This file
- `docs/plans/implementation/phase-1/HANDOFF.md` — Handoff notes

### Modified
- `src/workspace/__init__.py` — Added 4 imports, 5 exports (+8/-4 lines)
- `src/config.py` — Added `POLICIES_FOLDER` constant (+5 lines)

### Total diff: `git diff f84db31~1 190184c`
- **4 files created, 2 files modified**
- **1,483 insertions(+), 4 deletions(-)**

---

## Next Steps

### For Approval Review
1. Verify the gate condition (fabricated evidence ID + target_weight both fail) by running `uv run python -m unittest tests.test_investment_thesis_validation.EvidenceCitationResolutionTest.test_fabricated_evidence_id_fails -v` and `ForbiddenFieldTest.test_target_weight_is_rejected -v`
2. Spot-check the policy-file loading by running the Python snippet from the handoff (confirms `_POLICY` is non-empty)
3. Confirm `uv run python -m unittest discover -s tests` still shows 937/937 passing

### Before Phase 2 Begins
1. Decide on the 0.001 vs 0.02 tolerance (if different, update `config/policies/investment-analysis-policy.yml` before Phase 2 starts)
2. Decide what to do about `tests/tmpqinoi8gr/portfolio.duckdb` in git history
3. Phase 2 will implement `src/workspace/financial_metrics.py` and `src/workspace/valuation.py` (cheap data-gap closure: technicals + ratio modules); these depend only on Phase 0 & 1 artifacts being solid

---

## Reference

- **Roadmap:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md) (master plan, unchanged)
- **Phase 0 handoff:** [`../phase-0/HANDOFF.md`](../phase-0/HANDOFF.md) (config/policies/ architectural note)
- **Commit: Phase 1 deliverables:** `f84db31`
- **Commit: Policy path fix:** `190184c`
