# Phase 3 — Minimal Investment Analyst agent

**Priority:** critical  
**Dependency:** Phases 0–2

Agent configuration:

- strongest available reasoning model;
- least-privilege tools: Bash/Read/Write only if required by the local agent runtime;
- created from scratch rather than extending an existing agent;
- no direct non-resource skills;
- resource acquisition occurs before judgment through approved `*-resources` skills;
- worksheet construction and thesis validation run as deterministic Python stages outside the agent;
- no direct KB-write capability;
- output limited to the attached run's `agent_outputs/` and temporary draft paths.

Workflow:

1. Verify manifest and worksheet status.
2. Stop or request the one evidence-gap loop if a critical gap exists.
3. Draft every in-scope section.
4. Copy deterministic calculations exactly rather than recalculate.
5. Classify claims as fact/inference/assumption/opinion.
6. Cite registered evidence.
7. Compare against prior claims.
8. Produce scenarios and monitoring conditions.
9. Run precompute validation during drafting.
10. Run final validation until valid or report a genuine unresolved failure.

Acceptance criteria:

- produces valid thesis for the selected PLTR fixture;
- never reads the full resource bundle directly;
- never writes the KB;
- does not output a target weight or trade instruction;
- missing evidence remains an unknown;
- repeated runs on identical context are materially stable.
- agent configuration contains no legacy agent or non-resource skill dependency.

[Back to overview](00-overview.md)
