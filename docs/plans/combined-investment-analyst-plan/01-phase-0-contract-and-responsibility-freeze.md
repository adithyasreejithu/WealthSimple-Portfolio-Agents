# Phase 0 — Contract and responsibility freeze

**Priority:** critical  
**Start here:** yes  
**Purpose:** prevent later components from building against contradictory assumptions.

Tasks:

1. Approve the analyst/Portfolio Manager boundary.
2. Approve rating vocabulary and remove portfolio action fields from the analyst contract.
3. Define supported run modes.
4. Define section names and stable identifiers.
5. Define critical versus optional evidence policy per mode.
6. Define equity v1 scope and explicitly defer ETF execution.
7. Decide the initial valuation methods supported.
8. Decide whether the first analyst is allowed one evidence-gap loop.
9. Freeze the rebuild boundary: only `investment-analyst-resources` and `market-analyst-resources` are retained; all agents and all other runtime components are rebuilt from scratch.
10. Freeze the skill rule: every skill in the rebuilt workflow must be a purpose-built `*-resources` skill; deterministic worksheet, validation, rendering, and promotion logic is not implemented as a skill.
11. Adopt the workspace TRACE three-way classification and leave any analysis-blocking thresholds undecided until the owner supplies them.

Deliverables:

- approved `investment-thesis.v1` schema draft;
- approved `analysis-scope.v1` schema draft;
- analysis policy YAML draft;
- example valid initial thesis;
- example valid incremental patch;
- example invalid artifact containing a portfolio action.
- approved runtime dependency allowlist;
- TRACE policy fields with unresolved values left blank.

TRACE policy blanks:

```text
TRACE blocking threshold: ______________________________
TRACE warning threshold:  ______________________________
Who may override a TRACE-based block: __________________
```

Acceptance criteria:

- no field ambiguously assigns position sizing to the analyst;
- every report section has a stable machine identifier;
- every run mode maps deterministically to evaluated/preserved sections;
- schema examples validate before agent work begins;
- no existing agent or non-resource skill appears in the target runtime dependency graph;
- `not_applicable` is excluded from TRACE completeness exactly as implemented in `src/skill_trace.py`.

[Back to overview](00-overview.md)
