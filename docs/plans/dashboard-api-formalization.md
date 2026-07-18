# Formalize the Dashboard API (Phase 3 — minimal hosted view)

## Context

`dashboard/api/main.py` is a working FastAPI prototype (manually curl-verified
against the real DuckDB) that wraps four `src/analytics.py` functions behind
read-only GET endpoints for the in-progress Next.js frontend (`dashboard/web/`,
off-limits here). Per the roadmap, Phase 3's backend is "a thin read-only API
that serves already-computed pipeline output — reads a pushed snapshot, never
runs ingestion itself." The prototype violates repo conventions: no tests, no
docs entry, deps not in `pyproject.toml` — and, discovered during review, **the
entire `dashboard/` tree is git-ignored** by `.gitignore`'s `*` catch-all, so
`main.py` isn't even tracked. This plan formalizes it: git tracking, deps,
hardening, tests, docs, and one new `/api/portfolio/report` endpoint (user
approved) that recomputes the full analytics report from DuckDB, cached on the
DB file's mtime, so every planned visual is unlocked and the dashboard is never
stale relative to the pipeline. The user explicitly wants **no live market
data** (prices stay pipeline-sourced) but the design must stay **open to
future website-initiated triggers** (ingestion runs, agent calls) — documented
as a designed-but-not-built extension point, matching roadmap Phase 6.

## Key facts established during review

- `database.get_shared_connection()` does `parent.mkdir()` + `duckdb.connect()`
  (read-write) — a missing `DATABASE_PATH` would be **silently created as an
  empty DB**. The API must fail fast instead.
- The empty-portfolio divide-by-zero in `portfolio_holdings` is **already
  guarded** (`if total_value > 0 else 0.0`) — needs a test, not a fix. All
  analytics weight helpers also guard (`return {}` when total <= 0).
- `src/config.py`: `DATABASE_PATH` (env `DB_PATH`), `ANALYTICS_EXPORT_FOLDER =
  exports/analytics`, `ANALYTICS_EXPORT_FILENAME = "portfolio-analytics.json"`.
  The CLI `analytics --export` (src/app.py `_write_analytics_export`) writes
  the full `portfolio_report()` JSON there — `{schema_version, generated_at,
  workflow, parameters, report:{summary, holdings, allocation, targets,
  performance, income, fees, activity, realized_gains, data_quality,
  unavailable_metrics}}`.
- Test conventions: stdlib `unittest`, `self.addCleanup`, descriptive names.
  User explicitly wants DB access **mocked** here (analytics is already tested
  against real temp DuckDBs in `tests/test_analytics.py`); the API tests cover
  the wrapper layer only. FastAPI `TestClient` needs `httpx` (not a dep yet).
- `main.py` calls `analytics.<fn>()` via module attribute, so tests patch the
  defining module (`patch("analytics.get_holdings")`) — visible regardless of
  how `main` was imported.

## Steps (in order)

### 1. Track `dashboard/api/` in git — `.gitignore`

After the `!tests/**` block (line 17), add:

```
!dashboard/
!dashboard/api/
!dashboard/api/**
```

