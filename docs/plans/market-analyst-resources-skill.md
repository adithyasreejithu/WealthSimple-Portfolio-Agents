# Market Analyst Resources — Phase 2 macro/market data-layer skill

*Status: approved, not yet built — awaiting source identification. A
standalone phase of `investment-analyst-v2-plan.md`'s Phase 2, built as a
deterministic skill rather than the `build-market-landscape` skill that plan
sketches, per Adithya's direction on 2026-08-05. The
`Knowledge-Base/taxonomy/market-indicators.yml` registry ships with
free/no-key sources populated; the remaining `<TBD>` source slots are filled
in by Adithya before the first full run. Intended to be merged into the v2
plan once the `market-researcher` judgment agent is built on top of it.*

## Context

The v2 investment-analyst track has a per-ticker data layer
(`investment-analyst-resources`) but no market-level one. Phase 2 of the v2
plan (§8) calls for a Market Researcher producing a versioned macro/sector
landscape so the per-ticker analyst stops re-deriving macro context on every
run. This plan builds the **data layer beneath that agent** — pull, persist,
diff — not the agent itself.

The requirement is twelve questions the output must support: growth,
inflation, labour, central-bank policy, yields/credit, breadth, sector and
factor leadership, FX/commodities, what changed since last report, portfolio
impact, upcoming events, and what is missing or stale.

**Two findings shaped the design.**

*First:* `investment-analyst-resources` is DB-first because the database
already held thirteen schema versions of ticker data. For macro it holds
nothing — the only market-level series anywhere are `USDCAD=X`, `XEQT.TO`,
and `VFV.TO` in `historical_records`. No rates, inflation, breadth, credit,
volatility, or commodity data exists in this repo.

*Second:* **nine of the twelve questions are first-derivative questions** —
"is inflation moving *higher or lower*", "*what changed* since the last
report". None are answerable from a snapshot; each needs a prior observation
to difference against. So this skill cannot fetch and discard. It must own a
macro history store and backfill it.

**Confirmed decisions:** skills pull and agents analyze · separate
`market.duckdb` so `src/` is untouched · insert-on-change append-only
observations · raw storage with read-time transforms · two-tier registry ·
publication-lag freshness · hand-maintained event calendar · backfill as a
gate verdict · no portfolio-exposure linkage in the skill.

---

## 1. Storage — a separate database

### Why not the main DB

Two audits rejected the main database, for different reasons.

**A containment audit rejected reusing `tickers`/`historical_records`.**
Putting ~40 non-owned "market proxy" rows there — the
`_ensure_benchmark_ticker` precedent — is unsafe, and that precedent's
safety is *incidental, not enforced*: there is no flag column and no
exclusion list, and the existing rows stay out of portfolio output only
because they have no transactions, no verified provider mapping, and history
clamped to portfolio dates. Proxies break all three.

| Risk | Location | Effect |
|---|---|---|
| Valuation date axis | `src/analytics.py:493-507` | `value_dates` unions `record_date FROM historical_records` **unfiltered**. `CL=F`/`^VIX` trade on days holdings do not, injecting phantom points into portfolio value and the dashboard trend chart |
| Symbol shadowing | `src/analytics.py:406-416` | `get_price_history` resolves bare symbol `ORDER BY ticker_id LIMIT 1`. `XLK`/`IEF`/`HYG`/`MTUM` are plausible future holdings |
| Silent holding corruption | `src/database_command.py:92-109` | A proxy `XLK` makes a genuine future purchase look already-known → skips enrichment → no provider mapping, no sector |
| Ingestion blocked | `src/data_sorter.py:240-267` | Duplicate symbol makes activities-CSV rows *"ambiguous across exchanges"* |
| Transaction mis-binding | `src/ticker_pipeline.py:113-175` | A BUY binds to the **proxy `ticker_id`**, inheriting its exchange and `security_type` |
| Recompute thrash | `src/position_engine.py:45-57` | `FINGERPRINT_SQL` counts `historical_records` rows; every proxy bar forces a position recompute |
| Hard write failure | `src/database_command.py:306-313` | `upload_security_history` **raises** on null/non-integral volume; `^VIX`/`DX-Y.NYB` routinely return NaN volume |

**A design pressure-test then rejected even adding isolated tables to the
main DB**, because the schema-version bump carries three landmines:

1. **It breaks two read-only skills on landing.**
   `db_resources.validate_database` (`db_resources.py:105`) and
   `read_classification_data.py:58` both hard-fail on version mismatch, and
   both open read-only so neither can self-migrate. Bumping to v14 breaks
   `investment-analyst-resources` and `classify-portfolio` until some write
   path runs.
