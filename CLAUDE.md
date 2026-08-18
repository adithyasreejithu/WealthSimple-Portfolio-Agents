# Claude Code Instructions

This file is the canonical repo-wide context for implementation work in this repository.

## Project Structure & Module Organization

This repository is a Python data pipeline for Wealthsimple exports, statements, and email extraction.

- `src/` contains the application code, including pipeline orchestration (`app.py`), extraction logic (`email_extractor.py`, `statement_extractor.py`, `yfinance_extractor.py`, `earnings_dividends_extractor.py`, `financial_snapshots_extractor.py`), storage (`database.py`, `database_command.py`), read-only analytics (`analytics.py`, `portfolio_metrics.py`, `position_engine.py`, `market_data.py`, `security_technicals.py`, `holdings_reconciler.py`), Knowledge-Base wiki helpers (`kb_pages.py`), ticker identity/enrichment (`ticker_mapping.py`, `ticker_pipeline.py`, `manual_overrides.py`), and utilities (`config.py`, `data_sorter.py`, `staging.py`, `system_logger.py`, `skill_trace.py`).
- `tests/` contains `unittest` test cases that mirror the main modules.
- `docs/` contains design notes, handover material, and success criteria.
- `src/workspace/` is the run-workspace package: one directory per investment-research request (`workspace/runs/<run_id>/`) holding that request's inputs, evidence, calculations, agent outputs, and audit log. Exposed as `python src/app.py run <subcommand>`. See `docs/architecture/run_workspace.md`.
- `dashboard/api/` is the read-only FastAPI backend that serves already-computed pipeline output (DuckDB reads via `src/analytics.py`) to the dashboard frontend. See `docs/architecture/dashboard_api.md`.
- `dashboard/web/` is the Next.js + shadcn/ui dashboard frontend (App Router). Server components fetch the API; charts/tables/price-explorer are client components. It only formats API values and does presentation-layer derivations (`src/lib/derive.ts`) — never portfolio math. See `docs/architecture/dashboard_api.md` (Frontend section) and `dashboard/web/README.md`.
- `requirements.txt` lists runtime dependencies.

## Agent and Skill Documentation

Claude Code agents live in `.claude/agents/*.md` and skills live in `.claude/skills/*/SKILL.md`. When adding or updating an agent, also keep its human-readable documentation under `docs/agents/<agent>/`.

- Define each agent as a markdown file with YAML front matter (`name`, `description`, `model`, `color`, and an optional least-privilege `tools` array) plus a system-prompt body.
- Keep reusable workflows as skills in `.claude/skills/`, each a directory with a `SKILL.md` and optional `scripts/` and `references/`.
- Put Python that is used only by one agent/skill beside that skill in its `scripts/` directory, not in `src/`. `src/` is for general pipeline code that is not owned by a single agent. For example, the classifier workflow modules live in `.claude/skills/classify-portfolio/scripts/`.
- Document each agent's purpose, runtime settings, skill dependencies, workflow, and guardrails under `docs/agents/<agent>/`, and keep it aligned with the actual agent config and skills.
- See `docs/architecture/claude_agent_skill_structure.md` for the full recommended layout.
- Agent and skill invocations (with per-subagent token usage and session separators) are logged by the `.claude/hooks/usage_tracker.py` hook to `logs/AgentSkillUsage.txt`, separate from the pipeline log `logs/SystemLogs.txt`. Skills whose output quality depends on input coverage additionally emit a completeness trace to `logs/SkillTrace.txt`/`.jsonl` via the shared `src/skill_trace.py` writer — one format across skills, grading only what was *obtainable* for the subject so a not-applicable field is never reported as a gap. That includes data-collecting skills (`investment-analyst-resources`, `market-analyst-resources`) and calculation/lookup skills whose inputs may be thin (`security-technicals`, where a ticker with sixty days of history simply cannot have an SMA-200; `security-status`, shared read-only status resolution for the investment-analyst track below). See `docs/architecture/usage_tracking.md`.

**Skills import `src/`; `src/` never imports `.claude/`.** Python owned by exactly one skill lives in that skill's `scripts/`, but a skill may *wrap* `src/` code with multiple consumers rather than vendoring it — `security-technicals` supplies the CLI, run attachment, evidence/audit registration, and trace, while the math it computes stays in `src/security_technicals.py` because the worksheet builder and dashboard may also need it. Moving multi-consumer code into a skill would force a `src/` module to import from `.claude/`, which this rule exists to prevent. Skills come in two shapes: agent-invoked (Claude follows the `SKILL.md` workflow) and dependency-only (`read-portfolio-classification-data`, `fetch-yfinance-classification-data`, `read-security-price-history` — never invoked directly, imported by another skill's scripts). See `docs/architecture/claude_agent_skill_structure.md`.

