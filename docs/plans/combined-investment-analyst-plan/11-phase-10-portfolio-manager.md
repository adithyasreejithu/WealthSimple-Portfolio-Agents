# Phase 10 — Portfolio Manager

**Priority:** high eventual value, but build only after validated theses exist  
**Dependency:** reliable analyst artifacts and portfolio-resource contract

Build a new deterministic `portfolio-manager-resources` skill first, reusing shared `src/analytics.py` infrastructure for allocation, look-through exposure, overlap, currency, concentration, and risk. Then build the Portfolio Manager agent and policy validator from scratch. No existing agent or non-resource skill is reused.

Because `portfolio-manager-resources` is a new data-collecting resource skill, it must use the existing shared TRACE writer. Its TRACE domains and applicability rules are not defined in the workspace and remain blank:

```text
Portfolio-resource TRACE domains: _______________________
Portfolio-resource applicability rules: _________________
```

Acceptance criteria:

- action and size are derived only from valid/current theses and current portfolio context;
- stale classification or positions block the decision proposal;
- all policy limits are checked deterministically;
- proposal remains human-review-required and trade-executed false;
- analyst conclusion and portfolio action can legitimately differ and both are displayed.
- resource collection preserves the existing `ok` / `missing` / `not_applicable` TRACE arithmetic.

[Back to overview](00-overview.md)