2. **The migration ladder's tail must be rewritten.**
   `src/database.py:1165-1180` uses `DATABASE_SCHEMA_VERSION` as its literal
   target then `return False`s. Appending a `row[0] == 13` block means a v12
   database jumps straight to 14 **without creating the macro tables**. The
   repo documents this exact trap in
   `docs/plans/earnings-dividends-pipeline.md:217-249` and
   `docs/plans/financial-snapshots-pipeline.md:729-757`.
3. **`tests/test_database.py:48` asserts table-set equality**, so new tables
   must also land in `REQUIRED_TABLES` and `_deploy_schema`.

### The design

A skill-owned `market.duckdb`. All three landmines vanish, `src/` needs no
changes at all, and a market refresh no longer contends for the portfolio
DB's write lock — it can run concurrently with stock analysis rather than
serializing behind it.

Priced proxies do not need OHLCV. Nothing in the twelve questions needs
open/high/low — deltas, percentile ranks, spreads, ratios, and close-to-close
returns all work from the close. So priced and statistical series share one
table.

```sql
CREATE TABLE macro_observations (
    series_id         VARCHAR   NOT NULL,   -- raw, permanent identity
    obs_date          DATE      NOT NULL,
    value             DOUBLE,               -- NULL when suppressed
    status            VARCHAR   NOT NULL,   -- 'ok' | 'suppressed'
    source_id         VARCHAR   NOT NULL,
    units             VARCHAR   NOT NULL,   -- as-written, so a registry
                                            -- units change cannot silently
                                            -- reinterpret stored rows
    first_seen_at     TIMESTAMP NOT NULL,   -- when THIS VALUE was first seen
    last_confirmed_at TIMESTAMP NOT NULL,   -- bumped on unchanged refetch
    PRIMARY KEY (series_id, obs_date, first_seen_at)
);

CREATE VIEW v_macro_current AS ...            -- max(first_seen_at) per
                                              -- (series_id, obs_date)

CREATE SEQUENCE macro_run_id_sequence START 1;
CREATE TABLE macro_runs (
    run_id           BIGINT PRIMARY KEY DEFAULT nextval('macro_run_id_sequence'),
    run_date         DATE      NOT NULL,
    registry_version INTEGER   NOT NULL,
    status           VARCHAR   NOT NULL,
    bundle_path      VARCHAR,                -- advisory only, see below
    indicator_count  INTEGER,
    created_at       TIMESTAMP NOT NULL
);
```

No foreign keys — DuckDB's FK-target-update restriction has already forced
two soft links in the main schema (`src/database.py:671-675`, `780-783`).

**Revision handling.** Macro statistics get revised after publication (GDP
and payrolls especially), and a plain `(series_id, obs_date)` key would
silently overwrite the original — making a revision indistinguishable from
real economic change. So: on refetch, unchanged value → `UPDATE
last_confirmed_at`, no new row; changed value → `INSERT`. Storage stays
about one row per observation plus one per genuine revision. Everything
downstream reads `v_macro_current` and is exactly as simple as an overwrite
design would have been. "Revisions since last report" becomes rows whose
`first_seen_at` exceeds the last run and which have an earlier sibling.

`value` is nullable because StatCan suppresses observations for
confidentiality; `NOT NULL` would force either skipping the row (gap
invisible) or inventing a number.

The contract must state that `first_seen_at` is **when this system observed
the value**, not a release date — an agent will otherwise reason about it as
one.

**`macro_runs` rules.** Written only by a *successful* `read` that emitted a
bundle — never by `refresh`, never when `--db-path` is overridden, so a
debugging run cannot silently reset the diff baseline. `registry_version` is
recorded so indicators added later report `new_to_registry` rather than a
bare `delta: null`. `bundle_path` is advisory: two runs on the same day
resolve to the same filename.

---

## 2. The registry

`Knowledge-Base/taxonomy/market-indicators.yml`, hand-curated, committed
alongside `decision-rubric.yml` — the same kind of object: owner-authored
configuration read by agents and skills, never written by them.
`Knowledge-Base/` is git-whitelisted, so the registry is versioned, unlike
`exports/`. `taxonomy/index.md` must gain a row for it or `kb-search`'s
index validation will flag the file.

### Raw storage, read-time transforms

`series_id` is the **raw, as-published** series and is the permanent storage
key; `output_id` is the metric the agent sees. Storing transformed values
under a name that claims to be the transform is ambiguous — if a source
publishes a CPI *index level*, `delta_vs_prior_obs` silently becomes a
delta-of-a-YoY.

