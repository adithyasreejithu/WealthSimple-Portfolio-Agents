# Market Analyst Resources — resource contract

## Storage

`market.duckdb` (path: `Data/market.duckdb`, alongside the pipeline's own
`Data/PRD_WealthSimple.duckdb`, but a fully separate file — see SKILL.md's
"Why a separate database").

```sql
CREATE TABLE macro_observations (
    series_id         VARCHAR   NOT NULL,   -- raw, permanent identity
    obs_date          DATE      NOT NULL,
    value             DOUBLE,               -- NULL when suppressed
    status            VARCHAR   NOT NULL,   -- 'ok' | 'suppressed'
    source_id         VARCHAR   NOT NULL,
    units             VARCHAR   NOT NULL,   -- as-written, documents the RAW value
    first_seen_at     TIMESTAMP NOT NULL,
    last_confirmed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (series_id, obs_date, first_seen_at)
);

CREATE VIEW v_macro_current AS  -- max(first_seen_at) per (series_id, obs_date)
CREATE TABLE macro_runs ( run_id, run_date, registry_version, status, bundle_path, indicator_count, created_at )
```

`units` documents the **raw stored value's** unit (e.g. `usd` for an ETF
close), not the transformed output's unit — `pct_change`/`yoy_pct`/
`mom_pct`/`annualized_3m` always emit percent by construction, implicit from
the transform name itself.

## Insert-on-change write rule

On refetch of `(series_id, obs_date)`:

| Stored value vs refetched value | Action |
|---|---|
| Same | `UPDATE last_confirmed_at`, no new row |
| Different | `INSERT` a new row (new `first_seen_at`) |
| Never seen | `INSERT` (`action: "new"`) |

`first_seen_at` is **when this system observed the value**, not a release
date. Every read (`get_history`, `get_latest`, digest building) goes through
`v_macro_current`; only revision detection and `delta_vs_last_report`
reconstruct a past state, via `get_history_as_of(as_of=<last run's
created_at>)`.

## Registry (`Knowledge-Base/taxonomy/market-indicators.yml`)

### `indicators` (leaves)

| Field | Notes |
|---|---|
| `id` | Permanent, `^[A-Z][A-Z0-9_]*$`, globally unique across `indicators` and `derived` |
| `output_id` | Defaults to `id`; set explicitly when the transform changes the concept (e.g. a raw close vs its 1-month return) |
| `domain` | One of the thirteen domains (§ below) |
| `kind` | `statistical` or `priced` |
| `source` / `series_ref` | Must name a declared `sources:` entry; `<TBD>` on either marks the indicator unconfigured (skipped, not an error) |
| `transform` | One of the unary transforms below, optionally windowed: `"zscore(252)"` |
| `cadence` | `daily` \| `weekly` \| `monthly` \| `quarterly` \| `annual` |
| `publication_lag_days` | Owner-authored estimate of source lag after the period end |
| `backfill_years` | How far back the *initial* fetch should reach |
| `units` | Raw value's unit, for the owner's own reference |
| `value_field` (priced only) | `close` \| `level` |
| `scale` (priced only) | Multiplier applied to the raw source value before storage (e.g. `0.1` for a yield quoted x10) |

### `derived` (composites, depth capped at 1)

`op: spread \| ratio`, `inputs: [A, B]` — both must resolve to leaf
`indicators` ids. By convention **A is the anchor/denser leg**: its latest
date sets the comparison date, and B is carried forward (LOCF) onto it,
bounded by `max_carry_days`. `align: locf` is the only supported alignment
in v1.

### `events`

Hand-maintained: `{date, label, forces_due: [domain, ...]}`. An event forces
its listed domains due the day after — see the freshness gate.

### The thirteen domains

`growth` · `inflation` · `labour` · `policy` · `rates` · `credit` ·
`breadth` · `volatility` · `sectors` · `factors` · `fx_commodities` ·
`positioning` · `events`

## Transforms (`transforms.py`)

Offsets are counted in **observations already stored**, not calendar days —
null (suppressed) observations are dropped before indexing.

| Transform | Meaning |
|---|---|
| `none` | Latest raw value, unchanged |
| `yoy_pct` | % change vs. one cadence-implied year back (12 obs for monthly, 4 for quarterly, 252 for daily, ...) |
| `mom_pct` | % change vs. 1 observation back |
| `annualized_3m` | ~3-month % change, annualized by the cadence's periods-per-year |
| `pct_change(w)` | % change vs. `w` observations back |
| `zscore(w)` | Latest value's z-score against the trailing `w` observations |
| `percentile_rank(w)` | Latest value's percentile rank (0-100) within the trailing `w` observations |

## Freshness gate (`market_freshness_gate.py`)

```
next_expected = last_obs_date + cadence_period + publication_lag_days
due           = today >= next_expected
```

- **Backfill** is a gate verdict (`due_reason: "backfill"`), not a
  first-run flag: triggered when `coverage_start` is missing or falls short
  of a target anchored on the **registry's `updated` date** (fixed), not on
  "today" — anchoring on "today" would make the requirement creep backward
  forever and re-trigger a satisfied backfill years later.
- **Priced daily** indicators use a business-day boundary
  (`_last_business_day(today)` minus a 2-business-day grace) instead of the
  generic formula, since "next day" isn't meaningful across a weekend, and
  the grace absorbs one-sided TSX/NYSE/CME/FX holidays.
- **`overdue`**: `today` is past `next_expected` by `GRACE_DAYS` (statistical)
  or `PRICED_OVERDUE_CALENDAR_DAYS` (priced) with no new observation —
  distinguishes a dead source adapter from "no release yet."
