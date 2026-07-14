# Consolidate Decision Tracking into Stock Thesis Files

**Date:** 2026-07-11  
**Status:** Approved for future implementation  
**Priority:** Medium — improves knowledge base architecture coherence

---

## Problem Statement

The current Knowledge Base design has **redundant separate templates** for decisions that should be consolidated:

1. **`sell-decision-template.md`** — separate file for sell decisions
2. **`portfolio-review-template.md`** — separate file for portfolio reviews

However, the `stocks/TICKER.md` thesis file **already contains all necessary fields** to capture these decisions:
- `Decision` field (Buy | Sell | Hold | Trim | Add | Watchlist | Avoid)
- `status` field (research | watchlist | active | closed | rejected)
- **Decision History** table (append-only: Date, Action, Verdict, Note)
- **Updated Thesis** section (captures reasoning for conviction changes)

**Current flow for a sell decision:**
1. Create a separate sell-decision file
2. Update the thesis file Status/Decision fields
3. Append to Decision History

**Proposed flow:**
1. Update thesis file Status/Decision fields
2. Append to Decision History
3. Update Updated Thesis with exit reasoning
4. (No separate file needed)

---

## Root Cause

The templates were designed as if decisions would be documented separately, but the actual workflow consolidated them into the thesis file instead. The separate templates create cognitive and organizational friction without adding value.

---

## Solution

**Remove the redundant templates** and establish the thesis file as the **single source of truth** for all decisions and analysis:

- **Delete:** `Knowledge-Base/templates/sell-decision-template.md`
- **Delete:** `Knowledge-Base/templates/portfolio-review-template.md`
- **Clarify:** Update `docs/reference/knowledge_base_workflow.md` to remove references to sell-decision and portfolio-review as separate files
- **Document:** Add to `docs/architecture/knowledge_base.md` that:
  - All investment decisions live in `stocks/TICKER.md`
  - Sell decisions = Decision field change + status change + Decision History row
  - Portfolio reviews = selective thesis updates + Decision History entries for key holdings

---

## Implementation Steps

1. **Audit current usage:**
   - Search codebase for any references to `sell-decision-template.md` or `portfolio-review-template.md`
   - Confirm no code scaffolds or uses these templates

2. **Update documentation:**
   - Remove sell-decision and portfolio-review from `Knowledge-Base/templates/index.md`
   - Update `docs/reference/knowledge_base_workflow.md` Table 1 to remove these entries
   - Update `docs/architecture/knowledge_base.md` with decision consolidation principles

3. **Update skill instructions:**
   - Verify `kb-intake` agent doesn't reference these templates
   - If any CLI flags or scripts reference them, remove or deprecate

4. **Delete files:**
   - Remove `Knowledge-Base/templates/sell-decision-template.md`
   - Remove `Knowledge-Base/templates/portfolio-review-template.md`

5. **Add examples to thesis template:**
   - Enhance `stock-thesis-template.md` with examples showing:
     - How to document a sell decision (Decision field + Decision History row)
     - How to document a thesis update (Updated Thesis section + Decision History row)

---

## Files Affected

**To delete:**
- `Knowledge-Base/templates/sell-decision-template.md`
- `Knowledge-Base/templates/portfolio-review-template.md`

**To update:**
- `Knowledge-Base/templates/index.md` — remove two entries
- `Knowledge-Base/templates/stock-thesis-template.md` — add usage examples
- `docs/reference/knowledge_base_workflow.md` — remove sell-decision/portfolio-review rows from Table 1
- `docs/architecture/knowledge_base.md` — clarify decision consolidation
- `.claude/agents/kb-intake.md` — confirm no references to deleted templates

---

## Rationale

- **Reduces fragmentation:** One place for decisions (the thesis) instead of three (Decision field, Decision History, separate files)
- **Improves auditability:** Decision History is already append-only and timestamped; no need for separate logs
- **Simplifies kb-intake workflow:** No scaffolding or file management overhead for non-existent separate files
- **Aligns with existing practice:** The workflow already treats the thesis as the source of truth; templates just lag behind

---

## Acceptance Criteria

- [ ] All references to sell-decision and portfolio-review templates removed from codebase and docs
- [ ] `stock-thesis-template.md` includes examples of sell and portfolio-review workflows
- [ ] `docs/reference/knowledge_base_workflow.md` reflects thesis-only decision model
- [ ] No CLI flags or agent instructions reference deleted templates

---

## Related Decisions

- Original KB design: `docs/architecture/knowledge_base.md`
- Thesis template: `Knowledge-Base/templates/stock-thesis-template.md`
- KB workflow reference: `docs/reference/knowledge_base_workflow.md`
