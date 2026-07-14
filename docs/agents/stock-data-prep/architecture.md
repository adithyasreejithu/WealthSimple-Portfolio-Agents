# Stock Data Prep Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/stock-data-prep.md`](../../../.claude/agents/stock-data-prep.md).
Design rationale is in [`plan.md`](plan.md).

## Purpose

The mechanical first half of the stock decision-support workflow. It gathers KB
context, pulls research data, reads portfolio/position context, and builds the
deterministic scoring worksheet that the `stock-analyst` agent then scores. It
forms no opinions and writes nothing to the wiki.

## Runtime Settings

- `model: haiku` — this agent only runs scripts in a fixed order and reports
  paths. There is no judgment here, so the fast/cheap model is deliberate. The
  judgment runs on Opus in `stock-analyst`. This model split (cheap steps,
  expensive judgment) is the reason the workflow is two agents rather than one.
- `tools: ["Bash", "Read", "Write"]` — `Bash` runs the fetch and worksheet
  scripts; `Read` opens the classification JSON and any existing thesis page;
  `Write` is scoped by the guardrails to `exports/stock-recommendations/` only.

## Skill Dependencies

```yaml
skills:
  - kb-search
  - fetch-stock-research-data
  - evaluate-stock-decision
```

| Skill | Role |
|---|---|
| `kb-search` | Find an existing `stocks/TICKER.md` and its current decision. |
| `fetch-stock-research-data` | Pull the 11 yfinance research groups into JSON. |
| `evaluate-stock-decision` | Build the scoring worksheet (`scoring_worksheet.py`). |

## Workflow

1. `kb-search` for the ticker; read the page if it exists (capture Status block +
   Original Thesis).
2. Read `exports/portfolio-classification/portfolio-classification.json` for
   held/weight/role. Stop if the ticker has no verified provider symbol.
3. Fetch research data to `exports/stock-recommendations/<TICKER>-<date>-research.json`.
4. Build the worksheet to `exports/stock-recommendations/<TICKER>-<date>-worksheet.json`
   with every `--source`.
5. Report the worksheet path, data-sufficiency note, and next step (see Handoffs).

## Guardrails

- No scoring, no opinions, no proposed action — every worksheet slot is left
  empty for the analyst.
- Writes only under `exports/stock-recommendations/`; never in `Knowledge-Base/`.
- No fabrication of symbols or values; failures are reported as-is.
- No trades.

## Handoffs

| Label | Agent | Prompt |
| --- | --- | --- |
| Score the worksheet | stock-analyst | "Run stock-analyst on the worksheet at `<worksheet_path>` for `<TICKER>`." |
| Redirect pure KB lookups | kb-discovery | "This is a pure lookup of what the KB already knows about `<TICKER>`, not new research — kb-discovery handles that." |

## Concurrency

Safe to run concurrently across multiple tickers: read-only on
`Knowledge-Base/`, and each ticker writes its own
`exports/stock-recommendations/<TICKER>-<date>-*.json`, so there is no shared
mutable state to race on. Only the later `kb-intake` wiki-commit step must run
one ticker at a time — see "Multi-ticker / batch runs" in
[`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).

## Code Location

Logic lives in the skills it invokes: `fetch-stock-research-data/scripts/` and
`evaluate-stock-decision/scripts/scoring_worksheet.py` (which imports the shared
`rubric.py`). The agent itself contains no logic beyond invoking those skills.
See [`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md)
for the full flow.