- **`cadence_mismatch`**: the observed median inter-observation gap exceeds
  twice the declared cadence — a source that quietly stopped publishing
  looks identical to "no change" without this.
- **Event-forced due**: an `events` entry whose `forces_due` includes this
  indicator's domain, dated yesterday, forces `due: true` regardless of the
  schedule.

## Refresh (the only writer)

Commits **per indicator** (mirrors the pipeline's
`ensure_benchmark_history` per-symbol transactions), so one indicator's
failure does not roll back others already written in the same call. Errors
are reported in the result list, never swallowed:

| `status` | Meaning |
|---|---|
| `ok` | Fetched and persisted (possibly zero new points) |
| `config_error` | The source rejected `series_ref` (e.g. HTTP 404) — a registry typo, not worth retrying automatically |
| `fetch_failed` | Transient network/5xx/timeout — worth retrying next run |
| `error` | Unexpected exception, surfaced rather than swallowed |

## Digest (stdout)

Partitioned by whether anything happened, so the digest stays useful at
~50 indicators instead of drowning in unchanged noise:

- `changed` — full detail for any indicator/derived with a new observation,
  a revision, or a `no_data`/`not_configured`/`overdue` status since the
  last report.
- `unchanged` — `{id: {value, as_of, domain}}` one-liners. **Not dropped**:
  several of the driving questions need current *levels*, not just
  direction.

Per changed entry:

```json
{
  "id": "...", "domain": "...", "value": 2.1, "as_of": "2026-08-01",
  "units": "percent", "cadence_class": "low_frequency",
  "new_observations_since_last_report": 1,
  "primary_delta": -0.5, "primary_delta_basis": "prior_obs",
  "delta_vs_prior_obs": -0.5, "delta_vs_last_report": 0.0,
  "revised_since_last_report": [],
  "next_expected": "2026-09-01", "status": "fetched"
}
```

`primary_delta` picks the signal over the noise by cadence: for
`low_frequency` (monthly/quarterly/annual) indicators, `delta_vs_prior_obs`
is primary (report-to-report is usually zero); for `high_frequency`
(daily/weekly) indicators, `delta_vs_last_report` is primary
(observation-to-observation is mostly noise). Falls back to whichever delta
is non-null if the primary choice is unavailable (e.g. the very first
report, with no prior run to diff against).

`completeness` trace: `{total, by_status: {fetched: N, stale: N, ...},
completeness_pct, graded, not_applicable}` — one of `fetched` / `stale` /
`overdue` / `no_data` / `not_configured` per indicator, `ok` /
`input_missing` / `stale_leg` per derived. `by_status` keeps the registry's
own vocabulary, which is more informative here than a three-way split alone.

## Completeness trace (log)

Every `read` also emits one line through the shared `src/skill_trace.py`
writer this skill and `investment-analyst-resources` both use — previously
this skill computed a completeness figure but never logged it anywhere. See
`docs/architecture/usage_tracking.md`.

Statuses map onto the shared **ok / missing / not-applicable** split:

| Status | Shared outcome |
|---|---|
| `fetched`, `ok` | ok |
| `stale`, `overdue`, `no_data`, `input_missing`, `stale_leg` | missing |
| `not_configured` | **not-applicable** |

`not_configured` is the important one: those are the registry's `<TBD>`
stubs, five domains' worth of indicators whose source has not been chosen
yet. They are not data a run failed to fetch, so counting them as gaps would
peg completeness below 100% for a healthy run indefinitely. They are excluded
from the denominator and reported separately.

Entries are grouped by registry domain, so a line reads
`n_a=labour[4],breadth[3]` rather than fifty indicator IDs. The subject is
the run date — this skill is portfolio-wide, so unlike its per-ticker sibling
there is no security to name.

## Run workspace (default-on)

Any invocation that produces a bundle (`--mode read`, or the default
refresh→read sequence) **attaches to a run workspace automatically** and the
trace's `workspace` field is appended to that run's `audit_log.jsonl`
(`docs/architecture/run_workspace.md`). `--mode gate`/`--mode refresh` never
attach.

Same decision order as the sibling `investment-analyst-resources` skill, via
the shared `workspace.run.ensure_run`:

| Condition | Outcome |
|---|---|
| `--mode gate` / `--mode refresh` | skipped -- nothing to register |
| `--no-run` | skipped -- explicit opt-out |
| `--db-path` overridden (the existing test/debug convention that already suppresses `record_run`) | skipped |
| otherwise | created or attached |

A skip is never silent: `workspace.skip_reason` lands in
`logs/SkillTrace.jsonl` either way, so an absent run is a positive,
greppable record rather than indistinguishable from "nothing ran." `--run-id`
names the run explicitly instead of letting one be generated; `--no-run` and
`--run-id` together is a usage error. `--trace-log-path` overrides the log
path for testing, with the `.jsonl` sidecar following it.

## Bundle (disk)

`exports/market-analyst-resources/<date>-market.json` — full stored history
per configured indicator, the digest, the raw gate results, and `entries`
(the flat `{id, domain, status}` table the completeness trace is computed
from; the digest's changed/unchanged split is the right shape for a reader
but loses the uniform status column). Consumed programmatically by the
market-researcher agent; never `Read` directly by an LLM (the digest is what
an agent should read).

## Scope boundary

This skill emits `CPI_CA_YOY = 2.1, primary_delta = -0.5`. It never emits
"inflation is cooling." No regime characterization, no thresholds, no
labels, no portfolio-exposure joins, no `Knowledge-Base/` writes beyond
reading the registry.
