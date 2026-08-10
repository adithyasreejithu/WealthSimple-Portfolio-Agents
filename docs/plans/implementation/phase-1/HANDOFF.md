# Phase 1 Handoff — Key Changes and Context for Future Sessions

**Date:** 2026-08-09  
**Status:** Phase 1 complete and committed to Agent-Development branch  
**Commits:** `f84db31` (Phase 1 deliverables), `190184c` (policy-path fix)

---

## What Phase 1 Built

Deterministic Python validator for `investment-thesis.v1` and `analysis-scope.v1` artifacts that **enforces the Phase 0 contracts before any LLM agent exists**. The validator catches:

- **Forbidden fields anywhere in the artifact** (Portfolio Manager fields: `target_weight`, `shares_to_buy`, `portfolio_action`, `broker_order`, etc.; decision-framework actions masquerading as field values)
- **Evidence citation gaps** (unregistered IDs, missing-status citations, mutated artifacts)
- **Section/state mismatches** (changed sections without narrative, preserved sections with replacement content, missing states)
- **Scenario arithmetic** (probabilities sum to 1.0 ± policy tolerance)
- **TRACE-driven confidence capping** (TRACE completeness < 80% caps at `medium`, < 50% or unknown gate caps at `low`)

All 40 tests pass; full suite (937 tests) shows no regressions.

---

## Critical Issue Found and Fixed

**Silent policy-loading failure after Phase 0 refactor**

Commit `24e6272` moved `investment-analysis-policy.yml` from `Knowledge-Base/taxonomy/` to `config/policies/` (correct per Phase 0 handoff), but the Phase 1 code still pointed at the old path. Since `_load_policy()` swallows `OSError` silently, all policy-derived constants ran off hardcoded fallbacks instead of the real file.

**Status:** Fixed in commit `190184c`. Verified the policy now loads: `_POLICY` is non-empty, constants match the real file.

**For future phases:** The fallbacks are a safety net, not a feature — if policy loading ever fails, there is no warning. If Phase 2 reads from the policy file, it must also verify load success explicitly in its setup or tests.

---

## Architecture: `config/policies/` is Operational Configuration

Phase 0 established that operational policy/configuration files go in `config/policies/`, **not** in `Knowledge-Base/`. The split is:

- **`Knowledge-Base/`** — Research content only (stock theses, market research, analysis notes)
- **`config/policies/`** — Operational configuration (decision rules, TRACE thresholds, taxonomy, policy)

This is hardened in `src/config.py` with a `POLICIES_FOLDER` constant. Future policy files should follow this pattern.

A future architectural refactor might migrate existing policy files (`Knowledge-Base/policy_v1_1.yaml`, `Knowledge-Base/taxonomy/decision-rubric.yml`) to `config/policies/`, but that is deferred past Phase 0. Do not do it in Phase 2.

---

## Known Issues — No Action Taken

### 1. Probability Tolerance: Policy says 0.001, task text said ±0.02

The code reads `config/policies/investment-analysis-policy.yml`'s `scenario_rules.probability_sum_tolerance: 0.001` (it's the authoritative source). Tests enforce this. If you want 0.02, update the policy file; the validator will pick it up at import time.

### 2. Binary File in Git History

Commit `24e6272` (Phase 0 refactor) accidentally committed `tests/tmpqinoi8gr/portfolio.duckdb` (11MB binary). This violates CLAUDE.md ("Do not commit secrets, mailbox credentials, or local database files"). Removing it now requires either:
- A new commit deleting it (11MB stays in git history forever)
- A history rewrite (destructive; needs explicit user approval)

No Phase 1 action taken. Decide before Phase 2.

---

## Files to Remember

| File | Purpose | Notes |
|---|---|---|
| `src/workspace/analysis_models.py` | Pydantic models for both artifact schemas | Reads policy at import time; forbidden-field detection via before-validator |
| `src/workspace/thesis_validation.py` | Cross-file validator | Error-collection pattern; no early exit on first error |
| `tests/test_investment_thesis_validation.py` | 40 tests covering all gate conditions | Uses synthetic ticker `TEST`, temp run_dir fixture |
| `config/policies/investment-analysis-policy.yml` | Read-only dependency | Authoritative source for SECTION_IDS, ETF exceptions, evidence domains, TRACE thresholds |
| `src/config.py` | `POLICIES_FOLDER` constant | New in Phase 1, separates operational config from Knowledge-Base |
| `docs/plans/implementation/phase-1/deliverables-checklist.md` | Complete inventory of Phase 1 | What was built, what was fixed, what needs you to decide |

---

## What Phase 2 Depends On

Phase 2 will build the cheap data-gap closure (technicals + ratio modules). It depends on Phase 1 being solid:

- Models enforce the artifact contract so Phase 2 (and later Phase 3/4) never has to defend against invalid shape
- Validator catches evidence/scope/confidence problems before they reach an agent
- All policy constants are centralized in one file (Phase 2 can read them just like Phase 1 does)

Phase 2 does not depend on Phase 1's tests, only on the models/validator being correct. Reuse the test patterns if you want, but Phase 2's own tests should be deterministic and mock-friendly (no real network/database).

---

## Quick Verification (for a new session)

If you pick this up later and want to confirm Phase 1 is still working:

```bash
# Run Phase 1 test suite
uv run python -m unittest tests.test_investment_thesis_validation -v

# Verify policy file loads
uv run python -c "
import sys
sys.path.insert(0, 'src')
from workspace.analysis_models import _POLICY, SECTION_IDS, SCENARIO_PROBABILITY_TOLERANCE
print('Policy loaded:', bool(_POLICY))
print('Sections:', len(SECTION_IDS))
print('Tolerance:', SCENARIO_PROBABILITY_TOLERANCE)
"

# Run full suite (should still be 937 tests)
uv run python -m unittest discover -s tests
```

All three should show success. If policy fails to load, check that `config/policies/investment-analysis-policy.yml` exists and is valid YAML.

---

## Ready for Phase 2

The Phase 1 gate is met. Phase 2 can begin when approved.

**Watch the numbering.** The roadmap inserted the cheap data-gap closure as its Phase 2,
so it no longer lines up with `combined-investment-analyst-plan/`. The next phase's
contract is the **roadmap's** Phase 2 row plus its "Phase 2 design decision — reuse
boundary" section in `../../investment-analyst-rebuild-roadmap.md`, with the binding
file boundary in `../phase-2/design-decisions.md`. The combined plan's
`03-phase-2-worksheet-builder.md` is the roadmap's **Phase 3**, not this one.
