# Phase 4 Implementation Tracking

**Phase:** 4 — Investment Analyst agent (judgment layer over the Phase 3 worksheet)
**Status:** Deliverables complete, ready for approval
**Start date:** 2026-08-11

---

## Contents

- **`design-decisions.md`** — Implementation-level decisions: CLI-stage vs.
  skill boundary, the `check-thesis`/`save-thesis` split, the
  `save-thesis`-is-authoritative rule, evidence type/audit event names, the
  `state.py` addition, and a pre-existing Phase 3 Windows newline/hash bug
  found and fixed along the way. **Read this before touching
  `thesis_validation.py`'s new functions, `cli.py`'s three new subcommands,
  or `state.py` again.**
- **`deliverables-checklist.md`** — Inventory of files created/modified,
  tests, and gate verification.
- **`HANDOFF.md`** — What Phase 4 built, the scope correction applied at the
  start of this phase, and what later phases depend on.

---

## Phase 4 scope in one line

Build the one required LLM judgment stage: an agent that reads a run's
Phase-3 `investment-worksheet.v1`, interprets it against the locked
`investment-thesis.v1` contract, and produces a validated, evidence-cited
security thesis — fundamental attractiveness, not a portfolio action.

## Gate condition (roadmap)

Produces a valid thesis for the PLTR fixture; passes the Phase 1 validator;
never reads the raw resource bundle directly; never writes the KB; repeated
runs on identical input are materially stable. Evidence citations in key
claims resolve to registered evidence; a worksheet with a required-domain
TRACE failure produces no artifact (confidence-cap enforcement forces
rejection); valuation/scenario blocks are correctly treated as placeholders,
not real DCF/peer research.

---

## Related files

- **Phase design spec:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md)
  — Phase 4 row. Fuller spec:
  [`../../combined-investment-analyst-plan/00-overview.md`](../../combined-investment-analyst-plan/00-overview.md)
  §3.2 (agent responsibilities), §5.8 (Investment Analyst judgment stage).
- **Prior phase:** [`../phase-3/HANDOFF.md`](../phase-3/HANDOFF.md)
- **Files Phase 4 created:**
  - `.claude/agents/investment-analyst.md`
  - `docs/agents/investment-analyst/architecture.md`, `plan.md`
  - `tests/test_investment_analyst_cli.py`
- **Files Phase 4 modified:**
  - `src/workspace/thesis_validation.py` — `check_worksheet_ref`, `scope_from_worksheet_ref`, `check_thesis_draft`, `save_thesis`
  - `src/workspace/cli.py` — `run build-worksheet`/`check-thesis`/`save-thesis`
  - `src/workspace/validation.py` — schema-aware `investment-thesis.v1` branch in the `agent_outputs` loop
  - `src/workspace/state.py`, `src/workspace/run.py` — `insufficient_evidence` status
  - `src/workspace/investment_worksheet.py` — Windows newline/hash bug fix (Decision 6)
  - `docs/reference/cli.md`
  - `tests/test_run_workspace.py`, `tests/test_investment_thesis_validation.py`
- **Files Phase 4 must NOT modify:** `src/portfolio_metrics.py`, `src/analytics.py`,
  `src/security_technicals.py`, `.claude/skills/**`, `Knowledge-Base/taxonomy/decision-rubric.yml`,
  `decision-framework.yml` — the agent reads a worksheet and writes a thesis, nothing else.
