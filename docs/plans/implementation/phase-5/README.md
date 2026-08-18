# Phase 5 Implementation Tracking

**Phase:** 5 — Side-by-side benchmark (new Investment Analyst track vs. legacy stock-analyst track)
**Status:** Pilot complete; full benchmark deferred until Portfolio Manager (Phase 11) exists
**Start date:** 2026-08-11

---

## Contents

- **`benchmark-results.md`** — Per-ticker comparison tables, the rollup
  table, and the analyst-bias tally. Filled in as each ticker's pair of runs
  completes.
- **`HANDOFF.md`** — Final verdict (full replacement / dual system /
  targeted adoption), any prompt/workflow follow-ups identified, and what
  Phase 6+ depends on. Written once the full benchmark set is scored.

---

## Phase 5 scope (revised)

The original plan was to run both tracks on six tickers and record a
replacement verdict. A pilot with 3 tickers revealed that **a fair
comparison is not yet possible**: the new path produces a thesis
(business attractiveness), not a portfolio decision; the legacy path
produces a portfolio action. These are different pipeline stages.

**Phase 5 revised scope:** pilot validation that the new Investment
Analyst path works end-to-end, surfaces the same risks, and is
comparable in cost to the legacy path. Full benchmark deferred to
Phase 5 Round 2, post-Portfolio Manager (Phase 11), when both paths
can run end-to-end to the same output (Buy/Sell/Hold).

## Gate condition (roadmap) — revised

Original: "Comparison reviewed; outcome recorded as full replacement / dual
system / targeted adoption."

**Revised:** Pilot validation that the new path works and is comparable in
cost. Full verdict deferred to Phase 5 Round 2 (post-Portfolio Manager),
when both paths can be measured end-to-end on the same output vocabulary
(portfolio decision, not thesis).

## Fixture ticker set

| Category | Ticker | Why |
|---|---|---|
| Well-covered growth | PLTR | Phase 3/4 fixture ticker; 5.70% position; high analyst coverage |
| Dividend/value | ENB | Income group, 5.11% yield, mature name |
| TSX + FX complexity | L (Loblaw) | Pure TSX listing, Quality group, no US ADR |
| Sparse coverage | XNDU | Small-cap (0.78% position), Growth group, thin coverage |
| Unowned/watchlist | OUST | `Knowledge-Base/stocks/OUST.md` — Portfolio Status: research, not in `holdings.md` |
| ETF, routing-only | SMH | 9.35% position, equity ETF |

## Staging

- **Pilot (3 tickers):** PLTR, ENB, OUST — ✓ Complete. Validated that both
  paths work end-to-end, surface the same risks, and have comparable costs.
  Stability check (PLTR re-run) complete: verdict fields matched (neutral,
  demanding, medium confidence).
- **Scale-up (3 tickers):** L, XNDU, SMH — **Deferred.** No point running
  these until Portfolio Manager exists and paths are at the same pipeline
  stage. Will resume in Phase 5 Round 2 (post-Phase 11) as part of the
  end-to-end benchmark.

## Key decision: legacy path is comparison-only

The legacy path's `stock-analyst` recommendations for this benchmark are
**not** committed to the Knowledge Base via `kb-intake`. They stay under
`exports/stock-recommendations/` as inert comparison artifacts so the
benchmark doesn't add real, dated Decision History rows to live thesis
pages for tickers that are actually held.

---

## Related files

- **Phase design spec:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md)
  — Phase 5 row. Benchmark rubric:
  [`../../combined-investment-analyst-plan/05-phase-4-side-by-side-benchmark.md`](../../combined-investment-analyst-plan/05-phase-4-side-by-side-benchmark.md).
  Replacement-decision definitions: `../../combined-investment-analyst-plan/00-overview.md` §14.2.
- **Prior phase:** [`../phase-4/HANDOFF.md`](../phase-4/HANDOFF.md) — see §5 for
  the analyst-bias concern this benchmark tests.
- **Path A (new):** `.claude/skills/investment-analyst-resources/`, `investment-analyst` agent
- **Path B (legacy):** `stock-data-prep` agent, `stock-analyst` agent, `evaluate-stock-decision` skill
- **This phase must NOT modify:** `src/`, `.claude/agents/`, `.claude/skills/**`,
  `Knowledge-Base/taxonomy/decision-rubric.yml` — Phase 5 is observation only,
  no code changes are anticipated.
