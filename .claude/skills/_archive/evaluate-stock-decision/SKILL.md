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
    --thesis-page Knowledge-Base/stocks/AAPL.md \
    --output exports/stock-recommendations/AAPL-<date>-worksheet.json

# 2. (LLM) Read the worksheet, score every gate + dimension with citations,
#    write the narratives + Analyst View, and save a draft recommendation JSON.

# 2b. While scoring, iterate against the deterministic verdict math instead of
#     reverse-engineering rubric.py -- prints weighted_score/action/confidence/
#     default_time_horizon for whatever's filled in so far (skips citation and
#     narrative checks; not a substitute for step 3):
python .claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py \
    --path exports/stock-recommendations/AAPL-<date>.json --precompute-only

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
guessed. `--thesis-page` (when the page exists) lets the worksheet builder
derive `page_exists` and `position.prior_decision` (last Decision History row +
Status confidence/time horizon) deterministically -- see "Reference material is
already in the worksheet" below.

## Reference material is already in the worksheet -- don't re-read it

The worksheet embeds everything the analyst needs per gate/dimension:
`fail_when` text (gates), `anchors` and `section` text (dimensions), and
`position.prior_decision` (the last recorded date/action/verdict/confidence).
This means the analyst should **not**:

- Re-read `decision-rubric.yml`, `decision-framework.yml`, or
  `recommendation-contract.md` for fields already embedded in the worksheet.
- Read the *source code* of `scoring_worksheet.py`, `validate_recommendation.py`,
  or `rubric.py`, or run `python -c` to import their internals to predict the
  verdict -- use `validate_recommendation.py --precompute-only` (step 2b above)
  instead; it runs the exact same code the final validator uses.
- Read old dated artifacts under `exports/stock-recommendations/` (any
  `<TICKER>-<older-date>*.json`) to reconstruct prior-decision context -- that
  is what `position.prior_decision` is for.

These behaviors were observed adding 40-50+ extra turns per ticker in real runs
(see `docs/plans/stock-analyst-token-reduction.md`) without changing the
resulting artifact.

## Workflow

1. **Build the worksheet.** Run `scoring_worksheet.py` with every available
   `--source` plus `--thesis-page <path>` when the ticker's thesis page exists.
   It resolves each gate/dimension's cited evidence to concrete values, computes
   the derived metrics (FCF yield, YoY revenue growth, debt/equity, current
   ratio, price returns, put/call OI ratio, ...), works out the position context
   (held / weight / role / dividend payer / `prior_decision`), and leaves empty
   `result`/`score` slots. It never scores. `position.prior_decision` (from
   `--thesis-page`) is `{date, action, verdict, confidence, time_horizon}` from
   the page's last Decision History row + Status block, or `null` for a new page.
2. **Score every gate and dimension (LLM judgment).** For each gate set
   `result` to `pass` / `fail` / `unknown` against its `fail_when` text; for each
   dimension set `score` to an integer 1-5 (matching the `anchors`) or
   `"unknown"`. Every non-unknown item needs 1-3 `evidence` citations
   (`{source, field, value, note}`) whose `field` is a real, non-null path in a
   supplied source. **Missing evidence stays unknown -- never guess a score.**
