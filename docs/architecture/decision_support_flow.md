# Decision support flow

How the repository goes from raw research data to a recommendation recorded on a
thesis page. This is the interpretation layer that
[`stock_research_data_pull.md`](stock_research_data_pull.md) deferred to "later
agents".

## The pipeline

```
RESEARCH SOURCES            stock-data-prep (haiku)       stock-analyst (opus)        exports/stock-recommendations/
┌────────────────────────┐  mechanical steps only:        judgment only:              <TICKER>-<date>.json
│ fetch-stock-research-   │  kb-search context,            score each gate + dimension  (validated artifact,
│ data  (source id:       │─▶ fetch data, read position ─▶ against cited evidence, ───▶ evidence source-tagged)
│ "yfinance")             │  context, run                 write narratives +                     │
│ [future: documents,     │  scoring_worksheet.py          Analyst View                          ▼
│  other APIs — new ids]  │  → worksheet JSON              (validator recomputes math)  kb-intake ─▶ Knowledge-Base/
└────────────────────────┘                                                             (ingest)   stocks/TICKER.md
                                                                                                  + Decision History
                                                                                                  + logs
```

## The core principle

The LLM is not trained on trading data, so it never predicts the market. The
portfolio owner authors an explicit rubric
(`Knowledge-Base/taxonomy/decision-rubric.yml`): hard gates plus weighted 1-5
dimensions with written anchors. The analyst's job is to **apply** that rubric —
score each criterion against cited evidence — and deterministic Python recomputes
the weighted score, the position-aware verdict, and the confidence level. The
judgment is "does this evidence meet this written criterion", not "what will the
stock do".

## Model split: cheap steps, expensive judgment

One Claude Code agent runs on one model, so the split is two agents:

- **stock-data-prep (`haiku`)** — gathers KB context, fetches research data,
  reads position context, and builds the deterministic scoring worksheet. No
  judgment.
- **stock-analyst (`opus`, Opus 4.8)** — scores every gate and dimension with
  citations, writes the narratives and the Analyst View, and emits the validated
  recommendation. Only judgment.

A single request ("evaluate AAPL") runs data-prep, then analyst on its worksheet.

## The source registry

The rubric's `sources:` block lists the research sources criteria may cite.
Evidence fields are source-qualified (`"yfinance:data.valuation.forwardPE"`,
`"derived:fcf_yield"`, `"classification:holdings.primary_group"`). Today the
sources are:

- `yfinance` — the fetch-stock-research-data pull (12 groups, including the
  fund-only `funds` group from `get_funds_data()`).
- `classification` — the classify-portfolio holdings JSON (portfolio-fit, plus
  `asset_class` and the `etf_details` fields expense_ratio/aum/nav/
  sector_weights/top_holdings that the fund dimensions cite).
- `derived` — metrics computed deterministically by `scoring_worksheet.py`,
  including the options-positioning set (put/call OI and volume ratios, ATM IV
  near/far, IV skew, max-OI strikes).

**Asset-class awareness.** The rubric has two tracks. Company gates/dimensions
(solvency, profitability, financial_health, growth, earnings_catalysts,
insider_activity) are `equity_only`; `fund_efficiency` and `fund_quality` are
`etf_only`. A fund is scored on the fund track, so structurally-absent company
data is *excluded* (weights renormalize) rather than scored `unknown` — an ETF is
no longer forced to Low confidence for lacking a balance sheet.

**Adding a source** (a filings API, ingested documents, peer data) means adding
an entry to `sources:` and any criteria that cite it — the recommendation
artifact shape does not change, and a criterion whose source is not supplied
scores `unknown` rather than erroring, so rollout is incremental. This is where
peer/sector-relative valuation and historical ranges will come from; they are
out of reach of the single-ticker yfinance pull today.

## Unknown data and confidence

Missing evidence is never guessed. A dimension with no available evidence scores
`unknown` and drops out of the weighted average (remaining weights renormalize).
A gate that cannot be evaluated is `unknown`, never `pass`, and caps confidence
at Low. Confidence (`High`/`Medium`/`Low`) is a function of how many gates and
dimensions were unknown and how many research groups came back — defined in the
rubric's `confidence_rules`.

## Rubric verdict vs Analyst View

The recommendation carries both:

- `proposed.action` — the mechanical, band-derived verdict. The validator
  recomputes it from the scores and gate results; the LLM cannot override it.
- `narratives.analyst_view` — the model's own qualitative opinion, kept separate.
  It may agree, question, or dissent, and note what the numbers miss, but it never
  changes the action. Persistent dissent is the signal to retune the rubric via
  the `author-decision-rubric` skill.

## Deterministic prior-decision context and verdict precompute

Two pieces of the workflow exist purely to stop the analyst from re-deriving
information a script can hand it directly (see
`docs/plans/stock-analyst-token-reduction.md` for the transcript analysis that
motivated them):

- **`position.prior_decision`** — `scoring_worksheet.py --thesis-page
  <stocks/TICKER.md>` deterministically extracts the ticker's last recorded
  `{date, action, verdict, confidence, time_horizon}` from the page's Decision
  History table and Status block (`null` for a new page) and embeds it in the
  worksheet. The analyst uses this directly for `proposed.verdict_vs_previous`
  reasoning and never reads old dated artifacts under
  `exports/stock-recommendations/` or scans the thesis page's history itself.
