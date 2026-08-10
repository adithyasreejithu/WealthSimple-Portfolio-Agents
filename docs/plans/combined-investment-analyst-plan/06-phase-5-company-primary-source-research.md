# Phase 5 — Company primary-source research

**Priority:** high  
**Dependency:** benchmark confirms the vertical slice is sound

Build the bounded `company-research-resources` collector described in Stage 3 from scratch. It is a new allowed `*-resources` skill and does not wrap an existing collection skill. Add primary-source evidence in this order:

1. annual filing;
2. latest quarterly/interim filing;
3. latest earnings release;
4. accessible transcript;
5. investor presentation;
6. material post-filing news.

Then extend the worksheet with:

- segment/geographic revenue;
- customer concentration;
- management guidance;
- stated strategy and capital allocation;
- disclosed risks;
- share-count and SBC context;
- competitor and industry evidence when decision-relevant.

Integrate the existing shared TRACE writer from `src/skill_trace.py`. Use the implemented `ok` / `missing` / `not_applicable` outcomes, existing completeness arithmetic, shared text/JSONL outputs, and run-audit integration. The workspace does not define the company-research domains or applicability rules, so leave them blank until supplied:

```text
Company-research TRACE domains: _________________________
Company-research applicability rules: ___________________
```

Acceptance criteria:

- every document has verified entity and reporting period;
- primary-source precedence is enforced;
- amended/restated documents are detected or flagged;
- page/section locators are available for material claims;
- extraction failure becomes registered missing/partial evidence;
- analyst context stays bounded through selected excerpts.
- TRACE writing failure does not sink a successful collection run;
- the new collector has no dependency on an existing non-resource skill or agent.

[Back to overview](00-overview.md)
