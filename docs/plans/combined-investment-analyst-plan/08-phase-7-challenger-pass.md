# Phase 7 — Challenger pass

**Priority:** high for deep research; medium for routine reviews  
**Dependency:** stable analyst output and validation

Create the challenger definition from scratch and implement trigger policy plus a separate challenge/revision artifact pair. Measure whether the challenger finds real unsupported claims or merely increases verbosity. It receives prepared run artifacts and has no legacy-agent or non-resource-skill dependency.

Acceptance criteria:

- challenge output is retained and cited in audit events;
- primary thesis cannot silently discard a material objection;
- revision explains accepted/rejected objections;
- routine no-change monitoring skips the extra model call;
- benchmark shows a measurable quality benefit for triggered cases.

[Back to overview](00-overview.md)
