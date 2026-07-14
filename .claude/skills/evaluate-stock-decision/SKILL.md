---
name: evaluate-stock-decision
description: Score a stock against the hand-curated decision rubric (gates + weighted 1-5 dimensions) using fetched research evidence, and emit a validated stock-recommendation artifact. Use as the scoring step of the stock decision-support workflow, after fetch-stock-research-data has produced the research JSON, to turn evidence into a Buy/Sell/Hold/Trim/Add/Watchlist/Avoid recommendation for kb-intake to commit.
---

# Evaluate Stock Decision

Turn fetched research evidence into a recommendation by applying the rubric in
`Knowledge-Base/taxonomy/decision-rubric.yml`. The rubric is human-authored; the
LLM never predicts the market -- it scores each written criterion against cited
evidence, and deterministic scripts enforce the arithmetic.

Three mechanical scripts bracket the one judgment step:

```
# 1. Build the worksheet (deterministic: resolves evidence + derived metrics)
python .claude/skills/evaluate-stock-decision/scripts/scoring_worksheet.py \
    --ticker AAPL \
    --source yfinance=<research.json> \
    --source classification=<portfolio-classification.json> \
    --output exports/stock-recommendations/AAPL-<date>-worksheet.json

# 2. (LLM) Read the worksheet, score every gate + dimension with citations,
#    write the narratives + Analyst View, and save the recommendation JSON.

# 3. Validate the completed recommendation (deterministic: recomputes the math)
python .claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py \
    --path exports/stock-recommendations/AAPL-<date>.json

# 4. Commit the recommendation to the thesis page (deterministic: transcribes
#    narratives mechanically, creates the page if missing, never calls LLM)
python .claude/skills/kb-update-thesis/scripts/ingest_recommendation.py \
    --path exports/stock-recommendations/AAPL-<date>.json
```

`--source` is `id=path` and repeatable. `yfinance` is the only research source
today; `classification` supplies portfolio-fit context. More sources can be
registered in the rubric's `sources:` block later without changing this
workflow -- a criterion whose source is not supplied is marked `unknown`, never
guessed.

## Workflow

1. **Build the worksheet.** Run `scoring_worksheet.py` with every available
   `--source`. It resolves each gate/dimension's cited evidence to concrete
   values, computes the derived metrics (FCF yield, YoY revenue growth,
   debt/equity, current ratio, price returns, put/call OI ratio, ...), works out
   the position context (held / weight / role / dividend payer), and leaves
   empty `result`/`score` slots. It never scores.
2. **Score every gate and dimension (LLM judgment).** For each gate set
   `result` to `pass` / `fail` / `unknown` against its `fail_when` text; for each
   dimension set `score` to an integer 1-5 (matching the `anchors`) or
   `"unknown"`. Every non-unknown item needs 1-3 `evidence` citations
   (`{source, field, value, note}`) whose `field` is a real, non-null path in a
   supplied source. **Missing evidence stays unknown -- never guess a score.**
3. **Write the narratives.** Fill `executive_summary`, `updated_thesis`
   (state whether the thesis is stronger/weaker/unchanged/broken versus any
   existing page), `bull_case`, `bear_case`, `key_risks`, `open_questions`,
   `monitoring`, per-section `section_updates`, and the
   `facts_assumptions_opinions` split. Write the **`analyst_view`** -- your own
   qualitative opinion, where you agree or disagree with the rubric's mechanical
   verdict and what the numbers miss. The Analyst View may dissent but does not
   change `proposed.action`, which stays the band-derived value.

   **`section_updates` keys must equal each scored dimension's `section` field
   from the worksheet, verbatim** -- `ingest_recommendation.py` transcribes by
   exact `## {key}` header match and drops (with a warning) anything that
   doesn't match a real thesis-page section. The current dimension -> section
   mapping (also visible per-dimension in the worksheet):

   | Dimension | Section |
   |---|---|
   | `valuation` | Valuation Analysis |
   | `financial_health`, `growth` | Financial Analysis (combine both into one prose block) |
   | `earnings_catalysts` | Earnings and Catalysts |
   | `dividend_safety` | Dividend Analysis |
   | `market_sentiment` | Market Sentiment |
   | `insider_activity` | Insider Activity |
   | `options_activity` | Options Activity |
   | `portfolio_fit` | Portfolio Fit |

   `Technical Analysis` has no rubric dimension yet (needs RSI/moving-average
   computation) -- leave it alone; do not invent a `section_updates` entry
   for it.
4. **Do not compute the verdict yourself.** Set `proposed.action`,
   `confidence`, and `time_horizon` to what the rubric produces (the validator
   recomputes and will reject a mismatch), and `weighted_score` to the
   renormalized weighted average.
5. **Validate.** Run `validate_recommendation.py` and fix until it exits 0.
6. **Commit.** Run `ingest_recommendation.py --path <artifact>` to commit the
   recommendation to `Knowledge-Base/stocks/TICKER.md`. This step is
   deterministic (no LLM call): it transcribes prose from the artifact's
   narratives mechanically, creates the page if missing, and appends the
   Decision History row. Never edits Original Thesis on an existing page.

See [references/recommendation-contract.md](references/recommendation-contract.md)
for the full artifact schema and the validator's rules.

Do not edit `Knowledge-Base/taxonomy/decision-rubric.yml` (that is the
`author-decision-rubric` skill's job) or anything under `Knowledge-Base/ref/`.
Write only under `exports/stock-recommendations/`.
