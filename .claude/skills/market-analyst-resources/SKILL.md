---
name: market-analyst-resources
description: Pull, persist, and diff macro/market indicators (rates, inflation, labour, policy, credit, breadth, volatility, sector and factor leadership, FX/commodities, upcoming events) against a hand-curated registry, and emit a digest plus an on-disk bundle. Use as the data layer beneath the (not-yet-built) market-researcher agent, or any time a macro/market landscape snapshot is needed. Never characterizes the regime -- only fetches, transforms mechanically per the registry, and reports what changed since the last report.
---

# Market Analyst Resources

Deterministic, no-judgment data layer. See
`docs/plans/market-analyst-resources-skill.md` for the full design,
including the two audits that ruled out storing this in the pipeline's main
database.

Run `uv run python .claude/skills/market-analyst-resources/scripts/market_analyst_resources.py`:

- Default (no `--mode`): refresh due indicators, then read and emit a
  **digest** to stdout plus a **bundle** JSON to
  `exports/market-analyst-resources/<date>-market.json`.
- `--mode gate` — read-only per-indicator freshness verdict, JSON to stdout.
  No writes, no fetch. Safe even before `market.duckdb` exists (every
  configured indicator reports `due_reason: "backfill"`).
- `--mode refresh` — the **only** write path. Fetches every due indicator
  (including a first-time backfill, detected as a coverage gap rather than a
  special "first run" flag) and persists it.
- `--mode read` — builds the digest + bundle from what's already stored.
  Mostly read-only: it also does one brief, separate write to record the
  run, which is what "since last report" diffing is anchored on.
- `--domain rates credit ...` — limit `read` output to these domains.
- `--only <domain...>` / `--max-indicators N` — slice a `refresh` (useful
  for the first large backfill across ~50 indicators without holding the
  write lock for all of them at once).
- `--force-refresh` / `--no-bundle` / `--output PATH` / `--pretty` /
  `--registry-path PATH` / `--db-path PATH` / `--run-date YYYY-MM-DD` /
  `--trace-log-path PATH` — see
  [market-resource-contract.md](references/market-resource-contract.md).
  `--db-path` is for testing only: pointing at a non-default file suppresses
  run recording, so a debugging run can't silently reset the diff baseline
  other invocations rely on. It also suppresses run-workspace attachment,
  below, for the same reason.

Every `read` emits one completeness-trace line to `logs/SkillTrace.txt` and
`logs/SkillTrace.jsonl`, shared with `investment-analyst-resources`
(`docs/architecture/usage_tracking.md`). The registry's `<TBD>` stubs count
as **not-applicable**, not as gaps — they are indicators whose source hasn't
been chosen yet, not data this run failed to fetch.

## Run workspace: default-on

Any invocation that produces a bundle (`--mode read` or the default
refresh→read sequence) **attaches to a run workspace automatically**, the
same default-on behavior as the sibling `investment-analyst-resources` skill,
via the one shared implementation (`workspace.run.ensure_run`). `--mode gate`
and `--mode refresh` never attach; neither produces a bundle to register.

- `--run-id ID` — name the run explicitly, e.g. to group a market pull with a
  same-session ticker pull into one run. Attaches if it exists, creates it
  under that exact name if not.
- `--no-run` — opt out; the completeness trace logs only to the shared
  `logs/SkillTrace` files, as before this became the default. The opt-out is
  itself recorded in the trace (`workspace.skip_reason`), so it stays a
  visible fact rather than a silent gap.

See `docs/architecture/run_workspace.md`.

## Why a separate database

`market.duckdb`, sibling to the pipeline's `PRD_WealthSimple.duckdb`, not a
new table inside it. Two audits found the alternative unsafe: putting
non-owned "market proxy" rows in the pipeline's `tickers` table corrupts the
portfolio valuation date axis and can shadow a real holding's price chart,
and even adding *isolated* new tables to the pipeline database breaks two
other read-only skills (`investment-analyst-resources`, `classify-portfolio`)
the moment the schema version bumps. A dedicated file avoids both problems
and needs **zero changes to `src/`**.

## Skills pull, agents analyze

This skill never characterizes a regime, applies a threshold, or picks a
label. Every value it emits is either a raw as-published observation or a
transform explicitly declared per-indicator in
`Knowledge-Base/taxonomy/market-indicators.yml` (`yoy_pct`, `spread`, a
rolling `pct_change`, ...). The registry is hand-curated by the portfolio
owner; this skill reads it, never writes it. All narrative judgment —
"policy is turning supportive," "breadth is narrowing" — belongs to the
market-researcher agent this skill is designed to sit under, not here.

## Sources at a glance

| Domain | Coverage | Source |
|---|---|---|
| `policy`, `fx_commodities` (USD/CAD) | Covered | `boc_valet` (Bank of Canada Valet, no key) |
| `rates`, `volatility`, `fx_commodities` (oil/gold/copper/natgas/DXY), `sectors`, `factors`, `credit` | Covered | `yfinance` |
| `growth`, `inflation`, `labour`, `breadth`, `positioning` | `<TBD>` stubs in the registry | Source not yet chosen |
| `events` | Hand-maintained calendar in the registry | No adapter |

`registry.Indicator.is_configured` is `False` for `<TBD>` stubs — the gate
and refresh skip them without treating them as errors, so the skill runs
end-to-end today even with five domains still unfilled.

## Revisions

Macro statistics get revised after publication (GDP, payrolls especially).
`macro_observations` is insert-on-change and append-only
(`PRIMARY KEY (series_id, obs_date, first_seen_at)`): an unchanged refetch
just bumps `last_confirmed_at`, a changed value inserts a new row. Every
reader goes through `v_macro_current` (latest `first_seen_at` per
series/date), so this costs nothing in query complexity while making
`delta_vs_last_report` and revision detection possible at all — an
overwrite design could not tell a revision apart from real economic change.

## What it does not do

- Does not compute portfolio-exposure linkage ("which of my holdings are
  affected") — that is explicitly the market-researcher agent's job, via
  `src/analytics.py`'s existing `get_look_through_sector_exposure`,
  `get_currency_exposure`, and `get_sector_allocation`.
- Does not write to `Knowledge-Base/` beyond reading the registry. The
  market-researcher agent authors the KB note under
  `Knowledge-Base/market-research/macro-notes/`.
- Does not touch the pipeline's `PRD_WealthSimple.duckdb`, `src/`, or
  `docs/reference/cli.md` (no new `src/app.py` command).
- Does not run a topological sort for `derived` entries — depth is capped
  at 1 (a `derived` may not reference another `derived`), so evaluation is
  always "all leaves, then all derived."
- Does not fabricate a release calendar. `events:` is a short hand-pasted
  list the owner updates roughly once a year from the official BoC/Fed
  schedules; freshness otherwise uses
  `next_expected = last_obs + cadence + publication_lag_days`, not a full
  calendar.

Read [market-resource-contract.md](references/market-resource-contract.md)
for the full digest/bundle schema, the freshness-gate cadence rules, the
transform and derived-op reference, and the completeness-trace states.