3. **Write the narratives.** Fill `executive_summary`, `updated_thesis`,
   `bull_case`, `bear_case`, `key_risks`, `open_questions`, `monitoring`,
   per-section `section_updates`, and the `facts_assumptions_opinions` split.
   Write the **`analyst_view`** -- your own qualitative opinion, where you agree
   or disagree with the rubric's mechanical verdict and what the numbers miss.
   The Analyst View may dissent but does not change `proposed.action`, which
   stays the band-derived value.

   **Format (enforced by the validator):**
   - **Dimension sections are point form.** Each `section_updates` value is a
     bullet list of `- Metric: value — interpretation` lines, ending with a
     `- Score: X/5 (label)` line. When two dimensions share a section (Financial
     Analysis), write one block and give each its own labeled score line:
     `- Score (financial_health): 4/5` and `- Score (growth): 3/5`. A dimension
     scored `unknown` uses `- Score: unknown`.
   - **Be selective, not exhaustive.** Aim for **at most 6 bullets per dimension
     section** — cite the metrics that drive the score, not every field in the
     worksheet. The validator rejects a section over 12 bullets.
   - **`updated_thesis` is prose, 2-3 paragraphs** (at least two; the validator
     rejects more than six), and must state explicitly whether the thesis is
     stronger / weaker / unchanged / broken versus the original and why.
   - **Length ceilings:** `executive_summary` ~120 words, `analyst_view` ~300
     words (validator rejects at 2x those). Longer narratives are not better —
     they cost output tokens and bury the signal on the thesis page.
   - **`company_overview` and `original_thesis` are required when the page does
     not yet exist** (`position.page_exists` false). `company_overview` may draw
     on `overview.longBusinessSummary`; `original_thesis` is the initial
     investment reasoning and is immutable once written. Omit both when the page
     already exists.
   - **Options Activity** must interpret, together, the put/call OI **and** volume
     ratios, the IV term structure (`atm_iv_near` vs `atm_iv_far`), `iv_skew`, and
     the max open-interest strikes vs spot -- not just a single ratio. If the
     option chains are empty (common for Canadian-listed ETFs), score
     `options_activity` unknown.

   **`section_updates` keys must equal each scored dimension's `section` field
   from the worksheet, verbatim** -- `ingest_recommendation.py` transcribes by
   exact `## {key}` header match and drops (with a warning) anything that
   doesn't match a real thesis-page section. The current dimension -> section
   mapping (also visible per-dimension in the worksheet):

   | Dimension | Section | Track |
   |---|---|---|
   | `valuation` | Valuation Analysis | both |
   | `financial_health`, `growth` | Financial Analysis (combine into one block) | equity |
   | `fund_efficiency`, `fund_quality` | Financial Analysis (combine into one block) | ETF |
   | `earnings_catalysts` | Earnings and Catalysts | equity |
   | `dividend_safety` | Dividend Analysis | dividend payers |
   | `market_sentiment` | Market Sentiment | both |
   | `insider_activity` | Insider Activity | equity |
   | `options_activity` | Options Activity | both |
   | `portfolio_fit` | Portfolio Fit | both |

   For an **ETF** the worksheet already omits the equity-only gates and
   dimensions (solvency, profitability_or_path, dividend_integrity,
   financial_health, growth, earnings_catalysts, insider_activity) -- do not add
   them back or mark them unknown; score the fund dimensions (`fund_efficiency`,
   `fund_quality`) that appear instead.

   `Technical Analysis` has no rubric dimension yet (needs RSI/moving-average
   computation) -- leave it alone; do not invent a `section_updates` entry
   for it.
4. **Do not compute the verdict yourself.** Set `proposed.action`,
   `confidence`, and `time_horizon` to what the rubric produces (the validator
   recomputes and will reject a mismatch), and `weighted_score` to the
   renormalized weighted average. Use `--precompute-only` (step 2b above) to get
   these values as you go, rather than reverse-engineering `rubric.py`.
5. **Validate.** Run `validate_recommendation.py` (no flag) and fix until it
   exits 0.
6. **Commit.** Run `ingest_recommendation.py --path <artifact>` to commit the
   recommendation to `Knowledge-Base/stocks/TICKER.md`. This step is
   deterministic (no LLM call): it transcribes prose from the artifact's
   narratives mechanically, creates the page if missing, and appends the
   Decision History row. Never edits Original Thesis on an existing page.

See [references/recommendation-contract.md](references/recommendation-contract.md)
for the full artifact schema and the validator's rules.

## Multi-ticker / batch runs

Evaluating several holdings at once (e.g. the whole portfolio) splits into a
parallel half and a sequential half. **Do not hand one agent a list of N tickers
to loop over** -- that serializes the whole run inside a single agent context.
Instead:

1. **Refresh classification first.** `stock-data-prep` reads
   `portfolio-classification.json` as-is; regenerate it (portfolio-classifier)
   before a batch so held/weight/asset_class are current.
2. **Fan out data-prep + scoring, one agent invocation per ticker, in parallel.**
   `stock-data-prep` and `stock-analyst` are read-only against `Knowledge-Base/`
   and each ticker writes only its own
   `exports/stock-recommendations/<TICKER>-<date>-*.json`, so there is no shared
   state to race on. Split the portfolio into an **ETF batch and a stock batch
   and run both concurrently** (the asset-class split keeps like tickers
   together; either batch is still N independent per-ticker invocations, not one
   loop).
3. **Commit sequentially -- in ONE kb-intake invocation.** The wiki index/log
   helpers read and overwrite shared files with no locking, so concurrent
   commits can drop rows -- but `ingest_recommendation.py` is deterministic, so
   do not pay a fresh agent spawn per ticker either. Hand the full artifact
   list to a **single** `kb-intake` invocation, which loops the ingest script
   over them one at a time via Bash. See
   [../../../docs/architecture/decision_support_flow.md](../../../docs/architecture/decision_support_flow.md)
   ("Multi-ticker / batch runs") for the full rationale.

Do not edit `Knowledge-Base/taxonomy/decision-rubric.yml` (that is the
`author-decision-rubric` skill's job) or anything under `Knowledge-Base/ref/`.
Write only under `exports/stock-recommendations/`.
