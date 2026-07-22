# Stock recommendation contract

The artifact the stock-analyst produces and `kb-intake` ingests. Schema id
`stock-recommendation.v1`. Written to
`exports/stock-recommendations/<TICKER>-<YYYY-MM-DD>.json`. It is validated by
`validate_recommendation.py`, which recomputes the math from
`Knowledge-Base/taxonomy/decision-rubric.yml` so the LLM cannot override the
rubric's verdict.

## Source registry

Evidence fields are source-qualified: `"<source>:<path>"`. The sources today:

| Source | Meaning | Path shape |
|---|---|---|
| `yfinance` | fetch-stock-research-data output (per-ticker item) | `data.<group>.<field>` (e.g. `data.valuation.forwardPE`, `data.funds.fund_operations`) |
| `classification` | classify-portfolio holdings JSON (this ticker's holding) | `holdings.<field>` (e.g. `holdings.primary_group`, `holdings.expense_ratio`, `holdings.sector_weights`) |
| `derived` | metrics computed by `scoring_worksheet.py` | metric name (e.g. `fcf_yield`, `revenue_growth_yoy`, `debt_to_equity`, `current_ratio`, `return_90d`, `put_call_oi_ratio`, `put_call_volume_ratio`, `atm_iv_near`, `atm_iv_far`, `iv_skew`, `max_oi_call_strike`, `max_oi_put_strike`, `groups_ok_count`) |

A citation must resolve to a **non-null and non-empty** value in the named source,
or the validator rejects it. "Non-empty" matters for ETFs: yfinance returns
several structurally-empty groups (empty tables, `{"expirations": []}`) with
`errors={}`, and a citation into one of those is treated as unresolved, not
valid. A future source (documents, another API) is added to the rubric's
`sources:` block; the artifact shape does not change.

## Artifact schema

```json
{
  "schema": "stock-recommendation.v1",
  "ticker": "AAPL",
  "generated": "2026-07-13",
  "rubric_version": "v1.0",
  "research_sources": {
    "yfinance": {"path": "AAPL-2026-07-13-research.json", "groups_ok": ["overview", "..."], "groups_failed": {"options": "msg"}},
    "classification": {"path": "portfolio-classification.json"}
  },
  "position": {"held": true, "weight_pct": 3.2, "portfolio_role": "Quality",
               "dividend_payer": true, "income_role": false,
               "asset_class": "stock", "is_etf": false,
               "page_exists": true,
               "prior_decision": {"date": "2026-05-01", "action": "Hold",
                                   "verdict": "unchanged", "confidence": "Medium",
                                   "time_horizon": "Long-term"}},
  "gates": [
    {"id": "solvency", "result": "pass",
     "evidence": [{"source": "yfinance", "field": "yfinance:data.valuation.freeCashflow", "value": 90000000000, "note": "positive FCF"}]}
  ],
  "dimensions": [
    {"id": "valuation", "weight": 0.20, "score": 3, "rationale": "fwd P/E ~28, near target",
     "evidence": [{"source": "yfinance", "field": "yfinance:data.valuation.forwardPE", "value": 28.0, "note": "..."}]}
  ],
  "weighted_score": 3.65,
  "unknown_dimensions": ["options_activity"],
  "proposed": {"action": "Hold", "confidence": "Medium", "time_horizon": "Long-term", "verdict_vs_previous": "unchanged"},
  "narratives": {
    "executive_summary": "...",
    "analyst_view": "The rubric says Hold; I agree but note ...",
    "updated_thesis": "Para 1 ...\n\nPara 2: stronger/weaker/unchanged/broken and why ...",
    "company_overview": "... (required only when the page does not yet exist)",
    "original_thesis": "... (required only when the page does not yet exist)",
    "bull_case": ["..."], "bear_case": ["..."], "key_risks": ["..."],
    "open_questions": ["..."], "monitoring": ["..."],
    "section_updates": {"Valuation Analysis": "- Metric: value — interpretation\n- Score: 3/5 (fairly valued)",
                         "Financial Analysis": "...\n- Score (financial_health): 4/5\n- Score (growth): 3/5",
                         "Earnings and Catalysts": "...", "Market Sentiment": "...",
                         "Insider Activity": "...", "Options Activity": "...",
                         "Portfolio Fit": "..."}
  },
  "facts_assumptions_opinions": {"facts": ["..."], "assumptions": ["..."], "opinions": ["..."]},
  "sources": ["yfinance via fetch-stock-research-data 2026-07-13"]
}
```

`position.prior_decision` (from `scoring_worksheet.py --thesis-page`) is the
ticker's last recorded date/action/verdict/confidence/time_horizon, or `null`
for a new page. It is informational context for the analyst's
`proposed.verdict_vs_previous` reasoning -- the validator does not check it.

## Precompute mode (`validate_recommendation.py --precompute-only`)

Reuses `validate_recommendation.py`'s own math (via `compute_verdict()`) to
print the expected `weighted_score`/`action`/`confidence`/`default_time_horizon`
for a draft artifact's `position`/`gates`/`dimensions`, skipping citation
resolution and narrative checks entirely (`_load_sources()` is never called).
Use it while filling in gates and dimensions, instead of importing `rubric.py`
by hand to predict the verdict; run the full validate (no flag) once the
artifact is complete. `groups_ok` (part of the confidence calculation) is read
from `research_sources.yfinance.groups_ok` -- copy that block from the
worksheet into the draft before relying on the printed confidence value.

## Validator rules (`validate_recommendation.py`)

- `schema` equals `stock-recommendation.v1`; `ticker` present.
- Every **applicable** gate (given `position.dividend_payer` / `income_role` /
  `is_etf`) is addressed; `result` in `{pass, fail, unknown}`; a `pass`/`fail`
  needs >= 1 evidence citation. For a fund (`is_etf: true`) the company gates
  (`solvency`, `profitability_or_path`, `dividend_integrity`) are **not**
  applicable and must be omitted, not scored unknown.
- Every applicable dimension is addressed; `score` is an integer 1-5 or
  `"unknown"`; a numeric score needs >= 1 evidence citation. Funds are scored on
  `fund_efficiency` + `fund_quality` in place of the equity-only company
  dimensions (`financial_health`, `growth`, `earnings_catalysts`,
  `insider_activity`).
- Every evidence `field` resolves to a non-null **and non-empty** value in the
  source file it names (re-read from `research_sources[id].path`, relative to
  the artifact).
- `weighted_score` equals the rubric's renormalized weighted average of the
  scored applicable dimensions (unknown and non-applicable excluded).
- `proposed.action` equals `lookup_action(weighted_score, gate_failed, held)` --
  a failed gate forces Avoid (not held) / Sell (held).
- `proposed.confidence` equals the rubric's `confidence_rules` outcome for the
  unknown-gate / unknown-dimension / groups-ok counts. Any unknown gate -> Low.
- `proposed.action` / `confidence` / `time_horizon` are decision-framework enums.
- `narratives.{executive_summary, analyst_view, updated_thesis, bull_case,
  bear_case, key_risks}` non-empty; `section_updates` a non-empty mapping.
- **Point-form sections**: each `section_updates` value must be point form -- at
  least one `- ` line and a final `- Score: X/5` line (labeled
  `- Score (dim_id): X/5` when several dimensions share one section, e.g.
  Financial Analysis; `unknown` allowed for an unknown-scored dimension).
- **Thesis depth**: `updated_thesis` must be at least two blank-line-separated
  paragraphs and state stronger/weaker/unchanged/broken with reasoning.
- **Length ceilings** (rejected when exceeded; guidance is half the ceiling):
  a `section_updates` block over 12 bullets, `updated_thesis` over 6 paragraphs,
  `executive_summary` over 240 words, `analyst_view` over 600 words.
- **New-page prose**: when `position.page_exists` is false, `company_overview`
  and `original_thesis` are required (ingest seeds them once, on creation;
  Original Thesis is immutable thereafter).
- `facts_assumptions_opinions.{facts, assumptions, opinions}` are lists.

The validator does not check that `section_updates` keys match a real
thesis-page header (that's a commit-time concern) -- but `ingest_recommendation.py`
does, at commit time. Each rubric dimension's `section` field (surfaced in the
worksheet) is the required key; a key that doesn't match any `## ` header on
the page is dropped with a stderr warning rather than silently discarded.

## Analyst View

`narratives.analyst_view` is the model's own qualitative judgment, kept distinct
from the mechanical rubric verdict. It may agree with, question, or dissent from
`proposed.action` and explain what the scored dimensions miss -- but it never
changes `proposed.action`, which stays the band-derived value. Persistent
dissent is a signal to retune the rubric via the `author-decision-rubric` skill.
