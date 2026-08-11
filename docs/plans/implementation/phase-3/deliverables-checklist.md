# Phase 3 Deliverables Checklist

**Status:** completed and ready for review
**Date completed:** 2026-08-10
**Companion:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md) (Phase 3 row), [`design-decisions.md`](design-decisions.md) (binding decisions this phase followed)

---

## Summary

Phase 3 built the deterministic worksheet builder that turns one run's
already-registered `investment-analyst-resources` bundle (plus, if present,
a `security-technicals` artifact) into the compact, evidence-linked
worksheet the Phase 4 Investment Analyst agent will read. Four new modules
per the roadmap's file list, a scope mapper (`build_analysis_scope`) that
turns a run `mode` into a validated `analysis-scope.v1` from
`investment-analysis-policy.yml`'s `mode_section_map`, and a PLTR fixture
proving the worksheet builds deterministically with TRACE's
`missing`/`not_applicable` distinction preserved verbatim from the bundle's
own trace record.

Two incidental fixes surfaced during implementation, not part of the
original file list: a two-mode section-partition defect in
`investment-analysis-policy.yml` (Decision 5), and a `.gitignore` gap that
had left `config/policies/investment-analysis-policy.yml` entirely untracked
by git since Phase 0's file move (commit `24e6272`) — see
`design-decisions.md` Decision 5 and the note below.

---

## Files Created

| File | Purpose | Status |
|---|---|---|
| **`src/workspace/investment_worksheet.py`** | Orchestrator + scope mapper: `build_analysis_scope`, pure `build_worksheet`, and the I/O wrapper `build_worksheet_for_run`. | ✅ Complete |
| **`src/workspace/financial_metrics.py`** | Quarterly financial-trajectory trend table with QoQ deltas; surfaces Phase-2 ratios by reference. | ✅ Complete |
| **`src/workspace/valuation.py`** | Four valuation-method calculators (`earnings_multiple`, `fcf_yield`, `ev_revenue`, `ev_ebitda`), each a `{low, mid, high}` range via a stated placeholder sensitivity band. | ✅ Complete |
| **`src/workspace/scenarios.py`** | Bull/base/bear scenario templates derived from `valuation.py`'s combined range, 25/50/25 default split. | ✅ Complete |
| **`tests/test_investment_worksheet.py`** | 21 tests: scope-mapper validity across all 7 policy modes, worksheet determinism, TRACE pass-through, ETF not-applicable sections, critical-gap blocking under both policies, missing-technicals tolerance, context-size ceiling, and the full `build_worksheet_for_run` I/O path against a temp run directory. | ✅ Complete, all passing |
| **`tests/fixtures/investment_analyst/pltr_bundle.json`** | Synthetic PLTR `investment-analyst-resources` bundle, shaped per `resource-contract.md`, with a hand-authored trace carrying genuine `missing` and `not_applicable` entries. | ✅ Complete |
| **`tests/fixtures/investment_analyst/pltr_technicals.json`** | Matching `security-technicals` artifact, shaped per `technicals-contract.md`. | ✅ Complete |
| **`docs/plans/implementation/phase-3/design-decisions.md`** | Binding design record: technicals sourcing, domain-vocabulary translation, placeholder valuation methodology, context ceiling, the policy-file section-partition fix. | ✅ Complete |
| **`docs/plans/implementation/phase-3/README.md`** | Phase tracking, mirroring the phase-0/1/2 folder pattern. | ✅ Complete |

## Files Modified

| File | Change | Status |
|---|---|---|
| **`src/workspace/analysis_models.py`** | Added `get_mode_section_map()` accessor (additive; no existing behavior changed). | ✅ Complete |
| **`config/policies/investment-analysis-policy.yml`** | Fixed `material_event` (missing `valuation`) and `price_move_review` (missing `catalysts`, `risks_and_disconfirming_evidence`) so every mode's `evaluate_sections ∪ preserve_sections ∪ not_applicable_sections` covers exactly the 16 registered sections (Decision 5). | ✅ Complete |
| **`.gitignore`** | Added `!config/` / `!config/**` — the policy file's directory had no allowlist entry, so it was never actually committed since Phase 0's move to `config/policies/`. See note below. | ✅ Complete |

