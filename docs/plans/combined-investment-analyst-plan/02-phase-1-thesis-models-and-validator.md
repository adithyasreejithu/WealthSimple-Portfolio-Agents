# Phase 1 — Thesis models and validator

**Priority:** critical  
**Dependency:** Phase 0

Build the validator as a new deterministic Python module before the new agent prompt. It is not a Claude skill and does not reuse a legacy validator skill.

Tasks:

1. Implement Pydantic models in `src/workspace/analysis_models.py`.
2. Add claim, citation locator, section state, scenario, trigger, confidence, conflict, and validation models.
3. Implement artifact path/hash registration.
4. Extend run validation to find and validate thesis artifacts.
5. Add scope-patch checks.
6. Add scenario and valuation recomputation interfaces.
7. Add forbidden action/weight/order checks.
8. Add confidence-cap validation.
9. Add fixtures for valid, warning, and invalid cases.
10. Keep TRACE validation limited to the existing TRACE structure and arithmetic; do not invent a universal pass/fail percentage.

Acceptance criteria:

- fabricated evidence ID fails;
- mutated evidence artifact fails hash validation;
- probabilities totaling 95% fail;
- unsupported High confidence fails when policy caps it at Low/Medium;
- changed content in a preserved section fails;
- `target_weight`, `shares_to_buy`, or broker fields fail;
- an explicit unknown passes when policy permits partial analysis;
- all tests are deterministic and offline;
- any TRACE fixture preserves `ok` / `missing` / `not_applicable` and excludes `not_applicable` from the denominator.

[Back to overview](00-overview.md)
