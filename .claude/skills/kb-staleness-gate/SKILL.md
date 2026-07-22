---
name: kb-staleness-gate
description: Compute which owned tickers have a due stock-page section this run
model: haiku
tools: []
---

# KB Staleness Gate

Decides which owned tickers the KB population workflow should process this run,
and which sections of each page are stale enough to rewrite. Run first by the
`kb-orchestrator` agent, before any prep/analyst work is dispatched.

## What it does

For every owned ticker (read from `portfolio-classification.json`'s
`holdings[].ticker`, which match `stocks/<TICKER>.md` filenames):

- **No page yet** → due, `first_run: true`, every gated section due.
- **Page exists** → due only if a gated section is past its cadence. Each
  section's last-updated date is `section_updated[<key>]` (decision #16),
  falling back to the page's top-level `updated:` when a key is absent.

DB-backed sections (Earnings and Catalysts, Dividend Analysis, Financial
Analysis) and event-driven sections (Original Thesis, Decision History,
Sources) are **never** gated. Cadence tiers (decision #16):

| Tier | Cadence | Sections |
|---|---|---|
| Weekly | 7 days | `status`, `market_sentiment`, `options_activity` |
| Monthly | 30 days | `valuation_analysis`, `company_overview`, `bull_case`, `bear_case`, `key_risks`, `open_questions`, `monitoring_checklist`, `portfolio_fit`, `updated_thesis`, `analyst_view`, `insider_activity` |

(`technical_analysis` joins the weekly tier once that phase ships — not gated
today.)

## Read-only guarantee

This skill never writes to `Knowledge-Base/` or anywhere else. It only reads
page front matter and prints JSON to stdout. `--dry-run` therefore computes
identically to a normal run; it exists to make "inspecting only" explicit.

## Usage

```powershell
# What would be due right now (safe, read-only):
uv run python .claude/skills/kb-staleness-gate/scripts/staleness_gate.py --dry-run

# Scope a targeted run to exactly these tickers, each fully due:
uv run python .claude/skills/kb-staleness-gate/scripts/staleness_gate.py --force NVDA AAPL
```

`--force TICKER...` scopes the run to exactly the named tickers (each fully
due, `first_run` set only when the page is genuinely missing) so a targeted
2-3 ticker verification run stays small instead of also pulling in every other
stale ticker.

## Output

One `kb-staleness-gate.v1` JSON document:

```json
{
  "schema": "kb-staleness-gate.v1",
  "run_date": "2026-07-21",
  "due_tickers": [
    {"ticker": "AAPL", "first_run": false, "due_sections": ["status", "market_sentiment"]},
    {"ticker": "NEWCO", "first_run": true, "due_sections": ["...all gated sections..."]}
  ],
  "skipped_count": 42
}
```

## Implementation

- **Script:** `.claude/skills/kb-staleness-gate/scripts/staleness_gate.py`
  - `compute_due_tickers(owned_tickers, force=None, dry_run=False, *, kb_root=None, run_date=None) -> dict`
  - `load_owned_tickers(classification_json)` — owned pipeline symbols
  - `parse_args` / `main` — CLI (`--force`, `--dry-run`, `--classification-json`, `--kb-root`)
- **Shared helper:** reuses `kb_pages.parse_page_file` (single canonical
  front-matter parser) from `src/`.
- **Tests:** `tests/test_staleness_gate.py`.
