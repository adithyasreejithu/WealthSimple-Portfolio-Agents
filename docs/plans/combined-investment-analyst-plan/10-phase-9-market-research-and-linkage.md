# Phase 9 — Market Researcher and market linkage

**Priority:** medium/high  
**Dependency:** market resource coverage adequate for intended claims

Retain `market-analyst-resources`, fill the highest-value unconfigured registry domains, then build the Market Researcher agent from scratch. Prioritize sources based on portfolio relevance, not theoretical completeness. No existing Market Researcher or non-resource skill is reused.

Use the resource skill's existing TRACE mapping unchanged: `fetched`/`ok` are TRACE `ok`; `stale`, `overdue`, `no_data`, `input_missing`, and `stale_leg` are `missing`; `not_configured` is `not_applicable` and excluded from the denominator. The trace subject remains the run date.

Outputs:

- versioned market landscape;
- change since prior landscape;
- security/sector relevance tags;
- upcoming event calendar;
- evidence IDs and confidence/gaps.

Acceptance criteria:

- analyst receives selected excerpts only;
- market claims cite registered market evidence;
- missing growth/labor/breadth sources remain explicit until implemented;
- market context changes scenario sensitivities but does not mechanically override fundamentals.
- benchmark output reports the existing market TRACE record without redefining its completeness arithmetic.

[Back to overview](00-overview.md)
