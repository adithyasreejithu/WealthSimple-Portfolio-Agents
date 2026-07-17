# Technical Analysis for the Stock Decision-Support System

*Status: **proposed — not yet approved for implementation** (2026-07-14). This plan
records the readiness assessment and full design; it moves to
`docs/plans/implementation/` in as-built form once executed.*

## Context

The goals doc (`docs/plans/goals/stock_decision_support_system.md` §7) requires
technical analysis — price trend, moving averages, support/resistance, gap
fills, breakouts, relative strength, volume trends, RSI, MACD — but it was
**deliberately deferred** when the scoring system shipped
(`docs/architecture/decision_support_flow.md`, "What is deliberately
deferred"). Today every thesis page has an empty `## Technical Analysis`
section, and `evaluate-stock-decision/SKILL.md` explicitly tells the analyst
to leave it alone.

### Readiness assessment: buildable now

All prerequisites exist:

- Daily OHLCV (`Date/Open/High/Low/Close/Adj Close/Volume`, default 400
  calendar days ≈ 274 trading rows) is already fetched by
  `fetch-stock-research-data` (`history` group) and persisted at
  `exports/stock-recommendations/<TICKER>-<date>-research.json`.
- pandas + numpy are installed; no TA library is needed (hand-rolled math,
  ~10 lines per indicator).
- The rubric's `sources:` registry was designed for exactly this — a new
  source drops in without changing the recommendation contract; a missing
  source degrades to `unknown`, never errors.
- Benchmark history for relative strength is fetchable via the existing
  `src/yfinance_extractor.py::fetch_security_history` primitive.

Two pre-existing gaps must be fixed as part of this work (verified in code):

1. `scoring_worksheet.py::build_worksheet` only populates evidence contexts
   for `yfinance`/`derived`/`classification` — a `--source technical=...`
   file loads but its evidence silently resolves "unavailable".
2. `validate_recommendation.py::_resolve_citation` special-cases the same
   three sources and returns `False` for any other — `technical:` citations
   would be rejected.

### Script vs agent: skill script, not an agent

The system's core principle is "deterministic Python computes, the analyst
judges against written anchors; the LLM never predicts markets." Indicator
math is pure computation, so it becomes a new skill run by `stock-data-prep`
(haiku) as one more mechanical step; the `stock-analyst` (opus) scores the
resulting evidence against rubric anchors like every other dimension. No new
agent. (The goals doc imagined a "Technical Analysis Agent," but the shipped
architecture consolidated mechanical work into `stock-data-prep` and judgment
into `stock-analyst`; this design follows the shipped architecture.)

## Fixed decisions

1. Full rubric integration: new scored `technical` dimension (rubric v1.2
   with reweighting), which is what wires the empty `## Technical Analysis`
   thesis section to auto-populate via the analyst's `section_updates`.
2. Relative-strength benchmark: `XEQT.TO` (`config.DEFAULT_BENCHMARK_SYMBOL`),
   overridable via `--benchmark`.
3. Hand-rolled pandas/numpy math; no new dependency (no `ta`/`pandas-ta`/
   `talib`).
4. New deterministic skill, not an agent.

## What will be built

### 1. New skill: `analyze-stock-technicals`

```
.claude/skills/analyze-stock-technicals/
├── SKILL.md                            # name + description; CLI usage, contract, guardrails
├── scripts/technical_analysis.py       # standalone argparse CLI (repo pattern: parents[4] root, sys.path src/)
└── references/technical-indicators.md  # written calculation spec (formulas, thresholds, null rules)
```

CLI: `--ticker` (required), `--research <path>` (required), `--benchmark`
(default `DEFAULT_BENCHMARK_SYMBOL`), `--no-benchmark`, `--output`,
`--pretty`. Reads the ticker's `data.history` from the research JSON; fetches
benchmark via `fetch_security_history` (injectable frame argument so tests
never hit the network; fetch failure → `benchmark_ok: false` + null RS
fields, exit 0). Output is JSON-safe (NaN→null, numpy scalars→Python).

**Artifact:** `exports/stock-recommendations/<TICKER>-<date>-technical.json`,
schema `technical-analysis.v1`, a **flat dict** (not a per-ticker list) so
`technical:trend.direction` resolves via the existing dotted-path lookup.
Top-level groups: `price`, `trend`, `momentum`, `levels`, `gaps`, `volume`,
`relative_strength`, `events`, `summary`, `data_sufficiency` (plus
`schema/ticker/generated/as_of/benchmark/params`).

### 2. Calculations (deterministic pandas; each nulls out below its row minimum)

| Computation | Spec |
|---|---|
| SMAs | 20/50/200 `rolling().mean()`; `price_vs_smaN_pct`; slopes over 10/20/60 rows respectively |
| Golden/death cross | sign change of `sma50 − sma200`; all in-window events + `last_sma_cross` with `days_since` |
| RSI(14) | Wilder via `ewm(alpha=1/14, adjust=False, min_periods=14)`; state bands: >70 overbought, 55–70 bullish, 45–55 neutral, 30–45 bearish, <30 oversold; null < 15 rows |
| MACD(12,26,9) | `ewm(span, adjust=False)`; line/signal/histogram, sign-change cross events (last 3), histogram trend vs 5 sessions back; null < 35 rows |
| Support/resistance | pivot highs/lows (`High[i] == max(High[i−10:i+11])`, window 10); greedy clustering at 2.0% tolerance; levels carry `touches` + `last_touch_date`; nearest support below / resistance above close with signed `distance_pct`; `near_support`/`near_resistance` at ≤3%; needs ≥25 rows |
| Gaps | gap-up: `Low[i] > High[i−1]` by ≥0.5% (symmetric for gap-down); **filled** when a later session trades back through the entire gap range; emit open gaps (≤10, newest first) with range, size, signed distance from close |
| Volume | 20d and 90d average volume + ratio; up-day/down-day volume ratio over 20 sessions; `volume_trend` rising/flat/falling at ±15% |
| Relative strength | stock return − XEQT.TO return over 30/90/180 **calendar** days (same convention as `scoring_worksheet._price_return`); `rs_state` out/in/underperforming at ±2 on rs_90d |
| 52-week range | high/low over last 252 rows; `pct_from_52w_high/low` |
| Breakout/breakdown | close beyond the strictly-prior 60-session extreme within the last 10 sessions; most recent event with level + `pct_beyond`; always an object (nullable fields) |
| Trend classification | `uptrend` = close>sma50, sma50>sma200 (or sma200 null), sma50 slope>0; `downtrend` symmetric; else `sideways`; `insufficient_data` if sma50 null |
| Data sufficiency | rows, date range, per-indicator availability flags, benchmark status; note any >40% single-day move (unadjusted-split warning, since auto_adjust=False) |

`stock-data-prep` will fetch with `--history-days 600` (~410 trading rows) so
SMA200 + slope and true 52-week levels are fully covered.

### 3. Pipeline plumbing fixes (required)

- `.claude/skills/evaluate-stock-decision/scripts/scoring_worksheet.py` —
  after the existing context population in `build_worksheet`, add a generic
  passthrough: `for name, (_p, payload) in sources.items():
  contexts.setdefault(name, payload)`. `setdefault` preserves the
  yfinance/classification special handling.
- `.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py`
  — in `_resolve_citation`, replace the final `return False, None` with a
  generic dotted-path fallback:
  `value = ws._resolve_path(sources[source], path); return value is not None, value`.

`rubric.py` and `ingest_recommendation.py` need **zero changes** (verified —
ingest matches any `section_updates` key against real `## headers`, and
`## Technical Analysis` exists in the thesis template).

### 4. Rubric v1.2 (via the `author-decision-rubric` skill workflow)

Edit `Knowledge-Base/taxonomy/decision-rubric.yml`:

- **Register source** `technical:` with `skill: analyze-stock-technicals` and
  the 10 groups above.
- **New dimension** `technical` — weight **0.08**, `applies_when: always`,
  `section: "Technical Analysis"`; evidence fields:
  `technical:trend.direction`, `momentum.rsi_14`, `momentum.macd_state`,
  `relative_strength.rs_90d`, `price.pct_from_52w_high`,
  `levels.nearest_support_distance_pct`, `events.breakout.type`,
  `gaps.open_gap_count`, `volume.volume_trend`. Anchors: 5 = confirmed
  uptrend + RSI 50–70 + bullish MACD + rs_90d>5 + recent breakout/near
  52-week high; 3 = sideways/mixed, rs_90d ±5, mid-range; 1 = confirmed
  downtrend, RSI<40 or fresh bearish cross, rs_90d<−5, breakdown, or >25% off
  the 52-week high with overhead gaps.
- **Reweight**: `market_sentiment` 0.15 → **0.07** (its price-momentum
  content moves to `technical`; remove `derived:return_90d/365d` from its
  evidence and rewrite anchors around analyst ratings + news only). All other
  weights unchanged; sum stays 1.0. `derived` keeps its return metrics
  (non-breaking).
- Bump `version: v1.1 → v1.2`, run `check_rubric.py`, log via
  `append_log.py --log update --action rubric-updated`.

Rollout is non-breaking: without a technical file the dimension scores
`unknown` and renormalizes away. Side effect to document: post-v1.2, **High
confidence effectively requires the technical artifact** (High needs 0
unknown dimensions) — intended.

### 5. Agent + doc updates

- `.claude/agents/stock-data-prep.md` — add `analyze-stock-technicals` to
  `skills:`; fetch step gains `--history-days 600`; new step between fetch
  and worksheet: run `technical_analysis.py --ticker <T> --research
  <research.json> --output ...-technical.json` (benchmark failure non-fatal);
  worksheet step gains `--source technical=<path>`; report step includes the
  technical artifact + its `data_sufficiency`.
- `.claude/skills/evaluate-stock-decision/SKILL.md` — add `technical` to the
  source example and dimension→section table; **delete** the "Technical
  Analysis has no rubric dimension yet — leave it alone" paragraph; mirror in
  `references/recommendation-contract.md` if it enumerates sources.
- `.claude/agents/stock-analyst.md` — no changes needed (worksheet-driven).
- `docs/architecture/decision_support_flow.md` — add the step; remove
  technical indicators from "deliberately deferred".
- `.claude/skills/author-decision-rubric/references/rubric-authoring.md` —
  update the data-availability constraint (TA now available via the
  `technical` source).
- `docs/agents/stock-data-prep/architecture.md`,
  `docs/agents/stock-analyst/architecture.md` — reflect the new step/source.
- `fetch_stock_research_data.py` docstring: "future Technical Analysis Agent"
  → point at the new skill.
- Move this plan to `docs/plans/implementation/` in as-built form on
  completion.

### 6. Tests (unittest, deterministic fixtures, no live yfinance)

- **New `tests/test_technical_analysis.py`** — sys.path-insert of the skill's
  `scripts/` (house pattern from `test_kb_thesis_scripts.py`); synthetic
  business-day OHLCV helper. Cases: RSI hand-verified values (all-up → 100;
  mixed 15-bar exact Wilder); SMA/trend direction on linear up/down series;
  short-history nulls (60 rows → sma200 null; 10 rows → `insufficient_data`,
  no crash); MACD cross on V-shaped series; engineered golden-cross date; gap
  open/filled/sub-threshold; sawtooth support/resistance clusters + nearest
  levels; exact volume ratios; RS with injected benchmark frame +
  `--no-benchmark` nulls; breakout above prior 60-bar max; CLI round-trip
  (`--no-benchmark --output`, valid JSON, schema id).
- `tests/_decision_fixtures.py` — add `technical_payload()` fixture.
- `tests/test_scoring_worksheet.py` — technical source supplied → evidence
  `available: true`; absent → dimension `unknown: true`.
- `tests/test_validate_recommendation.py` — `technical:` citation resolves
  via the generic fallback; bogus path fails.
- `tests/test_decision_rubric.py` — assert v1.2 has a `technical` dimension
  with section "Technical Analysis"; weight-sum test auto-covers.

## Implementation order

1. Skill + script (`analyze-stock-technicals/`)
2. `tests/test_technical_analysis.py` — iterate to green
3. Plumbing fixes in `scoring_worksheet.py` + `validate_recommendation.py`
   plus their test/fixture updates
4. Rubric v1.2 via the author-decision-rubric workflow (+ rubric test
   additions)
5. Agent/skill doc edits (`stock-data-prep.md`, `evaluate-stock-decision/SKILL.md`)
6. Repo docs + as-built plan doc

## Verification

1. Run `fetch_stock_research_data.py --ticker <held ticker> --history-days
   600`, then `technical_analysis.py` on the output; sanity-check the
   artifact (RSI ∈ [0,100], nearest support below price / resistance above,
   gaps sane, `benchmark_ok`).
2. Run `scoring_worksheet.py` with all three `--source` flags; confirm the
   technical dimension's evidence has `available: true`.
3. One full end-to-end evaluate flow (worksheet → recommendation artifact →
   `validate_recommendation.py` exit 0 → `ingest_recommendation.py` populates
   `## Technical Analysis` on the page).
4. `python -m unittest discover -s tests` — full suite green.
5. `check_rubric.py` passes with the version bump detected.

## Deliberately deferred

- Intraday data (daily bars only).
- Split/adjusted-close handling (unadjusted prices retained; >40%
  single-day-move note surfaces suspected splits to the analyst).
- Chart patterns, Fibonacci levels, composite technical scores.
- A second benchmark (S&P 500) for relative strength.

## Risks

- **The plumbing gap is the sharp edge** — without the two generic-source
  fixes, everything silently degrades to `unknown` scores and validator
  rejections. Fix and test first-class (step 3).
- **Unadjusted prices** (auto_adjust=False): splits distort indicators —
  surfaced via the >40% single-day-move note; dividend micro-gaps absorbed by
  the 0.5% gap threshold.
- **Benchmark fetch can be blocked** — graceful degradation (nulls, exit 0)
  so prep never fails on it.
- **pandas 3.x**: only stable APIs used (`rolling`, `ewm(adjust=False)`,
  `diff`, `clip`); convert numpy scalars before `json.dumps`.
