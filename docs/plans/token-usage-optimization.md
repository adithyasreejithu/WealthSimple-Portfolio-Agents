# Token-Usage Optimization for the Stock Decision-Support Pipeline

> On approval, this plan is saved verbatim to `docs/plans/token-usage-optimization.md`
> (repo convention per CLAUDE.md) as the first implementation step.

## Context

A portfolio-wide evaluation run (25 tickers × stock-data-prep + stock-analyst, then
kb-intake commits) consumes ~50–70M tokens and blows through session limits
(`logs/AgentSkillUsage.txt`, 2026-07-15 run). Investigation traced the burn to
payload sizes and one misplaced agent boundary — not to the fan-out design itself:

1. **`data.analyst.upgrades_downgrades`** — an uncapped multi-year table (~213K chars
   for AMZN) — is embedded **twice** per worksheet (cited by both `market_sentiment`
   and `earnings_catalysts`), making worksheets ~500KB. The Opus analyst reads the
   whole worksheet: ~$6 of cache-write per ticker before any judgment happens.
2. **stock-data-prep (Haiku)** has no scripted way to answer its required report
   (groups ok/failed, asset class, position), so it Reads the 245–671KB research
   JSON and the 70KB classification JSON — 0.5–1.8M tokens per "mechanical" agent.
3. **kb-intake commits** route a fully deterministic script
   (`ingest_recommendation.py`, "never calls LLM") through one full agent spawn per
   ticker: ~1M+ tokens each → potentially ~25M for the mechanical commit stage.
4. **Analyst narrative output is unbounded upward** (observed 6K–73K output tokens;
   skill mandates minimums only).
5. **ETF-track analysis runs on Opus** although the fund rubric track (expense
   ratio, concentration, distributions) needs far less judgment.

Target outcome: a full portfolio run drops to roughly **12–15M tokens**, with the
Opus share falling the most, and no change to the rubric-driven decision quality
model (deterministic scripts do mechanics; the LLM judges and cites).

## Root-cause → fix mapping

| # | Root cause | Fix | Stage |
|---|---|---|---|
| 1 | Uncapped raw tables embedded verbatim, duplicated across dimensions | Derived metrics + fetch window + embed caps | Phases 1–3 |
| 2 | Prep agent must open huge JSONs to report | Script stdout summary + agent guardrail | Phases 3, 5 |
| 3 | Deterministic commit behind per-ticker agent spawns | Single batch-commit pass | Phase 5 |
| 4 | No narrative maximums | Skill + validator caps | Phase 4 |
| 5 | Opus for fund-track scoring | Sonnet 5 model override for ETF fan-out | Phase 5 |

---

## Phase 1 — Fetch-time trim (`fetch_stock_research_data.py`)

File: `.claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py`

- In `_fetch_analyst()` (~line 204): trim `upgrades_downgrades` and `recommendations`
  to a **trailing 365-day window** before `_json_value()` conversion (both are
  date-indexed DataFrames; filter on index ≥ cutoff). Add a module constant
  `ANALYST_HISTORY_DAYS = 365`.
- Leave other groups unchanged — `history` already has `--history-days`; options/
  financials feed derived metrics and thesis writing and only cost tokens if read.
- Update `references/yfinance-research-contract.md` to document the window.

## Phase 2 — Rubric: new derived metrics as evidence (via `author-decision-rubric` workflow)

File: `Knowledge-Base/taxonomy/decision-rubric.yml` (edited only through the
`author-decision-rubric` skill: validate + **version bump required** since
evidence sources change).

- Add derived evidence fields and re-point the two heavy dimensions at them:
  - `market_sentiment.evidence_fields`: replace `yfinance:data.analyst.upgrades_downgrades`
    with `derived:upgrades_90d`, `derived:downgrades_90d`, `derived:net_revisions_365d`
    (keep `recommendations_summary`, returns, headlines).
  - `earnings_catalysts.evidence_fields`: same replacement (keep calendar +
    earnings_dates).
  - `insider_activity`: keep `derived:net_insider_shares` (exists) + raw tables,
    which Phase 3's embed cap will bound.
- Run the rubric validation script per the skill; bump `version` (minor).

## Phase 3 — Worksheet builder (`scoring_worksheet.py`) — the core change

File: `.claude/skills/evaluate-stock-decision/scripts/scoring_worksheet.py`

1. **New derived metrics** in the existing `DERIVED_METRICS` registry (line 435),
   following the `_net_insider_shares` / `_price_return` patterns:
   - `_upgrades_90d`, `_downgrades_90d` — count rows in
     `data.analyst.upgrades_downgrades` with an upgrade/downgrade `Action` within
     the window (reuse `_parse_date`, line 179).
   - `_net_revisions_365d` — upgrades minus downgrades trailing 365d.
   `validate_recommendation.py` resolves `derived:` citations through the same
   registry (its `derived_cache`, line 79), so new metrics validate with no
   validator change — verify the import path during implementation.
