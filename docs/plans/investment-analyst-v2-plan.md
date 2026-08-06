# Investment Analyst v2 — Benchmark Track Plan

*Status: proposed — Phase 1 and Phase 2 are ready to build; later phases are
sketched for context only. Adapted from `investment-agent-system-plan.md`
(a Codex-oriented plan Adithya wrote separately) into this repo's Claude Code
conventions, per Adithya's direction on 2026-08-04.*

## 1. Purpose

Build a second, independent investment-research agent system in this repo
and run it side by side with the existing decision-support pipeline
(`stock-data-prep` → `stock-analyst`, scored against
`Knowledge-Base/taxonomy/decision-rubric.yml`). The goal is a genuine
architecture benchmark: same underlying holdings and market data, two
different reasoning approaches, compared on evidence traceability,
recommendation usefulness, and token cost.

Built one phase at a time, not as a single big-bang rebuild:

1. **Phase 1 (this plan, buildable now):** an independent Investment
   Analyst that gathers its own evidence and produces a bull/base/bear
   thesis — no rubric, no target weight, no portfolio judgment.
2. **Phase 2 (buildable next):** a Market Researcher that produces a
   versioned macro/sector landscape the Investment Analyst can reuse
   instead of re-deriving macro context per ticker.
3. **Later phases (sketched only, §9):** Portfolio Manager, Options
   Specialist, a conditional challenger pass, and a hardened multi-source
   evidence layer (SEC EDGAR, SEDAR+, Bank of Canada, StatCan, ...).

This is a decision-support system, not an autonomous trading system. It
must never place a trade, and it must never invent a financial value it
could not find evidence for — both carried over unchanged from the source
plan and from this repo's existing rubric agents.

## 2. What this is not

- Not a replacement for `stock-data-prep`/`stock-analyst` yet. Both systems
  run; nothing about v1 changes as part of this plan.
- Not a port of the Codex plan's Python-collector empire (SEC EDGAR, SEDAR+,
  BLS, Treasury, FINRA, OCC) in one shot. That's real, useful work, but it's
  §9.4 here — deferred until Phase 1/2 prove the reasoning approach is worth
  the investment.
- Not a rebuild of portfolio math. `src/position_engine.py`,
  `src/portfolio_metrics.py`, and `src/analytics.py` already compute
  average-cost positions, weights, sector/currency/AI exposure, ETF
  look-through exposure and overlap, drawdown, Sharpe/Sortino, and
  rebalance drift against `Knowledge-Base/portfolio/allocation-policy.md`'s
  targets. Nothing in the source plan's §22.3/§22.4 calculator lists is
  missing here — v2 calls this code, it does not reimplement it.

## 3. v1 vs v2, side by side

| | v1 (existing) | v2 (this plan) |
|---|---|---|
| Agents | `stock-data-prep` (haiku) → `stock-analyst` (opus) | `investment-data-prep` (haiku) → `investment-analyst` (opus) |
| Judgment method | Score each rubric gate/dimension 1-5 against cited evidence; deterministic script computes the weighted verdict | Qualitative bull/base/bear thesis against cited evidence; no scoring rubric, no computed verdict |
| Output | `proposed.action` (Buy/Sell/Hold/Trim/Add/Watchlist/Avoid) + confidence, mechanically derived | Fundamental rating (attractive/neutral/unattractive) + bull/base/bear expected return; **no portfolio action or target weight** |
| Portfolio fit | One rubric dimension (`portfolio_fit`) scored alongside everything else | Explicitly out of scope — deferred to the Phase-9.1 Portfolio Manager |
| Storage | `Knowledge-Base/stocks/TICKER.md` (shared wiki, `kb-intake` is sole writer) | `research-v2/` (new, fully separate tree — see §6) |
| Evidence source | `fetch-stock-research-data` (yfinance), ephemeral JSON, no persisted raw snapshot | Same yfinance pull to start, but the raw response is persisted with a timestamp and content hash under `research-v2/evidence/` (§7.3) |

