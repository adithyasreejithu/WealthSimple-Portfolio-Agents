---
name: security-technicals
description: Compute per-security price technicals for one or more tickers from stored DuckDB history -- moving averages, max drawdown, annualized volatility, relative strength versus the configured benchmark, and beta/alpha -- and write the result as a run-workspace calculation artifact with an evidence entry, audit event, and completeness trace. Use when a stock's price behavior needs quantifying for research, before scoring or thesis work. Never interprets the numbers.
model: haiku
---

# Security Technicals

Deterministic, no-judgment calculation layer. Reads stored prices, computes,
writes an artifact. It does **not** fetch from the network, does not decide
anything, and does not write the knowledge base.

Run `uv run python .claude/skills/security-technicals/scripts/security_technicals_cli.py`:

- `--ticker TICKER [TICKER ...]` — one or more pipeline ticker symbols.
- `--benchmark SYMBOL` — comparison series for relative strength and
  beta/alpha. Defaults to `config.DEFAULT_BENCHMARK_SYMBOL` (`XEQT.TO`).
- `--output PATH` — write the artifact here instead of into a run
  (single-ticker runs only).
- `--no-run` / `--run-id ID` — see below.
- `--no-trace` — skip the completeness trace.
- `--pretty` — pretty-print the JSON digest.

## Workflow

1. Run the CLI for the ticker(s) in question. A run workspace is opened
   automatically — no `run create` step first.
2. Read the **stdout digest**. It carries the computed values, the price-series
   coverage, and an explicit `gaps` list. This is what you report from.
3. **Do not read the artifact JSON.** The digest already contains every number
   in it; the artifact exists for downstream programmatic consumers and for the
   audit trail.
4. If a metric is `null`, say so and say why — the digest's `gaps` and the
   trace's `not_applicable` list name the reason (usually insufficient
   history). Do not substitute a shorter window and present it as the
   requested one; the module already refuses to do that.

## When to invoke

- A stock's price behavior needs quantifying: "how volatile is X", "what is
  X's drawdown", "is X outperforming the benchmark", "what is X's beta".
- As a preparation step before thesis or scoring work that cites price
  behavior.
- Do **not** invoke it to fetch fresh prices — it reads only what
  `historical_records` already holds. If prices are stale, run the pipeline or
  `investment-analyst-resources` first.

## Run workspace: default-on

Every invocation attaches to a run workspace, creating one if it does not
exist. The artifact lands in that run's **`calculations/`** directory — not
`evidence/` — because it is derived arithmetic over data the run already has,
not a fact obtained from outside. It is registered in the evidence registry as
`derived_calculation` so its provenance and content hash are tracked, an
audit event is appended, and the completeness trace is written to
`logs/SkillTrace.txt`/`.jsonl`.

`context_manifest.yaml` lists the artifact under `calculation_paths`, which is
how a downstream stage finds it without being told the filename.

- `--run-id ID` — name the run explicitly; attaches if it exists, creates it
  under that name if not. Passing the same ID to several invocations groups
  them into one run.
- `--no-run` — opt out; the artifact goes to `--output` (or `exports/`). The
  opt-out is recorded in the trace's `workspace.skip_reason`, so it stays a
  greppable fact rather than a silent gap.
- A non-default `--db-path` also skips attachment (the existing test/debug
  convention), so the test suite never populates the real `workspace/runs/`.

See `docs/architecture/run_workspace.md`.

## What it computes

| Metric | Notes |
|---|---|
| `moving_averages` | Latest close plus SMA-50 and SMA-200. A window longer than the available history is `null`, never a shorter average relabelled. |
| `max_drawdown` | Worst peak-to-trough decline over the full stored series. |
| `volatility` | Daily and annualized, `ddof=0`, `x sqrt(252)`. |
| `relative_strength` | Ticker's trailing 365-day return minus the benchmark's over the same window. Either leg is `null` when its series does not reach back that far. |
| `beta_alpha` | Beta, alpha, tracking error, information ratio versus the benchmark. `null` below 20 overlapping trading days. |

Full field-by-field contract, including the convention parity statement, is in
[technicals-contract.md](references/technicals-contract.md).

## Guardrails

- **Computes and reports; never interprets.** "Beta is 1.8" is this skill's
  output. "This is a risky position" is not — that is the analyst's judgment,
  made against the rubric, and stating it here would launder a calculation
  into a recommendation.
- **Never recommends an action.** No buy/sell/hold/trim, no sizing, no "you
  should".
- **Never writes the knowledge base.** The artifact goes to the run workspace;
  `kb-intake` owns every KB write.
- **Never fetches.** No network. If the stored history is short, that is a gap
  to report, not a reason to reach for yfinance.
- **A `null` is a real answer.** Report it as insufficient history. Do not
  silently widen or narrow a window to produce a number.

## Dependencies

- `read-security-price-history` — the read-only DuckDB boundary (dependency-only
  skill; do not invoke it directly).
- `src/security_technicals.py` — the pure math, which is shared with any
  non-skill consumer and so deliberately lives in `src/`, not here. See
  `docs/plans/implementation/phase-2/design-decisions.md`.