2. **Evidence embed guard** in `_resolve_evidence` (line 470): cap any embedded
   list/table value at `MAX_EVIDENCE_ROWS = 15` most-recent rows and
   `MAX_EVIDENCE_CHARS = 4000` serialized chars; on truncation replace the value
   with `{"rows": [...], "truncated": true, "total_rows": N}`. This is the
   permanent backstop against any future unbounded yfinance table.
   (A shared evidence store / dedupe was considered and rejected: with derived
   metrics + caps, residual duplication is ≤ ~8K chars — not worth the contract churn.)
3. **Classification freshness check**: `build_worksheet` already receives the
   classification source; add the ~7-day `generated_at` staleness check there
   (currently done by the prep agent Reading the 70KB JSON) and surface it in the
   stdout summary + a `position.classification_stale` flag.
4. **Stdout data-prep summary**: after writing the worksheet, print a compact
   block (≤ ~20 lines): ticker, asset class, dividend payer, track
   (equity/etf), groups ok / empty / failed, position (held, weight, role),
   classification freshness, worksheet path. This is everything the prep agent's
   Output Format needs — it never opens a JSON again.

## Phase 4 — Analyst output caps

Files: `.claude/skills/evaluate-stock-decision/SKILL.md`,
`references/recommendation-contract.md`,
`.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py`

- SKILL.md step 3: add maximums — ≤ 6 bullets per dimension `section_updates`
  block; `updated_thesis` 2–3 paragraphs; `analyst_view` ≤ ~300 words;
  `executive_summary` ≤ ~120 words.
- Validator: enforce loose structural ceilings (e.g. bullet count per section,
  char ceilings ~2× the prose guidance) so a runaway narrative fails fast instead
  of silently costing output tokens. Keep existing minimums.

## Phase 5 — Agent & workflow docs

1. **`.claude/agents/stock-data-prep.md`** — rework Workflow steps 2/5 and add a
   guardrail: *never Read research/worksheet/classification JSONs; report from
   `kb_search` output, `stocks/TICKER.md`, and the scripts' stdout summaries.*
   (kb-search + reading the ~4KB TICKER.md stay as-is.)
2. **Batch commit** — `docs/architecture/decision_support_flow.md` ("Multi-ticker /
   batch runs"), `evaluate-stock-decision/SKILL.md` (same section),
   `.claude/agents/kb-intake.md`, and the CLAUDE.md decision-support paragraph:
   replace "walk artifacts through kb-intake one at a time" (one spawn per ticker)
   with **one kb-intake invocation** that receives the artifact list and loops
   `ingest_recommendation.py` sequentially via Bash — still sequential writes (the
   index/log helpers race otherwise), but one agent context instead of N.
   kb-intake must not Read the artifacts; the script output is its report input.
3. **ETF model tiering** — in `decision_support_flow.md` + CLAUDE.md: portfolio
   fan-out invokes stock-analyst for **ETF-batch tickers with a `model: sonnet`
   override** (Agent tool model parameter); equity batch stays Opus. The agent
   definition's default model stays `opus`. Note in `docs/agents/stock-analyst/architecture.md`.
   Safety: the validator recomputes all rubric arithmetic regardless of model.
4. Keep `docs/agents/stock-data-prep/architecture.md` and
   `docs/architecture/decision_support_flow.md` aligned with the above (CLAUDE.md
   requires docs to move with behavior).

## Phase 6 — Tests

- `tests/test_scoring_worksheet.py`: new derived metrics (fixture rows spanning
  the 90/365-day windows), embed-cap truncation shape, stale-classification flag,
  stdout summary smoke test.
- `tests/test_validate_recommendation.py`: citations of the new `derived:` fields
  resolve; narrative ceiling failures reported.
- `tests/test_decision_rubric.py`: updated evidence_fields + version bump pass
  rubric validation.
- Fetch trim: unit-test the window helper with a synthetic DataFrame (no live
  yfinance; per repo testing guidelines, mock network).

## Verification (end-to-end)

1. `uv run python -m unittest discover -s tests` — full suite green.
2. Regenerate research + worksheet for one equity (AAPL) and one ETF (ZEB):
   - research JSON `analyst` group ≪ previous (~200K → low-K chars);
   - worksheet **< 60KB** with no evidence value > 4K chars;
   - stdout summary shows groups/asset-class/position correctly for both tracks.
3. Run one full single-ticker evaluation through the real agents
   (prep → analyst → validate → single kb-intake commit) and compare per-agent
   token lines in `logs/AgentSkillUsage.txt` against the 2026-07-15 baselines:
   prep ≤ ~250K, analyst ≤ ~500K total tokens.
4. Confirm `ingest_recommendation.py` output on the thesis page is unchanged in
   structure (Decision History row appended, sections transcribed).

## Expected impact (25-ticker portfolio run)

| Stage | Before | After |
|---|---|---|
| stock-data-prep ×25 | ~20M | ~4M |
| stock-analyst ×25 (Opus-heavy) | ~25M+ | ~8M (less with ETF tiering) |
| kb-intake commits | ~25M (per-ticker spawns) | < 0.5M (one batch pass) |
| **Total** | **~50–70M** | **~12–15M** |

## Out of scope

- Orchestrator-session behavior (spawn staggering for cache warmth, main-session
  model choice) — noted as usage guidance, no repo change.
- The threading/markdown-export refactor previously rejected (per project memory).
- Any change to verdict bands, weights, or the action/confidence/horizon enums.