### Research Knowledge Base

`Knowledge-Base/` holds two zones: `ref/*.yaml` (the approved classifier
reference, unchanged by this section) and the research wiki (everything
else — portfolio, stocks, theses, earnings, dividends, market-research,
sources, taxonomy, templates, logs). Every wiki page carries YAML front
matter per `Knowledge-Base/templates/front-matter-spec.md` — pages without
it are invisible to `kb-search`. Generated pages (`portfolio/holdings.md`,
`portfolio/portfolio-overview.md`, and every `index.md` except the
hand-curated `theses/index.md`, `logs/index.md`, `taxonomy/index.md`) are
rebuilt by their owning skill, never hand-edited. `kb-intake`, the only
KB-writing agent, never edits `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md`.
(A separate `kb-discovery` read-only lookup agent existed at one point but
was archived and never restored -- `.claude/agents/_archive/kb-discovery.md`
-- so a pure lookup with no write intent now goes straight to the `kb-search`
skill rather than a dedicated subagent.) See
`docs/architecture/knowledge_base.md` for the full layout.

### Decision-support scoring

`Knowledge-Base/taxonomy/decision-rubric.yml` is the hand-curated scoring
rubric that turns fetched research evidence into a
Buy/Sell/Hold/Trim/Add/Watchlist/Avoid recommendation. **Every tunable number
lives there** — hard-gate thresholds, dimension weights, 1/3/5 anchor cutoffs,
verdict bands, confidence rules. Edit it only through the
`author-decision-rubric` skill (which validates it, checks the weights still
sum to 1.0, and requires a version bump); the `stock-analyst` agent and every
other KB agent read it but never write it. The rubric never redefines the
action/confidence/horizon enums — those stay in `taxonomy/decision-framework.yml`.

The workflow is two agents by model: `stock-data-prep` (`haiku`) runs the
mechanical steps (fetch data, build the scoring worksheet), and `stock-analyst`
(`opus`, Opus 4.8) applies the rubric — scoring each criterion against cited
evidence — because that judgment is the one place the strongest model is worth
it. `fetch-stock-research-data` is the first registered research **source**
(`yfinance`); more sources are added via the rubric's `sources:` registry
without changing the recommendation contract. The analyst emits a validated
artifact under `exports/stock-recommendations/`; `kb-intake` commits it to
`stocks/TICKER.md` (Decision/Confidence/Time Horizon + a Decision History row,
never Portfolio Status). The LLM never predicts markets — it applies the
owner's written rubric and cites everything. The rubric is **two-track**:
`equity_only` company gates/dimensions vs `etf_only` fund dimensions, so a fund
is scored on fund-appropriate criteria (expense ratio, concentration) instead of
being penalized for lacking a balance sheet. For a **portfolio-wide run**, fan
out one `stock-data-prep`/`stock-analyst` invocation **per ticker** in parallel
(ETF and stock batches concurrently) — invoke the **ETF batch's analysts with a
`model: sonnet` override** (fund-track judgment is simpler; the validator
recomputes the math regardless). Never loop N tickers inside one agent — with
one exception: the commit step is **a single `kb-intake` invocation** that loops
the deterministic `ingest_recommendation.py` over all artifacts sequentially
(the wiki helpers are not concurrency-safe, and per-ticker agent spawns just
re-pay the agent context N times). Agents never Read the research/worksheet
JSONs — the scripts print the summaries they need. See
`docs/architecture/decision_support_flow.md`.

### Investment Analyst track

A second, separate decision-support path from the legacy `stock-analyst`
benchmark above — its own vocabulary, its own storage, never mixed. Where
the legacy path scores `decision-rubric.yml` and writes
`Buy/Sell/Hold/Trim/Add/Watchlist/Avoid` into `Knowledge-Base/stocks/TICKER.md`,
this track writes a `DecisionProposal` (`Buy/Hold/Trim/Sell/Add` for an owned
security, `Buy/Watch/Wait/Pass` for a not-owned one — no `Watchlist`/`Avoid`)
into a run-scoped `workspace/runs/<run_id>/final/*.json`, never into the
knowledge base. Three agents, two of them doing real judgment:

- **`investment-analyst`** (`opus`) reads a run's investment worksheet and
  produces a validated `investment-thesis.v1` — fundamental rating,
  valuation stance, thesis direction/confidence, key claims, conditions, all
  cited to registered evidence. It never proposes a portfolio action.
