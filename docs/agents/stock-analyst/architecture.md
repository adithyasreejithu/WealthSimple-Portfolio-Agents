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
  `stock-data-prep`.
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

## The rubric it applies

`Knowledge-Base/taxonomy/decision-rubric.yml` — hard gates, weighted 1-5
dimensions with anchors, position-aware verdict bands, and confidence rules. The
agent reads it but never edits it (that is the `author-decision-rubric` skill's
job). The agent proposes `action`/`confidence`/`time_horizon`; the validator
recomputes them from the rubric and rejects any mismatch, so the LLM cannot
override the mechanical verdict.

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