Persisting raw means changing a transform never requires a backfill, and
`macro_observations` stays a faithful record of the source — which is
exactly what revision tracking needs. **`series_id`s are permanent;
renaming one orphans its history.**

### Two tiers

`spread(A,B)` and `ratio(A,B)` are not transforms — they have no
`source`/`series_ref`, which is the signal that they are a different entity.

```yaml
version: 1
updated: 2026-08-05

sources:
  boc_valet: { adapter: boc_valet }
  yfinance:  { adapter: yfinance }

indicators:                       # leaves: fetched
  - id: CPI_CA_ALL_ITEMS_INDEX
    output_id: CPI_CA_YOY
    domain: inflation
    kind: statistical             # statistical | priced
    source: <TBD>
    series_ref: <TBD>
    transform: yoy_pct            # unary only
    cadence: monthly
    publication_lag_days: 18
    backfill_years: 5
    units: index

derived:                          # composites: computed, depth 1
  - id: CURVE_US_2s10s
    domain: rates
    op: spread
    inputs: [UST10Y, UST2Y]
    align: locf
    max_carry_days: 5
    units: percent

events:                           # hand-maintained, ~16 entries a year
  - date: 2026-09-16
    label: "BoC rate decision"
    forces_due: [policy, rates]
```

Evaluation order is "all leaves, then all derived" — ten lines, not a
topological sort. A derived may not reference another derived in v1.

**Unary transforms:** `none`, `yoy_pct`, `mom_pct`, `annualized_3m`,
`pct_change(w)`, `zscore(w)`, `percentile_rank(w)`.

### Validator rules

- `id` globally unique across `indicators` and `derived`, restricted charset.
- Every `derived.inputs[*]` resolves to a leaf `id`.
- Priced indicators declare `value_field` (`close` vs `adjusted_close` —
  `adjusted_close` is meaningless for a yield proxy) and `scale`. Without
  `scale`, mixing a Valet percent (4.25), a FRED index, and a `^TNX` quote
  in one spread is silently off by 100×.
- A `derived` with a missing input emits `null` + `status: input_missing`
  naming the leg, counted **once** as a derived failure — not twice as two
  fetch failures, which would make `completeness_pct` lie precisely when it
  matters.
- Each operand carries its own `basis_date`, so the agent can see when one
  leg of a spread is three weeks older than the other.

### Coverage on day one vs. still `<TBD>`

Pre-populated from Bank of Canada Valet (no key) and yfinance (already a
dependency), US-primary with Canada for policy/FX/curve/TSX sectors:

- **Covered:** `policy`, `rates`, `fx_commodities`, `volatility`,
  `sectors`, `factors`, `credit` (HY/IG ETF-ratio proxy), `events`.
- **`<TBD>` — needs a source Adithya picks:** `growth` (GDP, PMI),
  `inflation` (US CPI, core, breakevens), `labour` (payrolls, claims,
  unemployment), `breadth` (% above 200dma, advance/decline),
  `positioning` (put/call, flows, sentiment).

---

## 3. Domains

Thirteen domains covering the eleven scope areas. "Macro regime" is not a
domain — it is what the agent synthesizes from the first four.

`growth` · `inflation` · `labour` · `policy` · `rates` · `credit` ·
`breadth` · `volatility` · `sectors` · `factors` · `fx_commodities` ·
`positioning` · `events`

| Question | Domains |
|---|---|
| Economic growth improving or weakening? | `growth` |
| Inflation higher or lower? | `inflation` |
| Labour market strong or deteriorating? | `labour` |
| Central banks restrictive or supportive? | `policy`, `rates` |
| Yields and credit helping or hurting equities? | `rates`, `credit` |
| Market advance broad or concentrated? | `breadth` |
| Which sectors and factors lead? | `sectors`, `factors` |
| CAD/USD, oil, commodities? | `fx_commodities` |
| What changed since last report? | diff engine, all domains |
| Which portfolio exposures affected? | **agent**, via its own `analytics.py` calls |
| What major events approach? | `events` |
| What is missing, stale, uncertain? | completeness trace, all domains |

---

## 4. Freshness gate

Age-in-days is wrong for macro: a short threshold makes a monthly series
permanently stale, a long one never fires on release day.

```
next_expected = last_obs_date + cadence_period + publication_lag_days
due           = today >= next_expected
```

`publication_lag_days` is one owner-authored integer per indicator — about
90% of a release calendar for zero infrastructure. Plus:

- **Poll window with reset.** Fetch nothing before `next_expected`. Once
  due, poll at most once per run until a new `obs_date` appears, then reset.
- **Overdue flag.** `today > next_expected + grace_days` with no new
  observation. This distinguishes *a dead source adapter* from *no release
  yet* — a pure age-based gate cannot.