The point of keeping these genuinely separate (not a shared rubric with a
different prompt) is that a benchmark comparing two prompts against the same
scoring code isn't testing much. v2 tests whether a narrative,
variant-perception-driven analyst produces better-calibrated, more useful
output than a rubric-scored one, on the same evidence.

## 4. Codex plan → this repo, translated

The source plan targets Codex (`.codex/agents/*.toml`, `AGENTS.md`,
`.agents/skills/`) and proposes a new standalone `investment-system/` repo.
This repo already runs on Claude Code, so the runtime layer maps directly
onto existing conventions instead of introducing a second tool:

| Codex plan concept | This repo's equivalent |
|---|---|
| `.codex/agents/*.toml` custom agents | `.claude/agents/*.md` (YAML front matter + system prompt), per `docs/architecture/claude_agent_skill_structure.md` |
| `AGENTS.md` | `CLAUDE.md` (already exists, already the durable repo-wide contract) |
| `.agents/skills/research-acquisition/` | `.claude/skills/<name>/SKILL.md` + `scripts/` |
| Root Codex orchestrator selecting a workflow mode | The top-level Claude Code session/user, invoking `investment-data-prep` then `investment-analyst` directly — no new Task-holding orchestrator agent (see §11) |
| `workflows/*.yaml` | Documented directly in each agent's "When to invoke" / a `## Handoffs` section, matching how `stock-data-prep` → `stock-analyst` is documented today |
| `schemas/*.json` + Pydantic validators | A `validate_thesis.py` script per artifact type, mirroring `evaluate-stock-decision/scripts/validate_recommendation.py`'s pattern (deterministic recompute, not LLM self-certification) |
| New `investment-system/` repo with its own `pyproject.toml`/`uv.lock` | A subtree of this repo (`research-v2/`), sharing this repo's `uv` environment and `src/` calculators — see §6 |
| Evidence store + DuckDB tables (§18, Phase 1 of source plan) | Deferred past this plan's Phase 1/2 (§9.4) — start with timestamped JSON files, add DuckDB only if the file-based version proves insufficient |

## 5. What v2 reuses outright

Reused as-is, not reimplemented, from day one:

- **Portfolio math**: `src/position_engine.py`, `src/portfolio_metrics.py`,
  `src/analytics.py`, `src/database.py` (DuckDB). Whenever a later phase
  (Portfolio Manager, §9.1) needs weights, exposure, or risk numbers, it
  calls these, exactly like `stock-data-prep` does today.
