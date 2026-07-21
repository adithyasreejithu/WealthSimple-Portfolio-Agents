---
name: bootstrap-stock-research
description: Prepare annual financial context for a stock's first-run KB analysis
model: haiku
tools: []
---

# Bootstrap Stock Research

Sets up the initial research context for a stock's Knowledge Base entry on its first-ever analysis run.

## Purpose

When a KB analyst begins work on a new stock that has never been analyzed before (no existing `Knowledge-Base/stocks/TICKER.md`), they need multi-year financial context for the Company Overview / Original Thesis. The quarterly financial snapshots table in the database only accumulates trailing quarters from `financial-snapshots-sync` runs, which is not deep enough for a first-time analysis.

This skill fetches the **annual** financial statements (from yfinance's `financials`, `balance_sheet`, `cashflow` — not the quarterly variants) as a one-time, ephemeral context pass. The analyst uses this for the initial narrative only; it is never persisted to the database or the Knowledge Base.

## Workflow

**Via the prep agent (future work):**
1. Orchestrator checks whether `Knowledge-Base/stocks/TICKER.md` exists.
2. If not (first-ever run): prep agent invokes `annual-financial-context` command, captures the JSON, and passes it to the analyst step as extra context.
3. If yes (incremental run): analyst uses only the persisted `financial_snapshots` / `earnings_events` / `dividend_events` tables, per the normal pipeline workflow.

**Via CLI (manual use by developer):**
```powershell
uv run python src/app.py annual-financial-context --ticker AAPL
uv run python src/app.py annual-financial-context --ticker SHOP.TO --ignore-proxy
```

## Output Schema

Emits a single `annual-financial-context.v1` JSON document to stdout:

```json
{
  "schema": "annual-financial-context.v1",
  "ticker": "AAPL",
  "generated_at": "2026-07-21T14:32:00Z",
  "period_count": 4,
  "periods": [
    {
      "period_end_date": "2022-09-24",
      "revenue": 394328000000.0,
      "net_income": 99803000000.0,
      "eps": 6.11,
      "gross_margin": 0.4331,
      "operating_margin": 0.3029,
      "debt_to_equity": 1.7961,
      "current_ratio": 0.8794,
      "free_cash_flow": 111443000000.0,
      "extra": {
        "income_statement": {...},
        "balance_sheet": {...},
        "cash_flow": {...}
      }
    }
  ],
  "note": null
}
```

- `periods` sorted **ascending** by `period_end_date` (oldest first).
- Named fields are identical to the quarterly `financial_snapshots` schema (revenue, net_income, eps, gross_margin, operating_margin, debt_to_equity, current_ratio, free_cash_flow), so the analyst doesn't learn a second set of field semantics.
- `extra` JSON captures every line item from the annual statements not already consumed by a named column (same structure as quarterly).
- Zero-period case (ETF/fund, or a fetch failure): `periods: []`, `period_count: 0`, `note` set to a human-readable reason — never raises.

## Implementation

- **Script:** `.claude/skills/bootstrap-stock-research/scripts/annual_financial_context.py`
  - `fetch_annual_financial_context(ticker: str) -> dict` — fetch and compute, never raises
  - `parse_args(argv)` — CLI argument parser for `--ticker` (required, singular), `--cache-dir`, `--ignore-proxy`
  - `main(argv)` — CLI entry point, prints JSON to stdout

- **Shared helpers:** reuses `_compute_period_fields`, all label-alias tuples, and `_safe_statement` from `src/financial_snapshots_extractor.py` — single source of truth for both quarterly pipeline and annual context.

- **CLI wiring:** `src/app.py` exposes the command via `annual-financial-context`, delegating to the skill's `main()` entry point. The skill scripts directory is added to `sys.path` only when the command is dispatched (same pattern as `portfolio-classify`).

- **Tests:** `tests/test_bootstrap_annual_context.py` (10 cases covering named-column mapping, alias fallback, computed-FCF fallback, `extra` capture, per-statement exception isolation, empty/ETF envelope, fetch-failure note, blank-ticker handling, argparse, and JSON output).

## Notes

- The singular `--ticker` flag emphasizes this is a single-ticker operation, unlike `--tickers` (plural) in the pipeline commands.
- Annual statements only exist for equities; ETFs return empty results (expected, not an error). The envelope is never omitted — even on zero periods, the caller can always parse the JSON.
- Output is always **ephemeral**: the JSON is printed to stdout, captured by the orchestrator, and never written to disk or database. Decision #12 in the Financial Snapshots plan calls this out: agents never read result JSONs directly off disk.