(gitignore can't re-include children of an excluded dir, hence all three).
`dashboard/web/` stays ignored — it's built elsewhere; revisit when the
frontend is formalized. Trailing `__pycache__/` / `*.pyc*` rules still ignore
`dashboard/api/__pycache__/`.

### 2. Dependencies — `pyproject.toml` via uv (repo root)

```powershell
uv add fastapi "uvicorn[standard]"
uv add --group dev httpx
```

Then **delete `dashboard/api/requirements.txt`** — pyproject + uv.lock is the
single source of truth; the file was only a pointer and would drift.

### 3. Harden `dashboard/api/main.py`

Keep `to_jsonable`, `db_path`, and the four existing endpoints' response
shapes untouched (the frontend consumes them). Changes:

1. **Configurable CORS** — pure helper + env var, replacing the hardcoded list:
   ```python
   def cors_origins(raw: str | None) -> list[str]:
       if raw is None or not raw.strip():
           return ["http://localhost:3000"]
       return [o.strip() for o in raw.split(",") if o.strip()]
   ```
   Wire `allow_origins=cors_origins(os.getenv("DASHBOARD_CORS_ORIGINS"))`;
   keep `allow_methods=["GET"]`.
2. **Fail-fast startup** — FastAPI lifespan (defined before `app = FastAPI(...)`,
   passed as `lifespan=lifespan`): raise `RuntimeError` if
   `config.DATABASE_PATH` doesn't exist, message mentioning `DB_PATH` and that
   it refuses to start rather than silently create an empty DB. Lifespan (vs
   module-import check) keeps import side-effect-free so tests can construct
   `TestClient(app)` without the check (no `with` block) and exercise the
   check explicitly (`with TestClient(app)`).
3. **New `GET /api/portfolio/report`** — live recompute with an mtime-keyed
   cache (no manual `analytics --export` step, never stale vs the pipeline):
   - On request, stat `config.DATABASE_PATH`; if the cached report was built
     from the same mtime, serve it; otherwise call
     `analytics.portfolio_report(db_path(), ...)`, `to_jsonable` the result,
     cache `(mtime, payload)` in a module-level slot, and serve it. Single
     user, single process — a plain module-level cache is enough (no lock
     beyond a simple `threading.Lock` around rebuilds, since FastAPI's sync
     endpoints run in a threadpool).
   - **Benchmark section**: reuse the exact same benchmark-fetcher wiring the
     CLI's `run_analytics` (src/app.py) passes to `portfolio_report`, wrapped
     so a network failure degrades that section to unavailable (the report
     already has `unavailable_metrics` handling) instead of failing the
     request. This is the report's only network touch and runs at most once
     per pipeline run thanks to the cache.
   - Response wrapped as `{"generated_at": <iso now>, "database_mtime": <iso>,
     "report": {...}}` so the frontend can display freshness.
   - This design is deliberately trigger-friendly: any future ingestion
     trigger changes the DB file, which invalidates the cache automatically —
     no export re-run to coordinate.
4. Extend module docstring: `DASHBOARD_CORS_ORIGINS`, fail-fast behavior,
   report-endpoint refresh workflow.
5. **No pydantic response models** — plain dicts keep the layer thin;
   analytics dataclasses stay the single source of truth.

### 4. Tests — new `tests/test_dashboard_api.py`

Import strategy (survives `uv run python -m unittest discover -s tests`):

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (REPO_ROOT / "src", REPO_ROOT / "dashboard" / "api"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import analytics, config
import main as dashboard_api
from fastapi.testclient import TestClient
```

Patch targets: `analytics.get_*` (defining module), `config.DATABASE_PATH`,
`config.ANALYTICS_EXPORT_FOLDER`. Fixture helpers build **real**
`analytics.Holding` / `PortfolioSummary` / `CashSummary` frozen dataclasses
with `Decimal`/`date` values so `to_jsonable` is exercised on true shapes.
Endpoint tests use `TestClient(app)` without a context manager (skips
lifespan); startup tests use `with TestClient(app)`.

Test cases:

- **ToJsonableTests**: Decimal→float; date/datetime→ISO; nested dataclass→dict;
  plain values pass through.
- **CorsOriginsTests**: default on None/blank; comma parsing strips whitespace
  and empties; wiring test — GET /health with `Origin: http://localhost:3000`
  returns `access-control-allow-origin` header.
- **HealthEndpointTests**: existing DB (temp file) → `database_exists: true`;
  missing DB → `false` but still `status: ok` (health reports, doesn't fail).
- **SummaryEndpointTests**: normal (2 holdings, Decimals→floats, cash_source
  passthrough, holdings_count); empty portfolio → zeros, HTTP 200.
- **HoldingsEndpointTests**: weights correct (750/250 → 0.75/0.25) and sorted
  desc by market_value; empty portfolio → `[]`, no ZeroDivisionError.
- **AllocationEndpointTests**: `get_group_allocation` called with
  `(db_path_str, holdings)`, Decimals serialized; empty → `{}`.
- **TrendEndpointTests**: date/Decimal serialization; empty history → `[]`.
- **ReportEndpointTests**: patch `analytics.portfolio_report` + point
  `config.DATABASE_PATH` at a temp file — (a) first call invokes
  `portfolio_report` once and returns its payload jsonable-ized under
  `report` with `generated_at`/`database_mtime` present; (b) second call with
  unchanged mtime serves the cache (`portfolio_report` still called exactly
  once); (c) touching the temp DB file (bump mtime) triggers a recompute;
  (d) benchmark-fetcher failure degrades gracefully (endpoint still 200 —
  simulate by having the patched `portfolio_report` be reached with the
  wrapped fetcher; keep this test at the wrapper level: fetcher raising →
  report still built); (e) missing DB file → 404/503 with actionable detail
  (pick 503 here: DB absence at request time is an operational fault, and
  startup normally prevents it).
- **StartupTests**: missing DB → RuntimeError mentioning `DB_PATH`; existing
  DB → starts, /health returns 200.

### 5. Docs

**New `docs/architecture/dashboard_api.md`** (repo architecture-doc style: H1,
purpose paragraph cross-linking `../plans/goals/project-vision.md`,
`../plans/goals/development-roadmap.md`, `../reference/analytics.md`; ASCII
flow diagram; conceptual sections):

- Diagram: pipeline (`src/app.py`) → DuckDB + `exports/analytics/*.json` →
  FastAPI (`dashboard/api/main.py`, :8000) → Next.js (`dashboard/web/`, :3000).
- Design constraints: read-only; never runs ingestion or network (benchmark/
  yfinance math happens only at CLI export time); `src/analytics.py` is the
  single source of truth; fails fast rather than creating an empty DB.
- Endpoint table (all 6: path, backing function, empty/missing behavior).
- **Visual → report-path map** for the planned dashboard visuals, all under
  the endpoint's `report` key (identical shape to the CLI export's `report`):
  | Visuals | Report path |
  |---|---|
  | Group/sector/currency/geography pies | `allocation.by_group/.by_sector/.by_currency/.by_geography` |
  | Look-through sector | `allocation.look_through_sector` |
  | Treemap / top holdings | `allocation.by_ticker` + `holdings` |
  | Concentration gauge / HHI / top-N | `allocation.concentration` |
  | Target vs actual | `targets` |
  | Value trend | `performance.historical_values` |
  | Drawdown, Sharpe, Sortino, volatility, total-return tiles | `performance.adjusted_returns.*` |
  | XIRR | `performance.money_weighted` |
  | Alpha/beta/info-ratio | `performance.benchmark` |
  | Dividend bars / TTM / yields / growth | `income.*` |
  | Fee breakdown / drag | `fees.*` |
  | Turnover / holding period | `activity.*` |
  | Realized gains | `realized_gains` |
  | Data-quality dashboard | `data_quality` + `unavailable_metrics` |
- Rule for growth: new visuals read a `/api/portfolio/report` section first; a
  dedicated endpoint is added only when a visual needs parameters or
  interactive recomputation, and must wrap an existing `src/analytics.py`
  function.
- **Future: website-initiated actions (designed, not built)** — a reserved
  `POST /api/actions/*` namespace for Phase 6: e.g. `POST
  /api/actions/ingest` (run the pipeline) and `POST
  /api/actions/evaluate/{ticker}` (invoke the stock-data-prep → stock-analyst
  flow). Documented constraints for when they're built: they arrive **after**
  the single-user auth gate; they run as background jobs with a status
  endpoint (never block a request on ingestion or an agent run); CORS
  `allow_methods` widens from `["GET"]` to include `POST` only then; the GET
  surface stays read-only. The mtime-keyed report cache means any ingestion
  trigger automatically freshens every visual — no extra invalidation
  plumbing. No code for this now; the section exists so the frontend and API
  evolve toward the same shape.
- Configuration (`DB_PATH`, `DASHBOARD_CORS_ORIGINS`), run command, testing
  notes (mocked-DB rationale, lifespan-vs-plain TestClient).

**`docs/README.md`** — add link under Architecture references.

**`CLAUDE.md`** — "Project Structure & Module Organization": add two bullets —
`dashboard/api/` (read-only FastAPI backend serving already-computed pipeline
output, see the new doc) and `dashboard/web/` (Next.js frontend, managed
separately, consumes the API only). "Build, Test, and Development Commands":
add the uvicorn run line
`uv run uvicorn main:app --reload --port 8000 --app-dir dashboard/api`.

No `docs/reference/cli.md` change (not an `app.py` command); no
`docs/agents/<name>/` folder (that convention is for Claude agents, not app
subsystems).

## Explicitly out of scope

- `dashboard/web/` — untouched, stays git-ignored for now.
- Auth gate and deployment (later Phase 3 work items, noted in the doc).
- Granular per-section endpoints (/performance, /income, …) — deferred per
  the documented growth rule.
- `POST /api/actions/*` trigger endpoints (ingestion, agent calls) — Phase 6;
  documented as the designed extension point only.
- Live market prices in the API — prices remain pipeline-sourced by design.
- Any change to `src/analytics.py` / `src/database.py` behavior.

## Verification

```powershell
uv sync
git diff pyproject.toml                       # fastapi/uvicorn in deps, dev group httpx
uv run python -m unittest tests.test_dashboard_api -v
uv run python -m unittest discover -s tests   # full suite still green

# fail-fast: expect RuntimeError + exit
$env:DB_PATH = "C:\nonexistent\nope.duckdb"
uv run uvicorn main:app --app-dir dashboard/api --port 8000
Remove-Item Env:\DB_PATH

# happy path against real data
uv run uvicorn main:app --app-dir dashboard/api --port 8000   # background
# curl /health (database_exists true), all 4 original endpoints,
# /api/portfolio/report (full JSON incl. generated_at + database_mtime;
#   first hit slow ~seconds, second hit instant from cache);
# compare /report's report section against exports/analytics/
#   portfolio-analytics.json for sanity (same DB state -> same numbers).

git status --short dashboard   # api files now visible/added; web/ still ignored
```
