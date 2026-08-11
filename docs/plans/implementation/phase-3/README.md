# Phase 3 Implementation Tracking

**Phase:** 3 — Investment worksheet builder over `investment-analyst-resources`
**Status:** Deliverables complete, ready for approval
**Start date:** 2026-08-10

---

## Contents

- **`design-decisions.md`** — Decisions made during implementation: where
  technicals/benchmark data comes from, the placeholder valuation
  methodology, the context-size ceiling, and a Phase 0 policy defect found
  and fixed along the way. **Read this before touching `investment_worksheet.py`,
  `valuation.py`, or `investment-analysis-policy.yml`'s `mode_section_map`
  again.**
- **`deliverables-checklist.md`** — Inventory of files created/modified,
  tests, and gate verification.
- **`HANDOFF.md`** — Architectural notes and what Phase 4 depends on.

---

## Phase 3 scope in one line

Turn one run's already-registered `investment-analyst-resources` bundle
(plus, if present, a `security-technicals` artifact) into the compact,
evidence-linked worksheet the Phase 4 Investment Analyst agent will read —
without fetching anything itself.

## Gate condition

PLTR fixture generates the same worksheet deterministically; a context-size
ceiling test passes; TRACE's `missing`/`not_applicable` distinction is
preserved, not recomputed.

---

## Related files

- **Phase design spec:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md)
  — Phase 3 row. Fuller spec:
  [`../../combined-investment-analyst-plan/03-phase-2-worksheet-builder.md`](../../combined-investment-analyst-plan/03-phase-2-worksheet-builder.md)
  and `00-overview.md` §5.7 (note the roadmap-vs-combined-plan numbering
  mismatch already flagged in `../phase-1/HANDOFF.md`).
- **Prior phase:** [`../phase-2/HANDOFF.md`](../phase-2/HANDOFF.md)
- **Files Phase 3 created:**
  - `src/workspace/investment_worksheet.py`
  - `src/workspace/financial_metrics.py`
  - `src/workspace/valuation.py`
  - `src/workspace/scenarios.py`
  - `tests/test_investment_worksheet.py`
  - `tests/fixtures/investment_analyst/pltr_bundle.json`, `pltr_technicals.json`
- **Files Phase 3 modified:**
  - `src/workspace/analysis_models.py` — added `get_mode_section_map()` accessor
  - `config/policies/investment-analysis-policy.yml` — fixed a section-partition
    defect in `material_event`/`price_move_review` (see `design-decisions.md`)
- **Files Phase 3 must NOT modify:** `src/portfolio_metrics.py`, `src/analytics.py`,
  `src/security_technicals.py`, `.claude/skills/investment-analyst-resources/**`,
  `.claude/skills/security-technicals/**` — this phase only *reads* their outputs.