- **Data pull mechanics**: the yfinance extraction approach already proven
  in `.claude/skills/fetch-stock-research-data/`. Phase 1 forks the *skill*
  (new, independent script under `research-v2`'s own skill — see §6) but
  does not reinvent how to call `yfinance` or which fields are safe/JSON-safe;
  that groundwork is copied forward, not redesigned.
- **Benchmarking instrumentation**: `.claude/hooks/usage_tracker.py` already
  logs per-agent token usage and duration to `logs/AgentSkillUsage.txt`
  (`docs/architecture/usage_tracking.md`). Comparing v1 vs v2 cost per
  ticker is already free — no new instrumentation needed (§10).
- **Repo conventions**: agent front matter shape, least-privilege `tools`
  arrays, `## Handoffs` tables instead of a Task-holding orchestrator,
  `docs/agents/<agent>/` documentation requirement, and the
  evidence-before-opinion / no-invented-values / no-trade-execution
  guardrails already enforced in v1.

## 6. Directory layout

Agent and skill *definitions* must live where Claude Code auto-discovers
them (`.claude/agents/`, `.claude/skills/`) — that's not negotiable. Their
*data* (evidence, theses, market notes) gets a fully separate top-level
tree so the whole benchmark track can be deleted in one step if it doesn't
pan out, without touching `Knowledge-Base/`:

```text
.claude/
  agents/
    investment-data-prep.md      # Phase 1, haiku
    investment-analyst.md        # Phase 1, opus
    market-researcher.md         # Phase 2
  skills/
    gather-investment-evidence/  # Phase 1 mechanical fetch + persist
      SKILL.md
      scripts/
        fetch_and_persist.py
    draft-investment-thesis/     # Phase 1 judgment scaffolding + validator
      SKILL.md
      scripts/
        thesis_worksheet.py
        validate_thesis.py
    build-market-landscape/      # Phase 2
      SKILL.md
      scripts/

research-v2/                     # new, sibling to Knowledge-Base/, fully separate
  evidence/
    <TICKER>/
      <date>-yfinance.json       # raw persisted pull + retrieved_at + content hash
  theses/
    <TICKER>/
      <date>-thesis.md
      current.yaml               # points at the active thesis, like the source plan's kb/theses pattern
  market/
    <date>-landscape.md
    current.yaml
  logs/
    run-log.jsonl                # append-only, one line per Phase 1/2 run

exports/
  investment-analyst-v2/         # draft artifacts pending validation, mirrors exports/stock-recommendations/
    <TICKER>-<date>.json

docs/
  agents/
    investment-data-prep/
    investment-analyst/
    market-researcher/
```

Why `research-v2/` and not `Knowledge-Base/v2/`: `knowledge_base.md` already
flags that Windows' case-insensitive filesystem makes near-identical
directory names a real source of confusion (`knowledge-base/` vs.
`Knowledge-Base/`) — a clearly distinct name avoids repeating that mistake,
and keeps "is this v1 or v2 data" answerable by directory name alone.

## 7. Phase 1 — Investment Analyst (evidence + thesis)

### 7.1 Scope

For one ticker: gather evidence, persist it, and produce a cited bull/base/
bear thesis with a fundamental rating. No portfolio sizing, no target
weight, no buy/sell action — that judgment stays with the (deferred)
Portfolio Manager, matching the source plan's explicit separation
(`investment_analyst` "must not decide final portfolio size").

### 7.2 Two-agent split (recommended, not mandatory)

v1's haiku/opus split (`docs/architecture/decision_support_flow.md`) is a
proven cost pattern in this repo — cheap mechanical steps on haiku, judgment
reserved for opus. Recommend carrying that forward:

- **`investment-data-prep`** (haiku) — fetches yfinance data via
  `gather-investment-evidence`, persists the raw response under
  `research-v2/evidence/<TICKER>/`, and builds a worksheet listing what
  evidence is available. No judgment.
- **`investment-analyst`** (opus) — reads the worksheet, answers the source
  plan's §9.1/§9.2 required questions (thesis, variant perception, bull/
  base/bear, expected return, key risks, invalidation conditions), and
  writes the thesis artifact.

If a single combined agent turns out simpler to iterate on for Phase 1
(fewer moving parts while the contract is still unstable), that's a fine
starting point too — split it once the worksheet contract stabilizes. Flag
this as the first thing to decide when you start building, not something
this plan locks in.

### 7.2a Data layer already built (as of 2026-08-04)