- **`observed_cadence_days`** — median inter-observation gap, with a
  `cadence_mismatch` flag when observed far exceeds declared. A source that
  quietly stopped publishing otherwise looks identical to "no change."
- **Event-forced due.** An `events` entry with `forces_due: [policy, rates]`
  marks those domains due the day after — the one place a real calendar
  beats a lag estimate.
- **Priced side** reuses `freshness_gate._last_business_day`
  (`freshness_gate.py:46-51`), but that is a weekday-only approximation and
  proxies span TSX/NYSE/CME/FX with different holidays. Widen the proxy
  boundary to 2 business days and document it.

**Backfill is a gate verdict, not a first-run flag.** The gate compares
`coverage_start` against `backfill_years` and emits `due: backfill`. "First
run" stops being a special case, and adding five indicators in month four is
handled automatically. Backfill commits **per indicator**, mirroring
`ensure_benchmark_history`'s per-symbol transactions
(`src/market_data.py:311-327`), so a failure at indicator 37 does not roll
back 36. `--only <domain>` and `--max-indicators N` slice the initial fill.

Errors are **reported, never swallowed** — the log-and-continue pattern at
`src/market_data.py:352-357` is wrong for a layer whose entire premise is
history.

---

## 5. Run modes

| Mode | Lock | What it does |
|---|---|---|
| `gate` | read-only | Per-indicator freshness verdict as JSON. No writes, no fetch |
| `refresh` | read-write | The only writer. Fetches due indicators, persists |
| `read` | read-only | Builds digest + bundle; records the run |

Same split as `investment-analyst-resources`, for the same DuckDB reason —
one read-write process or many read-only ones, never both. Reuse
`db_resources.connect_read_only` (`db_resources.py:73-107`) rather than
reimplementing it; its actionable lock message is the entire point.

---

## 6. Output — two tiers

- **Digest (stdout), partitioned by whether anything happened:**
  - `changed` — at least one new observation or revision since the last run.
    Full detail. Typically 5-15 of ~50 on a weekly cadence.
  - `unchanged` — `id → {value, as_of}` one-liners. **Not dropped**: nine of
    the twelve questions need *levels*, not just direction. "Is policy
    restrictive" is a level question.
- **Bundle (disk).** `exports/market-analyst-resources/<date>-market.json`,
  full series windows. Consumed programmatically, never `Read` by an LLM.

**`primary_delta`.** The two deltas invert by cadence: for monthly and
quarterly series `delta_vs_prior_obs` is the signal and `delta_vs_last_report`
is usually zero; for daily series (VIX, FX, sector ETFs) it is the reverse.
Emit both, and have the digest surface a `primary_delta` chosen mechanically
by cadence class with `primary_delta_basis` naming which was used.

```
{ id, value, as_of, cadence_class,
  new_observations_since_last_report, primary_delta, primary_delta_basis,
  delta_vs_prior_obs, delta_vs_last_report,
  revised_since_last_report: [...], next_expected, status }
```

**Size.** ~50 indicators at ~300 bytes is ~15-20KB — well over the sibling's
`DIGEST_SOFT_LIMIT_BYTES = 8192` (`investment_analyst_resources.py:53`), and
unlike that per-ticker limit this is one global blob. Controls: the
changed/unchanged partition (largest win), static fields like `source` and
`cadence` moved to the bundle, compact JSON unless `--pretty`, and a
`--domain` filter. **Warn at 8KB, fail the test at 12KB.**

**Completeness trace** answers "what is missing, stale, or uncertain": per
indicator, one of fetched / stale / overdue / fetch-failed / input-missing /
not-configured.

---

## 7. Source adapters

Named after the **source**, not the transport — `boc_valet`, `statcan_wds`,
`fred`, `yfinance`. BoC Valet, StatCan WDS, and FRED return structurally
different JSON; a generic `http_json` adapter only works by pushing JSONPath
into the YAML, where it cannot be tested or debugged and the owner ends up
debugging config.

Each adapter is one function `fetch(series_ref, start, end) -> [(date, value)]`
with its own parser and a recorded-response fixture test.

- **Error taxonomy**, since the completeness trace depends on it: 404 (bad
  `series_ref` → config error) vs 5xx/timeout (transient) vs 200-with-empty
  (no data yet, not a failure).
- **Units normalized at the adapter boundary**, with an assertion.
- **API keys from environment variables, never the YAML** — the registry is
  committed. FRED requires one.
