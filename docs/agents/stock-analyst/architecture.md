# Stock Analyst Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/stock-analyst.md`](../../../.claude/agents/stock-analyst.md).
Design rationale is in [`plan.md`](plan.md).

## Purpose

The judgment half of the stock decision-support workflow. It applies the
hand-curated decision rubric to the worksheet `stock-data-prep` produced —
scoring each gate and dimension against cited evidence, writing the narratives
and the Analyst View — and emits a validated recommendation artifact for
`kb-intake` to commit. It is read-only on the wiki and executes no trades.

## Runtime Settings

- `model: opus` (Opus 4.8) — applying written criteria to evidence and forming
  the Analyst View is judgment, so this agent gets the strongest model. It is the
  only judgment step in the workflow; the mechanical steps run on `haiku` in
  `stock-data-prep`. **ETF-batch exception:** in a portfolio-wide run the fund
  track scores simpler criteria (expense ratio, concentration, distributions),
  so ETF invocations are launched with a `model: sonnet` override (Agent tool
  `model` parameter) — the agent definition keeps `opus` as its default, and
  `validate_recommendation.py` recomputes the verdict math regardless of model.
- `tools: ["Bash", "Read", "Write"]` — `Read` opens the worksheet and any
  existing thesis page; `Bash` runs `validate_recommendation.py`; `Write` is
  scoped by the guardrails to `exports/stock-recommendations/` only.

## Skill Dependencies

```yaml
skills:
  - evaluate-stock-decision
```

| Skill | Role |
|---|---|
| `evaluate-stock-decision` | Score the worksheet and validate the recommendation (`validate_recommendation.py`, and the rubric/worksheet contracts). |

## First-run annual context (decision #9)

On a ticker's first-ever run, `stock-data-prep` hands over an
`annual-financial-context.v1` JSON (a couple of years of annual statements the
shallow quarterly window can't yet provide). The analyst uses it **only** as
narrative background for the initial `company_overview` / `original_thesis` —
it is never a scored gate or dimension, never cited as evidence, and never
persisted. On an incremental run there is no such context.

## The rubric it applies

`Knowledge-Base/taxonomy/decision-rubric.yml` — hard gates, weighted 1-5
dimensions with anchors, position-aware verdict bands, and confidence rules. The
agent reads it but never edits it (that is the `author-decision-rubric` skill's
job). The agent proposes `action`/`confidence`/`time_horizon`; the validator
recomputes them from the rubric and rejects any mismatch, so the LLM cannot
override the mechanical verdict.

The rubric is **two-track** by asset class. For an ETF the worksheet already
omits the `equity_only` company gates and dimensions (solvency,
profitability_or_path, dividend_integrity, financial_health, growth,
earnings_catalysts, insider_activity) and includes `fund_efficiency` +
`fund_quality` instead — the analyst scores what the worksheet presents and does
not treat the omitted equity items as unknowns.

## Precompute iteration loop

The rubric's gate/dimension text (`fail_when`, `anchors`, `section`) and the
ticker's `position.prior_decision` (last recorded date/action/verdict/
confidence/time_horizon) are already embedded in the worksheet by
`scoring_worksheet.py` — the agent does not re-read `decision-rubric.yml`,
`decision-framework.yml`, `recommendation-contract.md`, old dated artifacts
under `exports/stock-recommendations/`, or the thesis page's Decision History
table for this. While scoring, it saves its draft artifact and iterates with
`validate_recommendation.py --path <draft> --precompute-only`, which prints the
deterministic `weighted_score`/`action`/`confidence`/`default_time_horizon` from
the same code the full validator uses, rather than importing `rubric.py`
internals by hand. It runs the full validate (no flag) once the artifact is
complete. See `docs/plans/stock-analyst-token-reduction.md` for the transcript
analysis that motivated this (observed 40-50+ extra turns per ticker without
these guardrails) and `docs/architecture/decision_support_flow.md`'s
"Deterministic prior-decision context and verdict precompute" section.

## Output format

Dimension sections are transcribed to the thesis page as point form —
`- Metric: value — interpretation` lines ending in a `- Score: X/5` line (labeled
per-dimension when a section is shared). `updated_thesis` is multi-paragraph
prose stating stronger/weaker/unchanged/broken. On a first-time page the analyst
also writes `company_overview` and `original_thesis`. The validator enforces
these; see the recommendation contract.

## Analyst View

The recommendation carries an `analyst_view` — the model's own qualitative
opinion, kept distinct from the rubric's mechanical verdict. It may agree with,
question, or dissent from the band-derived action and note what the scored
dimensions miss, but it never changes `proposed.action`. Persistent dissent is
the signal to retune the rubric.

## Guardrails

- Read-only on `Knowledge-Base/`; writes only under
  `exports/stock-recommendations/`. `kb-intake` commits the recommendation to the
  thesis page.
- Never fabricates evidence; missing data scores `unknown` and lowers confidence.
- Never hand-tunes the band-derived action.
- No trades.
- Never re-reads `decision-rubric.yml`, `decision-framework.yml`, or
  `recommendation-contract.md` for fields already embedded in the worksheet
  (`fail_when`, `anchors`, `section`).
- Never reads the source code of `scoring_worksheet.py`,
  `validate_recommendation.py`, or `rubric.py`, and never runs `python -c` to
  import their internals to predict the verdict — uses `--precompute-only`
  instead.
- Never reads old dated artifacts under `exports/stock-recommendations/`;
  prior-decision context comes from the worksheet's `position.prior_decision`.
  Reads the *current* `stocks/<TICKER>.md` at most once, only if it needs
  Original Thesis prose for narrative continuity.

## Handoffs

| Label | Agent | Prompt |
| --- | --- | --- |
| Commit the recommendation | kb-intake | "Commit the recommendation for `<TICKER>` at `exports/stock-recommendations/<TICKER>-<date>.json` to the thesis page." |
| Request missing worksheet | stock-data-prep | "No worksheet found for `<TICKER>` — run stock-data-prep first to build `exports/stock-recommendations/<TICKER>-<date>-worksheet.json`." |

## Concurrency

Safe to run concurrently across multiple tickers: read-only on
`Knowledge-Base/`, and each ticker writes its own
`exports/stock-recommendations/<TICKER>-<date>.json`, so there is no shared
mutable state to race on. Only the later `kb-intake` wiki-commit step must run
one ticker at a time — see "Multi-ticker / batch runs" in
[`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).

## Code Location

Logic lives in `evaluate-stock-decision/scripts/` (`validate_recommendation.py`
and the shared `rubric.py`). See
[`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).