Before the `investment-data-prep`/`investment-analyst` agents or the
`research-v2/` tree existed, the `investment-analyst-resources` skill
(`.claude/skills/investment-analyst-resources/`) was built as the DB-first
data layer this Phase 1 plan needs underneath it — per its own `SKILL.md`:
"the data layer beneath the v2 investment-analyst track's Phase 1." It
diverges from §7.3's evidence-file design in one respect worth noting here:
it's DB-first, not JSON-evidence-file-based — it reads positions, ledger,
prices, financials, earnings, dividends, and classification straight out of
DuckDB (refreshing only what's stale for the given ticker) and tops that up
with a narrow live yfinance pull for the groups DuckDB can't hold
(valuation, analyst ratings, options, news, insider, institutional, funds).
Whether Phase 1's evidence-file persistence (§7.3) sits on top of this or
replaces it is still open — flag alongside §12's open decisions.

That skill also had a real staleness bug fixed on 2026-08-04: its
`portfolio_context.position_market_value`/`weight_pct` were sourced from
`exports/portfolio-classification/portfolio-classification.json`, a
snapshot that could be (and was, in practice) stale relative to prices the
same run had just refreshed. Fixed by adding
`position_engine.read_live_position_values()` — a shared, connection-
agnostic query — so both `src/analytics.py`'s write path (the dashboard/CLI)
and this skill's read-only path compute market value/weight from the same
live query instead of two independent implementations of the same math.
Full rationale and diff scope: `docs/plans/investment-analyst-resources-live-position-value.md`.
Original skill design: `docs/plans/investment-analyst-resources-skill.md`.

### 7.3 Evidence contract (minimal, file-based)

Not the source plan's full §18 evidence schema (content hash + parser
version + confidence + reused flag, immutable raw store, DuckDB tables) —
that's real infrastructure worth building once Phase 1 shows what fields
actually get used. Start with the minimum that keeps evidence traceable:

```yaml
security_id: PLTR
source_id: yfinance
retrieved_at: 2026-08-04T18:00:00-04:00
content_hash: sha256:...
raw: { ... unmodified yfinance response ... }
```

One file per pull under `research-v2/evidence/<TICKER>/<date>-yfinance.json`.
Reused if a same-day pull already exists; refetched otherwise. This is
already more provenance than v1 carries today (v1's `fetch-stock-research-data`
output is explicitly ephemeral) — a legitimate, incremental improvement
worth having independent of which system "wins" the benchmark.

### 7.4 Thesis artifact contract

Answers the source plan's new-position/existing-position question lists
(§9.1/§9.2) directly, trimmed to what's answerable from yfinance-only
evidence in Phase 1:

```yaml
security_id: PLTR
as_of: 2026-08-04
evidence_ids: [...]
role: "what role would this play in a portfolio"
variant_perception: "why the market might be mispricing this"
fundamental_rating: attractive | neutral | unattractive
bull_case: "..."
base_case: "..."
bear_case: "..."
expected_return: { bear: ..., base: ..., bull: ... }
key_risks: [...]
invalidation_conditions: [...]
open_questions: [...]        # evidence that was missing or stale
confidence: high | medium | low
```

`confidence` follows the same discipline as v1: evidence that's missing is
never guessed, and enough missing evidence caps confidence at `low` rather
than blocking output entirely.

### 7.5 Freshness (Phase 1 version)

Field-specific freshness (source plan §17) is real but premature to encode
fully before Phase 1 has running data. Start with two rules and expand only
when a gap actually bites:

- Evidence pulled today is fresh; anything older triggers a refetch.
- A thesis is fresh for 30 days (matches v1's existing thesis-staleness
  window) unless a new earnings report or major price move has occurred.

### 7.6 Build steps

1. `gather-investment-evidence` skill: fetch yfinance data for a ticker
   (reuse the field groups from `fetch-stock-research-data`), write the
   evidence file under `research-v2/evidence/<TICKER>/`, print a summary
   (not raw JSON — the agent should never `Read` the persisted file
   directly, matching the "print summaries, don't re-read JSONs" discipline
   documented in `decision_support_flow.md`).
2. `investment-data-prep` agent: calls the skill, builds the worksheet.
3. `draft-investment-thesis` skill + `thesis_worksheet.py`: assembles the
   worksheet the analyst scores against.
4. `validate_thesis.py`: schema/length/citation checks on the completed
   artifact before it's written to `research-v2/theses/`, mirroring
   `validate_recommendation.py`'s deterministic-recompute pattern (no field
   the LLM could silently fudge, like a computed confidence value, should
   be LLM-authoritative).
5. `investment-analyst` agent: scores the worksheet, writes the thesis,
   runs the validator, commits to `research-v2/theses/<TICKER>/` and
   updates `current.yaml`.
6. `docs/agents/investment-data-prep/` and `docs/agents/investment-analyst/`
   documentation, per `CLAUDE.md`'s requirement.

