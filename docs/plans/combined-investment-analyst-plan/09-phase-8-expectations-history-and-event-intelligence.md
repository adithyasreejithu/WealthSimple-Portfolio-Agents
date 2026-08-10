# Phase 8 — Expectations history and event intelligence

**Priority:** high for earnings-driven use cases  
**Dependency:** core analyst stable

New capability required:

- persist dated consensus snapshots;
- persist company guidance snapshots;
- link snapshots to earnings events and research runs;
- calculate revisions and surprise against the pre-event snapshot;
- retain source and retrieval time.

Any Claude skill used to collect this dataset must be a new purpose-built `*-resources` skill. Deterministic persistence/calculation code may instead live in Python modules. A data-collecting resource skill must use the existing shared TRACE writer; the expectations TRACE domains and applicability rules are not defined in the workspace:

```text
Expectations TRACE domains: _____________________________
Expectations applicability rules: _______________________
```

Do not retrofit this into a narrative-only live group. It needs a time-series store or append-only artifacts because historical expectations cannot be reconstructed reliably after the event.

Acceptance criteria:

- the system can show what consensus was before earnings;
- current versus previous expectations are not conflated;
- guidance changes have source evidence;
- an earnings-update worksheet automatically selects the correct pre-event snapshot.
- no existing non-resource skill or agent is a runtime dependency.

[Back to overview](00-overview.md)
