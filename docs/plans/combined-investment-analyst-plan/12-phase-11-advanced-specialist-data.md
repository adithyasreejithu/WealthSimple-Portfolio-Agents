# Phase 11 — Advanced specialist data

**Priority:** later

Add only after core value is demonstrated:

- technical/relative-strength calculations;
- IV/OI history, Greeks, skew history, and expected-move analysis;
- insider filing parser;
- institutional snapshot history;
- industry-specific financial models;
- ETF analyst track;
- automated monitoring scheduler.

Each new dataset must have a clear decision use case, freshness rule, evidence contract, offline tests, and cost/latency budget.

Any skill introduced for these datasets must be a new purpose-built `*-resources` skill. Each data-collecting resource skill must use the existing `src/skill_trace.py` model and outputs. Dataset-specific TRACE domains and applicability rules remain blank until the dataset contract is defined; no values are inferred in this plan.

---

[Back to overview](00-overview.md)
