# Phase 2 Implementation Tracking

**Phase:** 2 — Cheap data-gap closure (technicals + ratios + return-window defect)
**Status:** Deliverables complete, ready for approval
**Start date:** 2026-08-10
**Files in this folder:** Track all artifacts, changes, and decisions made during Phase 2 implementation.

---

## Contents

- **`design-decisions.md`** — Decisions made *before* implementation began. Currently
  holds the reuse-boundary decision (Option B) resolving whether the technicals module
  duplicates the dashboard's analytics layer. **Read this before writing any Phase 2
  code** — it constrains which files may be touched.
- **`deliverables-checklist.md`** — To be created when implementation completes:
  inventory of files created, tests written, and acceptance criteria met.
- **`HANDOFF.md`** — To be created at phase close: architectural notes, known issues,
  and guidance for Phase 3.

---

## Phase 2 scope in one line

Close the two Doc A gaps that need no new data source — per-security technicals and
normalized valuation/quality ratios — and fix the windowed-return mislabeling defect,
all before any LLM judgment stage exists.

## Gate condition

Unit tests against a known-OHLCV fixture reproduce hand-computed beta/drawdown/MA/ratio
values; windowed returns are correctly labelled or marked insufficient-history rather
than silently duplicated; `git diff --stat` shows **no change** to
`src/portfolio_metrics.py` or `src/analytics.py`.

---

## Related files

- **Phase design spec:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md)
  — Phase 2 row, plus the "Phase 2 design decision — reuse boundary" section.
  Note: the roadmap **renumbers** the combined plan. This phase is an insertion with
  no counterpart in `combined-investment-analyst-plan/`; that plan's
  `03-phase-2-worksheet-builder.md` is the roadmap's **Phase 3**.
- **Gap analysis this phase closes:** [`../../stock-analysis-agent-design-review.md`](../../stock-analysis-agent-design-review.md) §6
- **Prior phase:** [`../phase-1/HANDOFF.md`](../phase-1/HANDOFF.md)
- **Files Phase 2 created:**
  - `src/security_technicals.py`
  - `tests/test_security_technicals.py`
- **Files Phase 2 modified:**
  - `.claude/skills/investment-analyst-resources/scripts/derived_metrics.py` — return-window
    defect fix (Decision 3) **and** six new ratio functions extending `LIVE_METRICS`/
    `DB_METRICS` in place (Decision 2 resolved to "extend", not a new module)
  - `.claude/skills/investment-analyst-resources/references/resource-contract.md`
  - `tests/test_investment_analyst_resources.py`
- **Files Phase 2 must NOT modify:**
  - `src/portfolio_metrics.py`, `src/analytics.py` (see `design-decisions.md`)
