# Phase 2 — Worksheet builder over retained resource skills

**Priority:** critical  
**Dependency:** Phases 0–1

This is the first integration with the retained `investment-analyst-resources` skill. The worksheet builder itself is a new deterministic Python module/stage, not a skill.

Tasks:

1. Read the registered `investment-analyst-resources.v1` bundle programmatically.
2. Resolve and validate ticker/provider identity.
3. Normalize dates, periods, units, and currencies.
4. Map resource fields into stable worksheet fields.
5. Port or centralize deterministic metrics without duplicating formulas unnecessarily.
6. Add annual/quarterly cadence labels so metrics cannot be confused.
7. Add evidence lineage to every worksheet value.
8. Add evidence health and critical-gap evaluation.
9. Add prior-thesis summary and base hash.
10. Add scope-specific section requirements.
11. Generate compact analyst context.
12. Assert a context size ceiling in tests.
13. Read the existing TRACE record carried in the investment resource digest/bundle.
14. Preserve the existing TRACE arithmetic and the distinction between actual `missing` fields and `not_applicable` fields.
15. Keep TRACE completeness separate from policy-defined criticality; the workspace does not supply a universal blocking threshold.

Initial worksheet limitations must be explicit:

- business-quality evidence may be shallow before Phase 5;
- expectations may be current-snapshot only;
- options are snapshot only;
- insider and institutional intent/history are limited;
- some market domains are not configured.

Acceptance criteria:

- PLTR fixture generates the same worksheet deterministically;
- missing options do not penalize a security when options are non-applicable/optional;
- missing critical financial evidence blocks a full initial rating under the selected policy;
- quarterly and annual metrics have distinct names and periods;
- no raw long series appears in analyst context;
- context includes all evidence IDs needed to support the displayed facts.
- worksheet evidence health agrees with the source TRACE record and does not turn `not_applicable` fields into gaps;
- no existing non-resource skill is called by the worksheet stage.

[Back to overview](00-overview.md)
