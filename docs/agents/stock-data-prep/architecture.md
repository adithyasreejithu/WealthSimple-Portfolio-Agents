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
  scripts; `Read` is used only for `kb-search` output (the agent no longer reads
  the thesis page itself, see Workflow and Guardrails); `Write` is scoped by the
  guardrails to `exports/stock-recommendations/` only.

## Skill Dependencies

```yaml
skills:
  - kb-search
  - fetch-stock-research-data
  - bootstrap-stock-research
  - evaluate-stock-decision
```

| Skill | Role |
|---|---|
| `kb-search` | Check whether `stocks/TICKER.md` exists (page content is not read here). |
| `fetch-stock-research-data` | Pull the 12 yfinance research groups (incl. fund-only `funds`) into JSON. |
| `bootstrap-stock-research` | First-run only: fetch ephemeral annual financial context (decision #9). |
| `evaluate-stock-decision` | Build the scoring worksheet (`scoring_worksheet.py`). |

## KB-population inputs (from `kb-orchestrator`)

When dispatched by `kb-orchestrator`, the prompt carries two extra inputs from
the staleness gate:

- **`first_run`** — no `stocks/TICKER.md` yet. The agent additionally invokes
  `bootstrap-stock-research` (`annual-financial-context --ticker <TICKER>`) and
  hands the resulting `annual-financial-context.v1` JSON to the analyst as
  one-time narrative context for the initial Company Overview / Original Thesis
  (decision #9). This context is ephemeral — never written to `Knowledge-Base/`,
  never persisted, never re-fetched on an incremental run.
- **`due_sections`** — on an incremental run, the agent fetches/prepares only
  data relevant to the stale sections, avoiding wasted fetches. On a first run
  every section is due, so `due_sections` is ignored and the full worksheet is
  prepared.

## Workflow

1. `kb-search` for the ticker; check whether `stocks/TICKER.md` exists (existence
   check only — the page itself is not read; see Guardrails).
2. Fetch research data to `exports/stock-recommendations/<TICKER>-<date>-research.json`.
   Stop if the ticker has no verified provider symbol.
3. Build the worksheet to `exports/stock-recommendations/<TICKER>-<date>-worksheet.json`
   with every `--source` (including the classification JSON) plus `--thesis-page
   Knowledge-Base/stocks/<TICKER>.md` whenever step 1 found the page exists. The
   worksheet auto-detects asset class (ETF vs stock), selects the matching
   rubric track, deterministically derives `page_exists` and
   `position.prior_decision` (last Decision History row + Status
   confidence/time horizon) from `--thesis-page`, and prints a **Data-prep
   summary** to stdout: asset class, position (held/weight/role), prior
   decision, classification freshness, and per-group ok/empty/failed. **Stop**
   and ask for a re-classification if the summary reports the classification
   stale (>~7 days) or missing, rather than trusting stale holdings.
4. Report — from the stdout summary, never by opening the JSONs or the thesis
   page — the worksheet path, detected asset class, prior decision, and a
   data-sufficiency note that distinguishes `groups_ok` / `groups_empty`
   (structurally empty, normal for ETFs) / `groups_failed`, plus the next step
   (see Handoffs).

## Guardrails

- No scoring, no opinions, no proposed action — every worksheet slot is left
  empty for the analyst.
- **Never Reads the JSON artifacts, and never Reads `stocks/TICKER.md`.** The
  research JSON, worksheet, and classification JSON run to hundreds of KB, and
  the scripts' stdout summaries carry everything the report needs.
  `--thesis-page` gets the worksheet builder what it needs from the thesis page
  without this agent opening it — reading it for Original Thesis prose is
  `stock-analyst`'s job (at most once), not this agent's. Only `kb-search`
  output is read.
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