- **`investment-portfolio-manager`** (`sonnet`) turns that thesis plus a
  deterministic policy worksheet (`src/workspace/policy_worksheet.py`
  against `Knowledge-Base/ref/policy_v1_1.yaml`'s `single_name_cap`/
  `group_allocation_target` limits and `src/analytics.py`'s exposure
  functions) into the `DecisionProposal`, with weight-based sizing and,
  for a live buy/sell action, advisory order-mechanics guidance (never an
  instruction to trade). When a failing weight/allocation check — not the
  thesis — is the only thing keeping an attractive thesis out of `Buy`/`Add`,
  its rationale must say so explicitly rather than letting the action alone
  imply the stock is unattractive; when the failing check is specifically
  `group_allocation_target`, it also flags a possible classifier-group
  mismatch (as an `uncertainties` entry, never a reclassification it performs
  itself) when the thesis's own description of the business doesn't seem to
  fit `primary_group`.
- **`investment-orchestrator`** (`haiku`) coordinates the other two for
  "analyze TICKER" requests: read-only preflight (ticker exists, price
  history depth, classification presence, status blockers), then dispatches
  `investment-analyst` and `investment-portfolio-manager` via `Task` in that
  fixed order, one run per ticker. It is one of two agents in this repo
  explicitly authorized to hold `Task` (with `kb-orchestrator`) — see
  `docs/architecture/claude_agent_skill_structure.md`. It also logs its own
  preflight/dispatch/report steps into the run's `audit_log.jsonl` via
  `run log-event`, using six fixed event names
  (`preflight_passed`/`analyst_dispatched`/`analyst_completed`/
  `pm_dispatched`/`pm_completed`/`orchestration_completed`) so that history
  reads the same way across every run.

Both judgment agents share the read-only `security-status` skill to resolve
a ticker's owned/wishlist/avoid/retired status before drafting. See
`docs/architecture/run_workspace.md` for the run-workspace mechanics
(evidence registry, audit log, lifecycle states) and
`docs/agents/investment-analyst/`, `docs/agents/investment-orchestrator/`,
`docs/agents/investment-portfolio-manager/` for full per-agent documentation.

## Build, Test, and Development Commands

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency and Python version management.

**First time setup:**
```powershell
uv sync
```

**Running commands:**
- `uv run python -m unittest discover -s tests` runs the full test suite.
- `uv run python -m unittest tests.test_app` runs one test module.
- `uv run python src/app.py --help` shows the CLI entry points for the pipeline.
- `uv run uvicorn main:app --reload --port 8000 --app-dir dashboard/api` runs the dashboard API locally.

See `docs/reference/cli.md` for the complete CLI reference.

## Coding Style & Naming Conventions

Follow standard Python 3 style with 4-space indentation and snake_case for functions, variables, and filenames. Keep modules small and focused on one responsibility. Prefer explicit imports and type hints where they improve clarity. Test classes use `CamelCase` and test methods use descriptive `test_*` names.

## Testing Guidelines

The project uses the built-in `unittest` framework. Add tests alongside the module behavior they cover, and prefer deterministic fixtures over live service calls. When testing pipeline behavior, mock external dependencies such as network, email, and database operations.

## Documentation

`docs/reference/cli.md` is the canonical, complete guide to every user-facing
`python src/app.py <command>` — every command registered in `app.py`'s
`_print_root_help`/`main()` dispatch must have a section there, kept in sync
with its actual arguments and behavior. When a change adds, renames, or alters
the behavior of a CLI command (new flags, new subcommand, changed defaults,
changed output format), update `docs/reference/cli.md` in the same change,
not as a follow-up — a command missing from this file is a bug. Related
architecture docs (`docs/architecture/*.md`, `docs/agents/<agent>/*.md`) should
also be updated when the change affects the behavior they describe.

## Planning & Approved Plans

All implementation plans approved during the planning process must be stored in
`docs/plans/` with a descriptive filename (e.g., `docs/plans/position-engine.md`,
`docs/plans/holdings-reconciliation.md`). This keeps the rationale, design
decisions, and scope of major features retrievable for future reference and
architecture reviews. Plans are reference material and should not be updated
after implementation completes — they capture the approved approach at the time
of execution.

## Commit & Pull Request Guidelines

Recent commits are short, imperative, and task-focused, for example: `Add normalized activity export storage` and `Consolidate statement extraction`. Keep commit messages in that style.

Pull requests should include:

- a short summary of the behavior change,
- any setup or migration notes,
- test evidence (`python -m unittest discover -s tests` output or equivalent),
- screenshots only when the change affects UI or rendered output.

## Security & Configuration Tips

Do not commit secrets, mailbox credentials, or local database files. Keep environment-specific values in `.env` or local config overrides, and verify file moves carefully because the pipeline renames and archives downloaded statements.
