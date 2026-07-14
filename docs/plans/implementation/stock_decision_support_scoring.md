# Stock Decision-Support: Scored Rubric + Analyst Agents

Approved implementation plan (captured at execution time; reference material, not
maintained after completion). Companion runtime doc:
[`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).

## Context

The KB pipeline was scaffolded end-to-end — `fetch-stock-research-data` pulls 11
yfinance groups per verified ticker, the thesis template has
Status/Decision/Bull/Bear/Decision History, and `kb-intake` is the single wiki
writer — but nothing bridged raw data → recommendation. `decision-framework.yml`
defined only enums; there was no scoring framework, criteria, or analysis agent.

The owner's concern: the LLM is not trained on trading data, so it cannot be
trusted to predict what to buy. The design answer: the LLM never predicts
markets — it applies a hand-curated, human-authored rubric (gates + weighted
criteria) to fetched evidence, citing every score. Deterministic Python enforces
the math and the citations.

## Fixed decisions

1. Framework = scored rubric + hard gates (YAML, hand-curated, agents read-only).
2. Write topology = analyst drafts, kb-intake commits (analyst read-only on the
   wiki; emits a validated recommendation artifact; kb-intake ingests it).
3. Scope = full lifecycle (Buy/Watchlist/Avoid for candidates; Add/Hold/Trim/Sell
   for holdings), position-aware via classification JSON.
4. Research data is pluggable via a source registry; `yfinance` is source #1.
5. Every tunable number is documented and edited via a dedicated
   `author-decision-rubric` skill (validated + version-bumped); CLAUDE.md points
   to it.
6. Recommendation carries an Analyst View (the LLM's own opinion, separate from
   the mechanical verdict). Model split: `opus` (Opus 4.8) for the judgment
   (scoring + Analyst View), `haiku` for the mechanical steps.

## What was built

**Rubric** — `Knowledge-Base/taxonomy/decision-rubric.yml`: `sources` registry
(yfinance / classification / derived), 4 gates, 7 weighted 1-5 dimensions
(weights sum to 1.0; dividend_safety conditional), position-aware `verdict_bands`,
`confidence_rules`, `time_horizon_rules`. Hand-curated; references
`decision-framework.yml` enums.

**Skill `evaluate-stock-decision`** — `scripts/rubric.py` (loader + validator +
shared math: weight renormalization, weighted score, band lookup, confidence),
`scripts/scoring_worksheet.py` (derived metrics + evidence resolution +
worksheet scaffold; unsupplied source → unknown, never error),
`scripts/validate_recommendation.py` (schema + citation resolution + recomputed
math so the LLM cannot override the verdict). Contract in
`references/recommendation-contract.md`.

**Skill `author-decision-rubric`** — `scripts/check_rubric.py` (validate + diff
summary + version-bump enforcement) and `references/rubric-authoring.md`
(every tunable field explained).

**Agents** — `stock-data-prep` (`haiku`, mechanical: context → fetch → worksheet)
and `stock-analyst` (`opus`, judgment: score → narratives + Analyst View →
validate). Docs under `docs/agents/stock-*/`.

**Ingest** — `kb-update-thesis/scripts/ingest_recommendation.py` re-validates the
artifact, rewrites Status-block Decision/Confidence/Time Horizon/Last Updated
(never Portfolio Status), appends a Decision History row + decision/update logs,
with an idempotence guard. A new `## Analyst View` thesis section (added to the
template and `thesis_page.py` required sections) holds the model's opinion,
rewritten each update. kb-intake and kb-update-thesis docs updated.

**Tests** — `test_decision_rubric.py`, `test_scoring_worksheet.py`,
`test_validate_recommendation.py`, `test_ingest_recommendation.py`, plus an
Analyst-View case in `test_kb_thesis_scripts.py`. Deterministic fixtures in
`tests/_decision_fixtures.py`; no live yfinance.

## Deliberately deferred

Additional research sources (documents, peer/sector data, other APIs — the
registry is ready), technical indicators, news-sentiment scoring, unusual-options
detection, batch portfolio-review mode, and automatic Portfolio Status
transitions on Buy/Sell (only user-confirmed trades change status).
