# Rubric authoring reference

Every field in `Knowledge-Base/taxonomy/decision-rubric.yml` and how to tune it.
The rubric is the human-authored policy the stock-analyst applies; the LLM does
not invent criteria. Edit here, validate with `check_rubric.py`, bump the
version, log it.

## Top-level

| Field | Meaning | Tuning |
|---|---|---|
| `version` | Rubric version (e.g. `v1.0`). | Bump on any gate/weight/band/source change. |
| `framework` | Points at `decision-framework.yml` for the action/confidence/horizon enums. | Do not change; those enums are the source of truth. |
| `sources` | Registry of research sources criteria may cite. | Add an entry before citing a new source. |

## `sources`

Each key is a source id; evidence fields are written `"<id>:<path>"`. Today:

- `yfinance` -- the fetch-stock-research-data pull; `groups` lists the 11 groups.
- `classification` -- the classify-portfolio holdings JSON (portfolio-fit).
- `derived` -- metrics computed by `scoring_worksheet.py`; `groups` lists the
  metric names. Adding a new derived metric means adding both the name here and
  its computation in `scoring_worksheet.py` (`DERIVED_METRICS`).

To register a **new source** (e.g. a filings API): add an entry with a `groups`
list, then write criteria that cite `newsource:...`. A criterion whose source is
not supplied at scoring time scores `unknown`, so partial rollout is safe.

## `gates`

Hard disqualifiers. Any gate that **fails** forces Avoid (not held) / Sell
(held) regardless of score. Each gate:

- `id` -- stable identifier.
- `applies_when` -- `always`, `dividend_payer`, `income_role`, `equity_only`, or
  `etf_only`; or a **list** of these (all must hold, e.g. `[income_role,
  equity_only]`). `equity_only` applies to non-funds, `etf_only` to funds --
  this is how the rubric excludes company gates (solvency, profitability) for an
  ETF instead of leaving them `unknown`.
- `fail_when` -- prose the analyst evaluates against the cited evidence. Write it
  as a clear boolean condition ("negative FCF AND debt/equity > 2 AND current
  ratio < 1").
- `evidence_fields` -- the `source:path` values the worksheet resolves for this
  gate. Keep them to fields a registered source actually provides.

An unevaluable gate is `unknown`, never `pass`: any unknown gate caps confidence
at Low. Tune a gate by editing `fail_when` thresholds, not by removing the
"unknown != pass" behavior.

## `dimensions`

Weighted 1-5 scores. **Weights must sum to 1.0 within each asset-class track**
-- the dimensions an equity is scored on (`always` + `equity_only` +
conditionals) sum to 1.0, and the dimensions a fund is scored on (`always` +
`etf_only` + conditionals) sum to 1.0 independently. `check_rubric.py` enforces
both. Each dimension:

- `weight` -- its share of the weighted average. If you raise one, lower another
  **in the same track** so that track still totals 1.0. An `etf_only` dimension
  (e.g. `fund_efficiency`, `fund_quality`) affects only the fund track; an
  `equity_only` one (e.g. `financial_health`, `growth`) only the equity track.
- `applies_when` -- `always`, `dividend_payer`, `equity_only`, `etf_only`, or a
  list. A non-applicable dimension is dropped and its weight redistributed
  pro-rata across the rest of that security's applicable dimensions.
- `anchors` -- the 1 / 3 / 5 descriptions the analyst scores against. Make them
  concrete and evaluable from the fetched data (name the metric and the cutoff).
  Editing an anchor cutoff (e.g. "FCF yield above 6%" -> "above 5%") is the main
  way you tune scoring.
- `evidence_fields` -- the fields the worksheet resolves for the dimension.

A dimension with no available evidence scores `unknown` and is excluded from the
weighted average (weights renormalize).

## `verdict_bands`

Position-aware mapping from weighted score to action. Read in descending order;
the first band whose `min` <= score wins. `not_held` uses
Buy/Watchlist/Avoid; `held` uses Add/Hold/Trim/Sell. `gate_fail` sets the action
when any gate fails. The lowest `min` in each state must be <= 1.0 so the whole
range is covered. Raise a `min` to make an action harder to reach (e.g. Buy
`min: 4.0` -> `4.2` demands a stronger score).

## `confidence_rules`

Checked High -> Medium -> Low; first whose thresholds all pass wins.
`max_unknown_dimensions`, `max_unknown_gates`, `min_groups_ok` gate each level.
Both High and Medium require `max_unknown_gates: 0`, so any unknown gate forces
Low. Loosen confidence by raising the `max_unknown_*` allowances.

`min_groups_ok` may be a single integer (applies to both tracks) or a per-track
mapping `{equity: N, etf: N}` -- funds have 4-5 structurally-empty equity groups
(financials, earnings, analyst, insider, institutional) that never count, so the
fund threshold is lower than the equity one. Only groups that returned usable
(non-empty, non-errored) data count toward `groups_ok`.

## `time_horizon_rules`

`default_by_role` maps a portfolio role to a default horizon; `catalyst_driven`
is the horizon to use when a dated catalyst drives the action. Horizons must be
decision-framework `time_horizons` values.

## Data-availability constraints (do not violate)

The yfinance pull is single-ticker and raw. Criteria must **not** depend on data
no source provides. Currently out of reach (see
`fetch-stock-research-data/references/yfinance-research-contract.md`):

- peer / sector-relative valuation and historical valuation ranges (needs a
  multi-ticker source -- a natural first new source),
- technical indicators (RSI/MACD/support-resistance) -- only raw OHLCV and the
  simple 30/90/365-day returns exist,
- news/social sentiment scoring, unusual-options detection -- only raw headlines
  and a simple put/call OI ratio exist.

If you want a criterion that needs one of these, register the source that
provides it first, or accept that it will score `unknown`.