- **`validate_recommendation.py --precompute-only`** — prints the deterministic
  `weighted_score`/`action`/`confidence`/`default_time_horizon` for whatever
  gates/dimensions are filled in so far (skipping citation resolution and
  narrative checks), from the same `compute_verdict()` function the full
  validator uses. The analyst iterates against this while scoring instead of
  importing `rubric.py` internals by hand to predict what the validator will
  accept.

Both are advisory/context-only: the full `validate_recommendation.py` run (no
flag) remains the actual gate before an artifact is saved.

## Multi-ticker / batch runs

Evaluating several tickers in one request splits into a parallel half and a
sequential half:

- **Parallel-safe: `stock-data-prep` and `stock-analyst`.** Both are
  read-only against `Knowledge-Base/` and write only to
  `exports/stock-recommendations/<TICKER>-<date>-*.json` -- a unique path per
  ticker. Fan these out concurrently across tickers; there is no shared
  mutable state for them to race on.
- **Sequential only: the wiki-commit step — and it is ONE `kb-intake`
  invocation, not N.** `kb-update-thesis`'s helpers in `src/kb_pages.py`
  (`rebuild_index`, `rebuild_thesis_views`, `rebuild_main_index`,
  `append_log_row`) each read a shared file in full, compute new contents, and
  overwrite the whole file -- there is no lock or version check. `stocks/index.md`,
  `theses/<status>/index.md`, the main `index.md`, and `logs/*.md` are all
  touched by every ticker's commit, so two concurrent commits silently clobber
  each other's rows. But sequential does **not** mean one agent spawn per
  ticker: `ingest_recommendation.py` is fully deterministic (no LLM call), so
  spawning a fresh kb-intake agent per artifact just re-pays the agent's whole
  context ~N times for a mechanical step (observed at ~1M+ tokens per spawn).
  Invoke **one** `kb-intake` agent with the full artifact list; it loops
  `ingest_recommendation.py` over them one at a time via Bash and reports the
  per-ticker script output. It must not Read the artifacts -- the script output
  is its report input.

The pattern for N tickers: run `stock-data-prep` + `stock-analyst` for all N
concurrently — **one agent invocation per ticker**, split into an ETF batch and a
stock batch run at the same time — wait for every recommendation artifact, then
hand the whole artifact list to **a single `kb-intake` invocation** that commits
them sequentially.

**Anti-pattern (observed on the first full-portfolio run):** handing a single
`stock-data-prep` (or `stock-analyst`) agent a list of all N tickers to loop
over. That serializes the entire fetch/score phase inside one agent context and
throws away the parallelism this boundary exists to enable — the 23-holding run
took one long sequential pass instead of N concurrent ones. Fan out N separate
invocations; do not loop inside one. (The commit step is the deliberate
exception: it is mechanical script execution, not analysis, so one looping
agent is cheaper than N spawns and just as correct.)

**Model tiering:** the equity-batch `stock-analyst` invocations run on the
agent's default model (Opus) — that judgment is where the strongest model is
worth it. The **ETF batch** scores fund-appropriate criteria (expense ratio,
concentration, distributions), a materially simpler judgment: invoke those
stock-analyst runs with a `model: sonnet` override (Agent tool `model`
parameter). The validator recomputes all rubric arithmetic regardless of model,
so the verdict math cannot drift with the model choice.

## Ownership

| Artifact | Owner | Editable by agents? |
|---|---|---|
| `Knowledge-Base/taxonomy/decision-rubric.yml` | portfolio owner, via `author-decision-rubric` | No — read-only to KB agents |
| `exports/stock-recommendations/*.json` | stock-data-prep (worksheet), stock-analyst (recommendation) | Yes, under `exports/` only |
| `Knowledge-Base/stocks/TICKER.md` | `kb-intake` (single wiki writer) | Only kb-intake; Original Thesis immutable |

## What is deliberately deferred

Technical indicators (RSI/MACD/support-resistance -- no `technical` dimension
yet, `Technical Analysis` stays a manual thesis-page section), peer/sector-relative
and historical valuation (needs a new multi-ticker source), and automatic status
transitions on Buy/Sell (only user-confirmed trades change Portfolio Status).

Headline-level news sentiment, insider transaction direction, and a fuller
options-positioning read are scored today (`market_sentiment`,
`options_activity`, `insider_activity` dimensions). `options_activity` now goes
beyond a single put/call OI ratio: `scoring_worksheet.py` derives the put/call
volume ratio, ATM IV term structure (near vs far expiry), IV skew, and the
max-open-interest strikes from the fetched chains. Still deferred (data yfinance
does not provide, no matter the code): implied-volatility history/percentile
("is IV high *for this name*"), unusual-activity detection versus a volume
baseline, option greeks, and intraday flow. Those need an options-history or
flow source, not more computation on the end-of-day snapshot — see
`docs/reference/yfinance_data_availability.md`.