### 7.7 Definition of done

- Running "evaluate PLTR with the v2 analyst" produces a validated thesis
  artifact citing real evidence IDs, with no invented values.
- The same ticker can be re-run and reuses same-day evidence instead of
  re-fetching.
- `logs/AgentSkillUsage.txt` shows both `investment-data-prep` and
  `investment-analyst` token/duration figures, comparable to v1's
  `stock-data-prep`/`stock-analyst` entries for the same ticker.

## 8. Phase 2 — Market Researcher

### 8.1 Scope

Produce a versioned market landscape (source plan §9.4 coverage list:
growth/inflation, central-bank policy, credit conditions, equity breadth/
vol, sector/factor leadership, CAD/USD, earnings revisions, upcoming
events) so Phase 1's Investment Analyst can pull relevant sections instead
of re-deriving macro context per ticker.

### 8.2 Sources, phased

Start cheap, add sources only as needed:

- **Bank of Canada Valet API** — no key, JSON/CSV, covers CAD/USD and
  policy rates directly (source plan §16.1).
- **yfinance-derived breadth/volatility proxies** — VIX level, major index
  and sector-ETF performance, already reachable with the same library
  Phase 1 uses.
- Deferred: StatCan WDS, BLS, U.S. Treasury curve, FINRA short interest —
  add only when a specific security review is blocked without them (§9.4).

### 8.3 Versioning pattern

Same `current.yaml` pointer pattern as the source plan's §20 KB design and
Phase 1's thesis storage: dated notes under `research-v2/market/`, never
overwritten, with `current.yaml` tracking the active one. Each update is
differential — what changed, why, which prior conclusions still hold —
per source plan §9.4/§20.

### 8.4 Consumption by Phase 1

Once Phase 2 exists, `investment-analyst`'s worksheet gains a
`market_context` block (report id + relevant section excerpts only, not
the whole report) — the same "small context by default" discipline
(source plan §4.5) v1 already follows by never loading the whole KB into a
model.

### 8.5 Build steps and definition of done

1. `build-market-landscape` skill: pulls BoC Valet + yfinance breadth data,
   writes a dated note under `research-v2/market/`.
2. `market-researcher` agent (opus or sonnet — a landscape summary is
   closer to the ETF-batch judgment tier than single-stock scoring; default
   to sonnet and revisit if quality is lacking).
3. Update `investment-analyst`'s worksheet builder to accept an optional
   `--market-report` pointer once one exists.
4. `docs/agents/market-researcher/` documentation.

Done when a market landscape note is produced on demand, `current.yaml`
points at it, and a subsequent Investment Analyst run references it instead
of independently characterizing macro conditions.

## 9. Deferred phases (sketched only — do not build yet)

### 9.1 Portfolio Manager

The highest-leverage missing piece relative to v1 today: v1 has no
LLM-judgment portfolio-level agent, only deterministic reports
(`portfolio-classifier`, `holdings-reconciliation`). A v2 Portfolio Manager
would read `src/analytics.py`'s exposure/risk output plus
`research-v2/theses/`, apply `Knowledge-Base/portfolio/allocation-policy.md`
(or a v2-specific policy file if the benchmark should also test policy
representation), and produce prioritized actions with target weights —
matching source plan §9.3 and §21. Explicitly out of scope until Phase 1/2
are running.

### 9.2 Options Specialist

Turns already-available options data (put/call OI, IV skew — the same
fields v1's `options_activity` dimension already computes in
`scoring_worksheet.py`) into real payoff/breakeven/Greeks/strategy
comparisons per source plan §9.6/§22.5. Meaningful only once there's an
underlying thesis (Phase 1) to compare an options structure against.

### 9.3 Challenger pass

Source plan §10 proposes a separate adversarial agent. In this repo, the
cheaper equivalent is a **second, conditional invocation of
`investment-analyst`** with a challenge prompt (find disconfirming
evidence, hidden assumptions, failure scenarios) rather than a new agent
definition — consistent with this repo's preference for narrow agents
without unnecessary proliferation. Revisit as a dedicated agent only if the
in-context challenge prompt proves insufficiently independent.