---

## Note: a pre-existing tracking gap, not a Phase 3 regression

`config/policies/investment-analysis-policy.yml` has been read successfully
by `analysis_models.py` since Phase 1 (it exists on disk), but was **never
tracked by git** — Phase 0's commit `24e6272` deleted the old
`Knowledge-Base/taxonomy/` copy and added the new `config/policies/` one to
the working tree, but `.gitignore`'s allowlist (`!src/`, `!docs/`,
`!Knowledge-Base/`, `!tests/`, `!dashboard/**`, `!.claude/**` — no `!config/`)
silently excluded it from every commit since. `git diff`/`git status` were
blind to any edit to this file for the life of the repo until this phase's
`.gitignore` fix. Discovered only because Phase 3 needed to commit a real
fix to it (Decision 5) and `git diff --stat` came back empty for a file that
had visibly changed on disk. Flagged here rather than fixed silently because
it also means the file's entire edit history before this phase (Phase 0's
original authoring, Phase 1's own possible reads) exists only in this
working tree, not in git history — worth the user's awareness, not just a
one-line diff.

---

## Gate Verification

Roadmap gate: *"PLTR fixture generates the same worksheet deterministically;
missing options do not penalize a security when options are
non-applicable/optional; missing critical financial evidence blocks a full
initial rating under the selected policy; ... worksheet evidence health
agrees with the source TRACE record and does not turn `not_applicable`
fields into gaps."*

```bash
uv run python -m unittest tests.test_investment_worksheet -v   # 21 tests, OK
uv run python -m unittest discover -s tests                    # 1031 tests, OK
```

- **Deterministic:** `BuildWorksheetDeterminismTest.test_repeated_builds_are_byte_identical`
  — two `build_worksheet` calls against the same fixtures + frozen `as_of`
  produce byte-identical JSON.
- **TRACE preserved, not recomputed:** `TracePreservationTest` — the
  fixture's `options`/`funds` domains (graded `not_applicable` in the
  bundle's own trace, an empty options chain / structural non-applicability
  for a stock) never appear in the worksheet's `unknowns`; `insider`
  (graded `missing`) always does.
- **Critical-gap blocking matches policy:** `CriticalGapBlockingTest` — the
  same failed required domain blocks under `initial_research`'s
  `critical_gap_policy: stop` and does not block under `scheduled_review`'s
  `proceed_with_gap_disclosure`.
- **Context-size ceiling:** `ContextSizeCeilingTest` — PLTR's rendered
  context is ~1,600 characters against a 12,000-character ceiling, with a
  structural per-section bullet-count check as a second line of defense.
- **No dashboard/technicals-math/skill-layer change:** `git diff --stat`
  touches only `src/workspace/*`, the new fixtures/tests, `config/policies/`,
  and planning docs — no `src/portfolio_metrics.py`, `src/analytics.py`,
  `src/security_technicals.py`, or `.claude/skills/**`.

---

## What Phase 4 Depends On

Phase 4 (the Investment Analyst agent) can call
`investment_worksheet.build_worksheet_for_run(run_dir, run_id=..., ticker=...,
scope=...)` once a run has `investment-analyst-resources` (and, ideally,
`security-technicals`) evidence registered, and read the returned
`worksheet_path`/`worksheet_hash` directly into `investment-thesis.v1`'s
`worksheet_ref`. `build_analysis_scope(mode, asset_track, subject, run_id,
trigger, decision_horizon)` is the scope-mapper entry point Phase 4's
controller (or whichever orchestration wraps it) should call before the
worksheet builder, not something Phase 4 needs to reimplement.

Not wired into Phase 4 by this phase: a real `prior_thesis` loader
(Phase 5/7) and a `market_context` digest (Phase 10) — both are accepted as
optional parameters that degrade to an explicit `unknowns` entry when
absent (Decision 6).

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_investment_worksheet -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py src/security_technicals.py dashboard/ .claude/skills/   # should be empty
```
