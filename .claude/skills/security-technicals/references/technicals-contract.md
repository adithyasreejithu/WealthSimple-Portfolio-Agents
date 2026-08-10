# Technicals contract

What `security_technicals_cli.py` computes, what it writes, and the
conventions every number follows.

## Boundary

| Rule | Why |
| --- | --- |
| No network, ever | This skill reads `historical_records` only. Stale prices are a gap to report, not a reason to fetch. `investment-analyst-resources` and the pipeline own refreshing. |
| No judgment in the output | Every field is a number, a `null`, or a coverage fact. No ratings, no adjectives, no recommendation. |
| Artifact goes to `calculations/`, not `evidence/` | It is arithmetic derived from data the run already holds, not a fact obtained from outside. `evidence/` means provenance-from-elsewhere; conflating the two would make an evidence registry that no longer answers "where did this come from". |
| `null` is a real answer | Insufficient history returns `null`, never a shorter window silently relabelled as the requested one. |

## Conventions

These match `src/portfolio_metrics.py`'s portfolio-level equivalents so the
per-security and portfolio numbers are comparable:

- **`ddof=0`** (population standard deviation) for volatility.
- **Annualization** by `math.sqrt(config.ANNUALIZATION_PERIODS)` (252).
- **Risk-free rate** from `config.DEFAULT_RISK_FREE_RATE`.
- **`close`, not `adjusted_close`** — matching the benchmark convention
  already documented in `docs/architecture/dashboard_api.md` ("raw CAD
  closes"), rather than introducing a second, different basis.

`beta_alpha` delegates to `portfolio_metrics.calculate_benchmark_stats` rather
than reimplementing beta. That function has a known `ddof` asymmetry
(`.cov()` defaults to `ddof=1`, divided by `.var(ddof=0)`), which skews beta by
`n/(n-1)`. It is **not** corrected here — see
`docs/plans/implementation/phase-2/HANDOFF.md`. At the 20-day minimum overlap
the effect is roughly 5%.

## Artifact schema

Written to `runs/<run_id>/calculations/security-technicals-<TICKER>-<date>.json`
(or `--output`).

```json
{
  "schema": "security-technicals.v1",
  "ticker": "PLTR",
  "as_of": "2026-08-10",
  "benchmark": "XEQT.TO",
  "prices": {
    "count": 504,
    "start": "2024-08-12",
    "end": "2026-08-08",
    "latest_close": 187.42
  },
  "benchmark_prices": { "count": 504, "start": "...", "end": "...", "latest_close": 41.18 },
  "technicals": {
    "moving_averages": { "latest_close": 187.42, "sma_50d": 172.10, "sma_200d": 149.83 },
    "max_drawdown": { "max_drawdown": -0.3142 },
    "volatility": { "volatility": 0.5218, "daily_volatility": 0.0329 },
    "relative_strength": {
      "excess_return": 0.4471, "ticker_return": 0.6183, "benchmark_return": 0.1712
    },
    "beta_alpha": {
      "overlap_days": 501, "beta": 1.8402, "alpha": 0.3915,
      "tracking_error": 0.4820, "information_ratio": 0.9276
    }
  },
  "gaps": [],
  "trace": { "...": "skill_trace.Trace.to_dict()" }
}
```

`beta_alpha` is `null` in full (not a dict of nulls) below the minimum
overlap, because `calculate_benchmark_stats` returns `None` rather than
partial statistics it cannot stand behind.

## Digest (stdout)

The same content as the artifact. This artifact is small enough — a few
hundred bytes of numbers, no raw series — that the digest and the artifact
carry the same fields, unlike `investment-analyst-resources` where the bundle
holds full OHLCV and the digest must summarize. Agents read the digest and
never open the artifact.

## Trace domains

Three domains, graded per `src/skill_trace.py`'s three-way split. Getting the
`missing` / `not_applicable` split right is the point:

| Domain | `ok` | `missing` | `not_applicable` |
| --- | --- | --- | --- |
| `prices` | `history` when rows exist | `history` when the ticker resolves but has no rows | — |
| `benchmark` | `history` when rows exist | `history` when the configured benchmark has no stored rows (it should — `market_data.ensure_benchmark_history` populates it) | — |
| `technicals` | each metric that computed | — | each metric that could not compute for lack of history |

**Insufficient history is `not_applicable`, never `missing`.** A 60-day-old
holding genuinely cannot have an SMA-200; grading that as a gap would report a
healthy run as incomplete and bury real gaps in noise. `skill_trace.py`'s
module docstring documents this exact failure mode: only `ok + missing` forms
the completeness denominator.

A ticker that does not resolve at all produces a `resolved: false` digest with
one gap and no trace domains — nothing was obtainable, so nothing is graded.