### 9.4 Evidence-layer hardening / multi-source expansion

The source plan's §16-18 (SEC EDGAR, SEDAR+, BoC/StatCan/BLS/Treasury/FINRA
collectors, full evidence schema with parser versioning and conflict
records, DuckDB-backed evidence tables) is real, valuable work — but it's
explicitly the "not fully working how I'd like" territory flagged during
scoping, and its exact shape should follow from what Phase 1/2 reveal is
actually missing, rather than being speculatively built first. Treat
§16.1's source table as the reference menu when a specific gap justifies
adding a collector (e.g., filings-based fundamentals once yfinance's
`overview`/financial-statement fields prove insufficient for a real
decision).

## 10. How the benchmark actually runs

For a given ticker, run both systems and compare:

1. **Evidence traceability** — does the v2 thesis cite verifiable evidence
   as cleanly as v1's rubric citations?
2. **Reasoning quality** — read both outputs side by side; does the
   narrative thesis surface things the rubric's fixed dimension list
   misses, or vice versa?
3. **Cost** — `logs/AgentSkillUsage.txt` already has both systems'
   token/duration figures per agent invocation; no new tooling needed to
   compare `investment-data-prep`+`investment-analyst` against
   `stock-data-prep`+`stock-analyst` for the same ticker.
4. **Stability** — rerun the same ticker with unchanged evidence; the
   thesis should not materially change (matches the source plan's §29
   "recommendation stability when inputs are unchanged" evaluation metric).

## 11. Guardrails (carried over, unchanged)

- No invented financial values — missing evidence is `unknown`/omitted,
  never guessed, in both systems.
- No trade execution in either system.
- No agent besides `kb-orchestrator` holds the `Task` tool
  (`docs/architecture/claude_agent_skill_structure.md`) — Phase 1/2's two-
  or three-step sequences are short enough to invoke directly from the top
  level via a `## Handoffs` table, the same way `stock-data-prep` hands off
  to `stock-analyst` today. Do not add a v2 orchestrator agent unless a
  later phase's fan-out genuinely needs one.
- `investment-data-prep`/`investment-analyst` are read-only outside
  `research-v2/` and `exports/investment-analyst-v2/` — no writes to
  `Knowledge-Base/` from this track.
- Document each new agent under `docs/agents/<agent>/` alongside its
  `.claude/agents/*.md` file, per `CLAUDE.md`.

## 12. Open decisions to confirm while building (not pre-decided here)

- Single combined agent vs. the data-prep/analyst split for Phase 1 (§7.2)
  — start simple, split once the worksheet contract is stable.
- Whether `research-v2/evidence/` needs a DuckDB index once there's enough
  volume to search, or stays flat files indefinitely.
- What specifically about the current yfinance-only evidence pull isn't
  "working how you'd like" — flagged during scoping but not itemized; worth
  naming concretely once Phase 1 evidence is in hand, so §9.4's source
  expansion targets the right gap first.

## 13. Build order backlog

1. `research-v2/` directory + `.gitignore` entries matching `exports/`'s
   pattern if any of this should stay local-only.
2. `gather-investment-evidence` skill + script.
3. `investment-data-prep` agent.
4. `draft-investment-thesis` skill (worksheet builder + validator).
5. `investment-analyst` agent.
6. `docs/agents/investment-data-prep/`, `docs/agents/investment-analyst/`.
7. Run Phase 1 end-to-end on one ticker already covered by v1 (e.g. PLTR,
   which has both an existing thesis page and recommendation history) to
   get a real side-by-side comparison.
8. `build-market-landscape` skill + `market-researcher` agent (Phase 2).
9. Wire `investment-analyst`'s worksheet to accept the market report.
10. Revisit §9 phases based on what Phase 1/2 actually show.
