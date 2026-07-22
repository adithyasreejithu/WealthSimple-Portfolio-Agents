# KB Population System — Agent Implementation Plan

*Status: proposed — ready to implement. Companion to
`docs/plans/financial-snapshots-pipeline.md` (data layer, mostly built) and
the design decisions recorded during scoping (see "Source" below).*

**Source of truth for every decision referenced here**: the rebuild scope
doc drafted collaboratively with Adithya, decisions #1-18 (#18 was
proposed, then reverted — see below). This plan turns those decisions into
concrete files, in build order. It does not re-litigate anything already
decided — where a decision is referenced, it's settled.

**2026-07-21 correction**: an earlier version of this plan dropped the
dedicated Orchestrator agent in favor of a scheduled top-level prompt
(decision #18). Adithya did not agree to that and explicitly overrode it:
the Orchestrator is a real agent holding the `Task` tool, invoked directly
(manually, on demand) for now. Scheduling it is a separate, later
decision — "Later we can think about running schedules." This plan is
rewritten to reflect that: **four** agents, matching decision #3's
original design, not three.

## What this plan does NOT cover

- KB template/spec changes (decision #7/#8's follow-up: `competitor-note`
  type, `stock-thesis-template.md` markers, `front-matter-spec.md`
  updates, `kb_pages.py` `PAGE_TYPES`/`STATUS_VALUES` changes). Tracked
  separately in the scope doc's "Follow-up work" list — build before or
  alongside step 3 below, since the writer agent depends on it.
- Decision #9's CLI command (`annual-financial-context`) — separate,
  already in progress per `docs/plans/financial-snapshots-pipeline.md`'s
  "Decision #9 implementation spec." This plan's Prep agent (step 2) calls
  that command once it exists; it does not rebuild it.
- The **required-fields schema** (front-matter contract for what must be
  populated before a page counts as "done"). Deliberately deferred, not
  designed up front — see "Deliberately deferred" at the end of this doc.
- Scheduling `kb-orchestrator` to run automatically. It's built and run
  manually/on-demand first; automation is a separate, later decision.

## Architecture (final — decision #3, restored)

Four agents. The Orchestrator is a real subagent that holds the `Task`
tool — a first-of-its-kind exception to this repo's documented convention
(`docs/architecture/claude_agent_skill_structure.md` currently states no
agent here holds `Task`; that doc needs a small update once this is built,
noting the exception and why). Invoked manually/on-demand for now;
scheduling is a separate, later decision, not a precondition for building
it this way.

| Component | What it is | Role |
|---|---|---|
| Staleness-gate script | Plain Python, no agent identity | Computes which tickers have at least one due section this run (decision #16); supports `--force`/`--dry-run`; invoked by the Orchestrator via `Bash` |
| **Orchestrator** (new) | `.claude/agents/kb-orchestrator.md` | Holds `Task`. Runs the gate script, fans out Prep+Analyst per due ticker in parallel, waits for all artifacts, makes one sequential Writer call. No judgment of its own — fixed sequence only. |
| **Prep** (`stock-data-prep`, restored) | `.claude/agents/stock-data-prep.md` | Mechanical fetch: KB existence check, yfinance research data, decision #9's first-run annual context via `bootstrap-stock-research`, worksheet build |
| **Analyst** (`stock-analyst`, restored) | `.claude/agents/stock-analyst.md` | Scores the worksheet against `decision-rubric.yml`, writes narratives |
| **Writer** (`kb-intake`, restored) | `.claude/agents/kb-intake.md` | Commits sections to `Knowledge-Base/`, appends logs, updates `section_updated` (decision #16) |

Three of the four agent files already exist as complete,
previously-working files under `.claude/agents/_archive/` — Prep,
Analyst, and Writer are a **restore-and-update**, not a from-scratch
rebuild. The archived versions predate decisions #7-#17, so each needs
the updates listed per-step below, but the bulk of each agent's body
(When to invoke, Guardrails, Output Format, skill references) carries
forward unchanged. The **Orchestrator has no archived equivalent** — it's
genuinely new, built from scratch in step 5.

## Build order

Sequenced so nothing gets built against a dependency that doesn't exist
yet — matches how the financial-snapshots pipeline was sequenced
(data/schema first, agent-facing tooling last).

### Step 0 — Prerequisites (confirm before starting agent work)

- [ ] **Decision #9 code migration (found mid-progress 2026-07-21).**
  `.claude/skills/bootstrap-stock-research/SKILL.md` is written and
  documents the intended architecture, but the actual code hasn't moved
  yet: `fetch_annual_financial_context`, `ANNUAL_CONTEXT_SCHEMA`,
  `parse_annual_context_args`, and `main_annual_context` are still in
  `src/financial_snapshots_extractor.py` (matching the original spec in
  `financial-snapshots-pipeline.md`, since superseded), and the skill's
  `scripts/` folder is empty. To close this out:
  1. Create `.claude/skills/bootstrap-stock-research/scripts/annual_financial_context.py`
     containing `fetch_annual_financial_context`, `parse_args`, `main` —
     importing (not duplicating) `_num`, `_line_item`,
     `_extra_line_items`, `_json_safe`, `_safe_statement`,
     `_create_ticker`, `_build_session`, and the label-alias tuples from
     `src/financial_snapshots_extractor.py`.
  2. Remove those four names from `src/financial_snapshots_extractor.py`
     once the skill's copy is verified working.
  3. Update `src/app.py`'s `annual-financial-context` delegated-command
     entry to import the skill's `main()` the same way `portfolio-classify`
     does (`sys.path.insert(0, ...)` before import), instead of pointing
     at `financial_snapshots_extractor.main_annual_context`.
  4. Move the annual-context test cases out of
     `tests/test_financial_snapshots_extractor.py` into
     `tests/test_bootstrap_annual_context.py`, matching the file name the
     `SKILL.md` already documents.
  5. Re-run the full test suite to confirm nothing broke in the move.
- [ ] Decision #9's `annual-financial-context` CLI command is live-verified
  against real annual yfinance data (still open regardless of where the
  code lives — see `financial-snapshots-pipeline.md` section 5).
- [ ] Section 16's `section_updated` front-matter field is added to
  `Knowledge-Base/templates/front-matter-spec.md` and enforced in
  `src/kb_pages.py` (new optional field — no `PAGE_TYPES`/`STATUS_VALUES`
  change needed, just a new recognized key). This blocks the Writer agent
  (step 4) from having anywhere to record what it wrote.
- [ ] Decision #7/#8's KB template follow-up work (`competitor-note` type,
  `sentiment-notes/`/`competitor-notes/` folders, template markers) — the
  Writer agent's Guardrails will reference these types, so they should
  exist first even if lightly used initially.

### Step 1 — Staleness-gate script

New, no old equivalent. Lives in a new skill's `scripts/` folder — this is
orchestration logic specific to the KB population workflow, not general
pipeline code, so per `CLAUDE.md`'s rule it belongs beside a skill, not in
`src/`. Recommended location: `.claude/skills/kb-staleness-gate/scripts/staleness_gate.py`
with a thin `SKILL.md`. The Orchestrator agent (step 5) runs this skill
first, via `Bash`, before dispatching anything.

**Function**: `compute_due_tickers(owned_tickers, force=None, dry_run=False) -> dict`

- Input: the list of currently-owned tickers (reuse the existing
  `get_market_targets`-style helper already used by
  `sync_financial_snapshots`/`sync_earnings_dividends`, or read
  `portfolio-classification.json` directly — either is fine, this script
  never touches `Knowledge-Base/` itself, only reads it).
- For each owned ticker: check whether `Knowledge-Base/stocks/<TICKER>.md`
  exists.
  - Missing → ticker is due, all sections due, `first_run: true`.
  - Exists → parse its `section_updated` front-matter map (falling back to
    top-level `updated:` for any missing key, per decision #16), compute
    which of the 11 gated sections are past their cadence (7 or 30 days
    per the tier lists in decision #16), and mark the ticker due if that
    set is non-empty. DB-backed sections (Earnings and Catalysts,
    Dividend Analysis, Financial Analysis) and non-gated sections
    (Original Thesis, Decision History, Sources) are never part of this
    computation — they're excluded per decision #16.
- `--force TICKER [TICKER...]`: bypass the gate for the named tickers,
  mark every gated section due regardless of last-updated date. Does not
  set `first_run` unless the page genuinely doesn't exist.
- `--dry-run`: run the exact same computation, print the result, exit —
  never invoked by anything that writes. This is the safe path for
  testing the gate logic without touching real KB state, per decision
  #16's testing requirement.
- Output: one JSON document to stdout (matches decision #12's "one
  versioned-JSON output per invocation" discipline):
  ```json
  {
    "schema": "kb-staleness-gate.v1",
    "run_date": "2026-07-21",
    "due_tickers": [
      {"ticker": "AAPL", "first_run": false, "due_sections": ["status", "market_sentiment"]},
      {"ticker": "NEWCO", "first_run": true, "due_sections": ["<all 11>"]}
    ],
    "skipped_count": 42
  }
  ```

**Tests**: a new `.claude/skills/kb-staleness-gate/scripts/test_staleness_gate.py`
(or under `tests/` if the project prefers central test discovery — confirm
against how `classify-portfolio`'s skill-scoped scripts are tested, since
that's the closest existing precedent for skill-owned Python with its own
tests) covering: first-run detection, weekly-tier-only-due case,
monthly-tier-only-due case, nothing-due case, `--force` bypass, `--dry-run`
performs no writes (assert no filesystem mutation), missing
`section_updated` falls back to top-level `updated`.

### Step 2 — Restore `stock-data-prep`

Copy `.claude/agents/_archive/stock-data-prep.md` to
`.claude/agents/stock-data-prep.md`. Required updates to the restored file:

1. **Decision #9 integration**: when the gate script reports
   `first_run: true` for a ticker, additionally invoke the
   `bootstrap-stock-research` skill (add it to the restored agent's
   `skills:` front-matter list, alongside `kb-search` and
   `fetch-stock-research-data`) and include its JSON output in the
   worksheet/report alongside the existing research data. Update the
   "Workflow" section's step 2 to describe this conditional fetch, and
   note in "Guardrails" that this output is ephemeral context only, never
   written anywhere. Once step 0's code migration lands, this is a skill
   invocation like any other the agent already uses — not a bare `Bash`
   CLI call.
2. **Due-sections awareness**: the agent should receive `due_sections`
   from the gate script's output (passed in its invocation prompt by the
   Orchestrator agent, via `Task`) and only fetch/prepare data relevant to
   those sections when this isn't a first run — avoids wasted fetches for
   sections that aren't due this cycle.
3. Everything else (guardrails against judgment/writing, concurrency
   safety across tickers, worksheet-building via `evaluate-stock-decision`,
   never reading the JSON artifacts itself) carries forward unchanged —
   this agent's core job hasn't changed.

**Tests**: none new required beyond what the skill scripts it calls
already cover (`evaluate-stock-decision`, `fetch-stock-research-data`) —
this agent file itself isn't unit-testable the way Python is; verify by
running it against one real ticker end-to-end (see "Verification" below).

### Step 3 — Restore `stock-analyst`

Copy `.claude/agents/_archive/stock-analyst.md` to
`.claude/agents/stock-analyst.md`. Required updates:

1. If step 2's Prep agent hands over decision #9's annual context on a
   first run, the Analyst's "Workflow" section needs a line describing
   how to use it: narrative-only input for the initial Company Overview /
   Original Thesis, never a scored dimension, never persisted.
2. Confirm the rubric (`Knowledge-Base/taxonomy/decision-rubric.yml`,
   currently `v1.3`) still matches whatever this agent's archived version
   expected — no rubric changes are in scope here (per
   `financial-snapshots-pipeline.md`'s "Deliberately deferred": wiring
   `financial_snapshots` into scored dimensions is separate,
   `author-decision-rubric` work), just confirm compatibility.
3. No other changes expected — the score-against-rubric judgment core is
   unaffected by anything decided in this rebuild.

**Tests**: same as step 2 — verify end-to-end, not unit tests.

### Step 4 — Restore `kb-intake`

Copy `.claude/agents/_archive/kb-intake.md` to `.claude/agents/kb-intake.md`
(this is the **Writer** in the scope doc's terms — kept as `kb-intake` for
continuity with existing skills/scripts that already reference that name).
Required updates:

1. **`section_updated` bookkeeping** (decision #16): every time this agent
   rewrites a gated section, it must update that section's key in the
   page's `section_updated` front-matter map to today's date. This is new
   guardrail language plus likely a small script change in whichever
   skill script performs the actual front-matter write (`kb-update-thesis`'s
   scripts, per the archived skill).
2. **Halt-on-failure behavior** (decision #17): if this agent's write
   fails partway through a batch commit (the existing "loop
   `ingest_recommendation.py` over artifacts one at a time" pattern), it
   must stop processing further artifacts in that batch and report which
   ticker it failed on and how many succeeded first — the Orchestrator
   agent (not this agent) is what actually halts the overall run, but
   this agent's report is what tells it to.
3. **New page type awareness**: once step 0's `competitor-note`/
   `sentiment-note` template work lands, this agent's Guardrails should
   reference the new types where relevant (e.g. filing a competitor
   comparison under `market-research/competitor-notes/` per decision #7,
   not inside `stocks/TICKER.md`).
4. Everything else (sole-writer boundary, immutability rules for Original
   Thesis/Decision History, logging discipline, no-fabrication guardrail)
   carries forward unchanged.

**Tests**: same pattern — verify end-to-end against a scratch copy of the
KB, never the live one, mirroring how `financial-snapshots-pipeline.md`'s
verification step used a scratch database copy.

### Step 5 — Build `kb-orchestrator` (new agent, no archived equivalent)

New file: `.claude/agents/kb-orchestrator.md`. Front matter, following the
convention in `docs/architecture/claude_agent_skill_structure.md`:

```yaml
---
name: kb-orchestrator
description: Use this agent to run the KB population workflow -- computes which owned tickers are due for an update, fans out stock-data-prep + stock-analyst per due ticker, then makes one sequential kb-intake call to commit the results. Typical triggers: "run the KB population workflow", "update the knowledge base for due tickers", "run kb-orchestrator". Invoked manually/on-demand for now -- see Guardrails for why this is the one agent in this repo that holds Task.
model: haiku
color: orange
tools: ["Task", "Bash", "Read"]
skills:
  - kb-staleness-gate
---
```

`haiku` because, like `stock-data-prep`, this agent makes no judgment
calls — it runs a fixed sequence and reports outcomes. `Task` is granted
deliberately as a first-of-its-kind exception (see "Guardrails" below,
and the note this requires in `claude_agent_skill_structure.md`).

**Workflow**:

1. Run the step-1 staleness-gate skill via `Bash` (`--dry-run` first
   during initial testing, then for real).
2. For every due ticker in the result, dispatch one `stock-data-prep` +
   `stock-analyst` invocation pair via `Task`, split into equity/ETF
   batches run concurrently — this is the exact fan-out pattern already
   proven in `decision_support_flow.md`, unchanged, just triggered by
   this agent instead of a human.
3. Wait for every recommendation artifact.
4. One single `kb-intake` invocation via `Task`, looping deterministically
   over every artifact (never one invocation per ticker — matches the
   existing anti-pattern warning in `decision_support_flow.md`).
5. If `kb-intake` reports a halt (decision #17), stop — do not retry the
   remaining tickers in the same run. Report the halt clearly: how many
   of N tickers completed, which one triggered the halt.

**Guardrails**:

- No judgment: never scores, never writes narratives, never writes to
  `Knowledge-Base/` directly. Its only outputs are `Task` dispatches and a
  final run summary.
- This is the one agent in this repo holding `Task` — deliberate,
  narrow-purpose exception per Adithya's explicit direction, not a
  precedent for giving other agents `Task` freely. Document this
  reasoning inline in the agent file itself, not just here, so it's clear
  to anyone reading `.claude/agents/kb-orchestrator.md` cold.
- Runs manually/on-demand only for now. Do not wire this into the
  `schedule` skill without Adithya explicitly deciding to do so —
  scheduling is a separate, later decision (see "Deliberately deferred").

**Tests**: not unit-testable as an agent file. Verify end-to-end per
"Verification" below.

## Verification (overall)

1. Steps 1-4 each individually testable/runnable before building step 5.
2. First full dry run: `--dry-run` on the gate script against the real
   portfolio, confirm the due-ticker list looks sane (spot-check a couple
   of known-stale and known-fresh tickers by hand).
3. First full real run: `--force` a small number of tickers (2-3,
   mixing a first-run new ticker and an existing one), manually invoke
   `kb-orchestrator`, inspect the resulting KB pages and `update-log.md`
   entries by hand before trusting anything further.
4. Keep running `kb-orchestrator` manually/on-demand for day-to-day runs
   (decision #5 — up to ~5 tickers/day is the target steady state) until
   Adithya separately decides to schedule it — see "Deliberately
   deferred."
5. Full-portfolio backfill (decision #5's "first run is heavier") is run
   manually regardless, given its size — same caution the
   financial-snapshots/earnings-dividends pipelines already applied.

## Deliberately deferred

- **Required-fields schema** — what must be populated per page/ticker
  before a page counts as complete, and how discovery-critical fields
  (`tickers:`/`tags:` accuracy per decision #8) get validated.
  Deliberately left undefined for now — Adithya's call: figure this out
  once the agent rebuild below is far enough along to know what's
  actually needed in practice, rather than designing it up front against
  a system that doesn't exist yet. Tracked in `docs/project/todo.md`
  Backlog. A version already exists, archived, at
  `.claude/skills/_archive/kb-update-thesis/references/thesis-contract.md`
  — restore and update that against decisions #7-#17 (new page types,
  `section_updated`, DB-backed sections) when this comes up, rather than
  starting from scratch. Not a blocker for anything in this plan.

- **Scheduling `kb-orchestrator`** — running it automatically (via the
  `schedule` skill or an external `cron` + `claude -p` invocation) is
  explicitly deferred. Adithya: "Later we can think about running
  schedules." Build and verify the agent manually first; automation is a
  separate future decision, not part of this plan's scope.
