# Portfolio Classifier Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/portfolio-classifier.md`](../../../.claude/agents/portfolio-classifier.md).
The original design rationale is preserved in [`plan.md`](plan.md).

## Purpose

The agent classifies a portfolio with one supported request: `Classify my portfolio`.
It must stay narrow, deterministic, and read-only, and it runs the scripted workflow only.

## Runtime Settings

The agent is a Claude Code subagent. Its key settings, declared in the agent
front matter:

- `model: haiku` — a fast, low-cost model is sufficient because the agent's only
  job is to invoke one fixed script and report the result.
- `tools: ["Bash"]` — least privilege. The agent needs only to run the single
  classification command; it has no Read/Write/Edit access.

## Skill Dependencies

The agent's front matter declares only its direct dependency:

```yaml
skills:
  - classify-portfolio
```

`read-portfolio-classification-data` and `fetch-yfinance-classification-data`
are deliberately not listed there — the `skills:` field preloads a skill's
SKILL.md into the agent's own context, and neither is meant to be read or
invoked by the agent directly (see "Why The Skills Are Separate" below). The
full dependency graph, for reference:

| Skill | Role | Used by |
|---|---|---|
| `classify-portfolio` | Public orchestration workflow; classifies holdings from the approved DuckDB and output contract | `portfolio-classifier` agent |
| `read-portfolio-classification-data` | Read-only DuckDB access using fixed queries | `classify-portfolio`'s `classification_workflow.py`, in-process |
| `fetch-yfinance-classification-data` | Restricted, ephemeral yfinance enrichment | `classify-portfolio`'s `classification_workflow.py`, in-process |

## Workflow

1. The `portfolio-classifier` agent is the public entrypoint.
2. It calls the `classify-portfolio` skill workflow, invoking only
   `python .claude/skills/classify-portfolio/scripts/classify_portfolio.py --pretty`.
3. That workflow reads portfolio data through `read-portfolio-classification-data`.
4. It enriches missing allowlisted metadata through `fetch-yfinance-classification-data`.
5. It returns deterministic JSON from the constrained rules and approved output contract.

## Why The Skills Are Separate

- `classify-portfolio` owns orchestration and output validation.
- `read-portfolio-classification-data` owns read-only DuckDB access.
- `fetch-yfinance-classification-data` owns narrow, ephemeral yfinance lookups.

Keeping those responsibilities separate makes the workflow easier to audit and
safer to change without widening the agent's permissions.

## Guardrails

- Do not accept arbitrary SQL, database paths, or ad hoc Python execution.
- Do not expose raw yfinance field selection or unsupported market-data modes.
- Do not persist enrichment back to DuckDB or cache it outside the workflow.
- Do not override populated database values with enrichment data.

## Code Location

Each skill's `scripts/` directory owns its own logic, not a shared borrowed
module:

- `.claude/skills/classify-portfolio/scripts/classification_workflow.py` is
  the thin orchestrator (build enrichment requests, merge results, classify,
  validate, write output) and `portfolio_classifier.py` is the rules engine.
  Both live beside the skill that owns them, not in `src/`.
- `.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py`
  owns the read-only DuckDB query and schema validation itself. It still
  imports shared, pipeline-wide constants (`DATABASE_PATH`,
  `DATABASE_SCHEMA_VERSION` from `src/config.py`; `REQUIRED_TABLES`,
  `SCHEMA_COMPONENT` from `src/database.py`) since those are also used
  elsewhere in the pipeline, not skill-exclusive logic.
- `.claude/skills/fetch-yfinance-classification-data/scripts/fetch_classification_data.py`
  owns the yfinance field allowlists and fetch logic itself, with **no
  imports from `src/` at all** — it calls `yfinance` directly.

`classification_workflow.py` imports `read_classification_data` and
`fetch_classification_data` directly from those two sibling skill
directories (adding each to `sys.path` itself, so it resolves correctly
regardless of which script invoked it first). `src/app.py` still exposes a
`portfolio-classify` subcommand that runs the same workflow for command-line
use.

## Persisting Classification Results

The agent and its skills remain fully read-only, exactly as described above —
they only ever produce the JSON file. Persisting that JSON into the database
is handled by a separate, decoupled pipeline stage, not by the agent:

- `src/database_command.py::upload_portfolio_classifications()` reads the
  classification JSON file, resolves each holding's `ticker_id` via its
  `ticker` and `fields.exchange` (a lookup against `tickers`, since the JSON
  output deliberately never exposes internal DB primary keys), and fully
  replaces the contents of the `portfolio_classifications` table (delete
  then re-insert, in one transaction) every run.
- `python src/app.py classification-sync` is the CLI entry point for this
  step, following the same "extract, then separately sync" split already
  used for market data (`src/yfinance_extractor.py` fetches;
  `src/market_data.py` + `src/database_command.py` persist).

This keeps the "read-only agent" guarantee intact — `classification-sync` is
a distinct, explicitly-invoked pipeline command, never run implicitly as
part of "Classify my portfolio." (`python src/app.py pipeline`, a separate,
human-invoked ingestion command unrelated to the agent, does run classify +
sync automatically as its final step for a full run — see `docs/reference/cli.md`
— but that is orthogonal to this guarantee: the agent itself still never
triggers a database write.)
