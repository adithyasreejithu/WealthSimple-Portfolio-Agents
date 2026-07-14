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

- `yfinance` — the fetch-stock-research-data pull (11 groups).
- `classification` — the classify-portfolio holdings JSON (portfolio-fit).
- `derived` — metrics computed deterministically by `scoring_worksheet.py`.

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

## Multi-ticker / batch runs

Evaluating several tickers in one request splits into a parallel half and a
sequential half:

- **Parallel-safe: `stock-data-prep` and `stock-analyst`.** Both are
  read-only against `Knowledge-Base/` and write only to
  `exports/stock-recommendations/<TICKER>-<date>-*.json` -- a unique path per
  ticker. Fan these out concurrently across tickers; there is no shared
  mutable state for them to race on.
- **Sequential only: the `kb-intake` wiki-commit step.** `kb-update-thesis`'s
  helpers in `src/kb_pages.py` (`rebuild_index`, `rebuild_thesis_views`,
  `rebuild_main_index`, `append_log_row`) each read a shared file in full,
  compute new contents, and overwrite the whole file -- there is no lock or
  version check. `stocks/index.md`, `theses/<status>/index.md`, the main
  `index.md`, and `logs/*.md` are all touched by every ticker's commit. Two
  tickers committed concurrently can each read the file before the other
  writes back, so the second write silently clobbers the first ticker's row
  instead of erroring. Commit tickers one at a time, in a loop, through
  `kb-intake`.

The pattern for N tickers: run `stock-data-prep` + `stock-analyst` for all N
concurrently, wait for every recommendation artifact, then walk the
artifacts through `kb-intake` one at a time.

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

Headline-level news sentiment, options put/call skew, and insider transaction
direction are scored today (`market_sentiment`, `options_activity`,
`insider_activity` dimensions) from data `fetch-stock-research-data` already
pulls -- but only at the coarse level available in the raw yfinance groups, not
deeper NLP sentiment scoring or unusual-volume options detection.