- **HTTP via `curl_cffi.requests`** — already a hard runtime dependency
  (yfinance pulls it in, and `yfinance_extractor._build_session` already
  uses it at `yfinance_extractor.py:94-102`), with a requests-compatible
  API. `httpx` is dev-only in `pyproject.toml`; `requests` is absent. **No
  new dependency.** Timeout and retry policy must be set explicitly — there
  is none to inherit.

---

## 8. Scope boundary

The skill emits `CPI_CA_YOY = 2.1, primary_delta = -0.5`.
It never emits "inflation is cooling."

Non-goals: no regime characterization, no thresholds, no labels, no
portfolio-exposure joins, no `Knowledge-Base/` writes, no new `src/app.py`
command (so `docs/reference/cli.md` is untouched).

Naming note: `market-analyst-resources` supersedes the v2 plan's
`build-market-landscape` skill name (§8.5 step 1), as
`investment-analyst-resources` superseded `gather-investment-evidence`.

---

## 9. Files

**New — everything lives beside the skill**
- `docs/plans/market-analyst-resources-skill.md` — this plan
- `Knowledge-Base/taxonomy/market-indicators.yml` — the registry
- `.claude/skills/market-analyst-resources/SKILL.md`
- `.claude/skills/market-analyst-resources/references/market-resource-contract.md`
- `.claude/skills/market-analyst-resources/scripts/`
  - `market_analyst_resources.py` — CLI, mode dispatch, digest, bundle
  - `registry.py` — load + validate `market-indicators.yml`
  - `sources.py` — per-source adapters
  - `transforms.py` — unary transforms + derived ops
  - `macro_store.py` — schema init, `v_macro_current`, read/write
  - `market_freshness_gate.py` — per-indicator gate
- `tests/test_market_analyst_resources.py`

**Modified** — `Knowledge-Base/taxonomy/index.md` (one row for the registry).

**Read/reused, not modified**
- `.claude/skills/investment-analyst-resources/scripts/db_resources.py` —
  `connect_read_only` and its actionable lock message
- `.claude/skills/investment-analyst-resources/scripts/freshness_gate.py` —
  gate/refresh split and `_last_business_day`
- `src/yfinance_extractor.py` — `_build_session`'s `curl_cffi` pattern

**Not touched** — **all of `src/`**, `docs/reference/cli.md`, the main
database, v1 agents and skills.

---

## 10. Verification

1. `uv run python -m unittest tests.test_market_analyst_resources` —
   registry validation (id uniqueness, unresolvable `derived.inputs`,
   missing `scale`), transform math at boundaries, derived alignment with
   LOCF and `max_carry_days`, gate arithmetic including overdue and
   event-forced, insert-on-change vs unchanged-refetch, revision detection,
   `primary_delta` selection by cadence class, digest size ceiling, bundle
   shape. Fixture DB + recorded source fixtures, no live network.
2. `uv run python -m unittest discover -s tests` — nothing regressed. In
   particular `tests/test_database.py` still passes untouched, since the
   main schema is unchanged.
3. **Isolation proof** — after a full refresh, the main database's file
   mtime, `tickers` count, and `historical_records` count are unchanged, and
   portfolio value, the dashboard trend chart, and `get_price_history` for a
   held symbol are identical to before.
4. `--mode gate` writes nothing, including no `market.duckdb` row.
5. Backfill on a small registry subset; immediate re-run reports everything
   fresh and fetches nothing. Simulate a revised value and confirm a second
   row appears while `v_macro_current` returns only the newer one.
6. **Concurrency** — `--mode refresh` running against `market.duckdb` at the
   same time as `investment-analyst-resources --mode read` against the main
   DB: both succeed, proving the separate-file decision delivered.
7. A deliberately broken `series_ref` surfaces as `fetch-failed` in the
   trace, not a crash, and does not abort the other indicators.

---

## 11. Handoff — the market-researcher agent

Deferred to its own plan, built next. That agent consumes the bundle and
owns everything this skill deliberately refuses to do:

- Regime characterization and all narrative judgment.
- **Portfolio-exposure linkage** — "which of my exposures are affected" —
  via `src/analytics.py`'s existing `get_look_through_sector_exposure`,
  `get_currency_exposure`, and `get_sector_allocation`.
- Authoring the KB note under `Knowledge-Base/market-research/macro-notes/`,
  including page structure, versioning, and current-pointer design. It is a
  **new** agent, not the existing `kb-intake`.

Note this crosses the v2 plan's §11 guardrail ("no writes to
`Knowledge-Base/` from this track"), which was written to keep the benchmark
track deletable in one step. Macro context is genuinely shared between both
tracks in a way a per-ticker thesis is not, so the amendment is deliberate —
but it should be recorded in the v2 plan when the agent is built.
