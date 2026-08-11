# Phase 3 Handoff — Key Changes and Context for Future Sessions

**Date:** 2026-08-10
**Status:** Phase 3 complete

---

## What Phase 3 Built

The worksheet builder: `src/workspace/investment_worksheet.py` (orchestrator
+ deterministic scope mapper) plus three supporting modules
(`financial_metrics.py`, `valuation.py`, `scenarios.py`). Given a run's
already-registered `investment-analyst-resources` bundle and (optionally) a
`security-technicals` artifact, it produces `investment-worksheet.v1` — a
compact, evidence-linked JSON plus a bullet-point Markdown context — without
fetching anything itself.

**Read `design-decisions.md` before touching any of this again.** It's the
binding record of six decisions, three of them non-obvious:

1. Technicals are read from the run's registered artifact, never recomputed
   — the worksheet builder has zero DB/network code of its own.
2. The policy's `evidence_domain_registry` names don't map 1:1 onto the
   bundle's actual TRACE domain names (`_DOMAIN_TRACE_MAP` bridges this).
3. Valuation/scenarios are explicit placeholders (a sensitivity band around
   today's market-implied multiple), not real DCF/peer-comparison output —
   stated in the artifact itself, not just in code comments.

## Critical Things to Know

### 1. A pre-existing bug was found and fixed in `investment-analysis-policy.yml`

Two of the seven `mode_section_map` entries (`material_event`,
`price_move_review`) didn't partition all 16 registered sections —
`AnalysisScope`'s own validator would have raised on first real use of
either mode. Fixed by adding the missing sections to `preserve_sections`
(design-decisions.md Decision 5). `BuildAnalysisScopeTest.test_every_policy_mode_produces_a_valid_scope`
loops all seven modes so this can't silently regress.

### 2. `config/policies/investment-analysis-policy.yml` was never tracked by git until this phase

Unrelated to Phase 3's own work, but blocking it: `.gitignore` had no
`!config/` allowlist entry, so the policy file has been untracked since
Phase 0's move (commit `24e6272`) — `git diff`/`git status` were blind to it
this entire time. Fixed by adding `!config/`/`!config/**` to `.gitignore`.
**If a fresh clone or CI checkout was ever relied on, it would have been
missing this file entirely** — worth confirming nothing downstream assumed
otherwise. See `deliverables-checklist.md`'s note for the full story.

### 3. Valuation/scenario numbers are not real investment analysis

`valuation.py`'s four methods all derive from **today's own market-implied
multiple** ± a fixed ±15% band — `mid` is mathematically guaranteed to equal
the current market value. This exists so the worksheet always has a
schema-valid structure for Phase 4's analyst to interpret or override, not
because it's a real valuation opinion. Every method's `sensitivity_note`
and every scenario's `revenue_assumption` say so explicitly. Don't let a
Phase 4 analyst (or a human) read a `fair_value_per_share` here as
independent research — it will need real assumptions (growth, discount
rate, peer multiples) before it means anything.

### 4. `prior_thesis` and `market_context` are unwired passthrough params

`build_worksheet`/`build_worksheet_for_run` accept them but nothing in this
repo produces them yet — a real prior-thesis loader needs Phase 5/7's
versioned KB-thesis shape (today's `Knowledge-Base/stocks/*.md` pages are a
different artifact entirely), and Market Researcher digests need Phase 10.
Both default to an explicit `unknowns` entry, never a silent gap.

---

## Files to Remember

| File | Purpose | Notes |
|---|---|---|
| `src/workspace/investment_worksheet.py` | Orchestrator + scope mapper | `_DOMAIN_TRACE_MAP` is the domain-vocabulary bridge — extend it, don't bypass it, if a new evidence domain is added to the policy registry |
| `src/workspace/valuation.py` | Placeholder valuation methods | `DEFAULT_SENSITIVITY_BAND` is the one tunable; real assumption inputs supersede this module's whole approach, not just the constant |
| `docs/plans/implementation/phase-3/design-decisions.md` | Binding decisions | Read before touching `investment_worksheet.py`, `valuation.py`, or the policy's `mode_section_map` |
| `config/policies/investment-analysis-policy.yml` | Now actually tracked by git | Confirm `git status` shows it clean after any future edit — the tracking gap that hid it before is fixed, but double-check |
| `tests/test_investment_worksheet.py`, `tests/fixtures/investment_analyst/` | Test coverage | 21 tests; fixtures are hand-authored, not derived from a real skill run |

---

## What Phase 4 Depends On

Phase 4 (the Investment Analyst agent, per the roadmap) can call:

- `investment_worksheet.build_analysis_scope(mode, asset_track, subject,
  run_id, trigger, decision_horizon)` to get a validated `AnalysisScope` for
  a run.
- `investment_worksheet.build_worksheet_for_run(run_dir, run_id=..., ticker=...,
  scope=...)` to get `(worksheet, worksheet_path, worksheet_hash)` — the
  hash is exactly what `investment-thesis.v1`'s `worksheet_ref.hash` needs.

Neither is wired into an agent or a `src/app.py` CLI command yet — per
`00-overview.md` §3.2 this stays a deterministic module until Phase 4's
agent (or whatever orchestration precedes it) becomes its first real caller.

---

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_investment_worksheet -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py src/security_technicals.py dashboard/ .claude/skills/   # must be empty
```

---

## Ready for Phase 4

The Phase 3 gate is met. Phase 4 can begin when approved — per the roadmap's
execution protocol, this is an explicit per-phase decision, not a default.
