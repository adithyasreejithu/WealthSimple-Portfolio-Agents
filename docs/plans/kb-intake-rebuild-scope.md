# KB Population System — Rebuild Scope

**Status:** Draft, v0.3 — architecture decided collaboratively (decisions
#1-18, #18 proposed then reverted). Design is settled; the required-fields
schema is deliberately deferred, not open. See
`docs/plans/kb-population-agent-rebuild.md` for the build plan derived
from these decisions.

## Context

The original `kb-intake` agent (and its four skills) was archived in
`feb3cdf` alongside the rest of the decision-support system, preserved as
reference in `.claude/agents/_archive/kb-intake.md` and
`docs/agents/kb-intake/`. This doc scopes the rebuild, drawing on
`docs/project/agent-development-lessons.md` where it applies, deviating
where Adithya's actual usage pattern calls for something different (e.g.
full unattended automation, which the old design never attempted).

## Decisions made so far

1. **Sole writer boundary.** The writer agent (successor to `kb-intake`)
   remains the only agent that writes to `Knowledge-Base/`, excluding
   `ref/*.yaml`, `README.md`, and `CHANGELOG.md`.
2. **Four agents, not one monolith.** Split by role: an orchestrator, a
   mechanical fetch agent, a judgment/analysis agent, and a KB-write agent.
   Reasoning: model cost fits the step (cheap model for zero-judgment
   fetch, strongest model only for the scoring step), tool access stays
   least-privilege (fetch/analyze never hold `Write`/`Edit`), context
   resets at each agent boundary instead of accumulating across steps, and
   parallel-safe steps (fetch, analyze) can fan out per ticker
   independently of the must-serialize write step.
3. **Orchestrator holds `Task`.** It is the only agent in this design
   permitted to invoke other agents — its sole job is running the fixed
   fetch → analyze → write sequence per ticker. It carries no judgment of
   its own and makes no analysis or write decisions directly. (Decision
   #18 proposed dropping this in favor of a scheduled top-level prompt;
   Adithya rejected that and this decision stands as originally written —
   see #18's entry below.)
4. **Target end state is fully unattended.** Once validated, Adithya does
   not want to be in the loop for normal runs. This is a deliberate
   departure from the old design, which always had a human manually
   chaining agent handoffs. Because the old failure mode was specifically
   "autonomy grew past what was being watched," unattended mode needs its
   safety nets (budgets, fail-closed validation) designed and proven before
   it's trusted to run without supervision — not retrofitted after.
5. **Usage pattern.** First run is a full-portfolio backfill (heavier,
   warrants more initial oversight). Day-to-day runs are small — up to
   ~5 tickers — and are the primary target for unattended automation.
6. **Log/index format and process carried forward as-is** from the old KB
   design (`update-log.md`, `research-log.md`/`decision-log.md`,
   `kb-index` tables).
7. **Multi-ticker/thematic content stays out of `stocks/TICKER.md`.** Peer/
   competitor comparisons, social/sentiment content, sector and macro notes
   live under `market-research/`, not duplicated into each affected stock
   page. Exact subfolder/type naming resolved in decision #9.
8. **Cross-linking mechanic resolved.** No separately maintained `related:`
   index. Two layers instead: a *derived* index off existing `tickers:`/
   `tags:` front matter (same mechanism `kb-search` already queries by)
   gives `kb-discovery` broad recall — this is sufficient and well-suited
   to discovery's job, since over-inclusive results are fine when a human
   or downstream agent narrows them down. A *curated* narrative mention by
   the analyst step (e.g. "this update follows COMP's earnings miss,
   covered in market-research/peer-notes/...") gives precision for a
   specific causal link that a broad tag query wouldn't surface distinctly.
   Caveat: this makes accurate `tickers:`/`tags:` tagging at write time a
   first-class, validated requirement — an under-tagged page becomes
   invisible to discovery silently, with no error signal.

## Architecture (current shape — decision #3 restored, decision #18 reverted)

**2026-07-21 correction**: decision #18 (below, struck through) proposed
dropping the dedicated Orchestrator agent in favor of a scheduled
top-level prompt. Adithya did not agree to that change and has explicitly
overridden it: **the Orchestrator is a real agent that holds the `Task`
tool**, invoked directly (manually, on demand) for now — scheduling it is
a separate, later decision, not a precondition for building it this way.
Decision #3's original architecture stands. Four agents:

| Agent | Role | Model | Old equivalent |
|---|---|---|---|
| Orchestrator | Runs the fixed per-ticker sequence via `Task` (staleness gate, fan out Prep+Analyst per due ticker, one sequential Writer call); no judgment of its own | TBD, likely cheap | None — new |
| Prep | DB ticker lookup, yfinance fetch (incl. decision #9's first-run annual context via the `bootstrap-stock-research` skill), other sources, formatting | haiku | `stock-data-prep` (restored) |
| Analyst | Score/analyze against the rubric, populate analysis field | opus (sonnet for ETF-track batches, matching the existing decision-support convention) | `stock-analyst` (restored) |
| Writer | Post to Knowledge-Base/, logs, indexes | sonnet | `kb-intake` (restored) |

Note: this repo's own convention as of the last audit
(`docs/architecture/claude_agent_skill_structure.md`) states no existing
agent holds the `Task` tool. The Orchestrator here is a deliberate,
first-of-its-kind exception in this repo — that architecture doc needs a
small update once the Orchestrator is built, noting the exception and why
(Adithya's explicit direction: an agent should run this, not a scheduled
top-level prompt).

## Writer responsibilities carried forward from the old kb-intake scope

1. Document intake — convert and file external documents under
   `Knowledge-Base/sources/`.
2. Thesis create/update — preserve `Original Thesis` and `Decision
   History` as append-only/immutable.
3. Status transitions.
4. Portfolio sync from classification output.
5. Recommendation ingestion from the Analyst agent's output.

## More decisions made

9. **Market-research type naming finalized.** Reuse the existing (currently
   unused) `sentiment-note` type + `sentiment-notes/` subfolder for social
   media content — no new type needed. Add one new type, `competitor-note`,
   with a `competitor-notes/` subfolder, for peer/competitor comparisons —
   named for clarity over the earlier `peer-note` working name.
10. **Reusable pattern for adding new evidence sources**, adopted from
    `docs/plans/stock-decision-support-technical-analysis.md` (a designed,
    not-yet-built precedent): register the new source in the rubric's
    `sources:` registry (name, producing skill, data groups), add a scored
    dimension with weight/anchors, reweight existing dimensions so weights
    still sum to 1.0, and rely on the plan's generic fallback fix so
    `scoring_worksheet.py`/`validate_recommendation.py` handle any
    registered source without hardcoding — a missing source degrades to
    `unknown` and renormalizes rather than erroring. This plan is also
    direct evidence for decision #2: it added a new capability (technical
    analysis) as a *skill* on the existing prep/analyst split, explicitly
    rejecting a new agent for it. Apply the same shape when specifying
    social media and competitor sources.

11. **Earnings + dividends: pipeline-owned, database-resident, dual
    purpose.** Both feed the dashboard (structured, chartable, includes a
    yearly estimate rollup) and decision support (the KB reads from the
    database rather than fetching itself). The pull runs through the
    normal data pipeline (like `yfinance_extractor.py`), not through the
    KB prep agent — the prep agent becomes a *reader* of this data, not a
    fetcher of it, for these two fact types specifically. Implemented per
    `docs/plans/earnings-dividends-pipeline.md`
    (`earnings_events`/`dividend_events` tables, following the existing
    `historical_records` convention: `(ticker_id, event_date)` composite
    PK, FK to `tickers`).

    **Important distinction:** `dividend_events` is company-declared market
    data (the dividend schedule/amount itself), separate from the existing
    `cash_transactions`/`transactions` tables, which record Adithya's
    actual received dividend cash from brokerage statements. Same word,
    different thing — don't conflate them.

    Dashboard-facing follow-up (API endpoint, calendar/chart view, yearly
    rollup) logged in `docs/project/todo.md` Backlog so it isn't lost, and
    deliberately not built now.

## Stock page schema (largely resolved)

**Final section classification, by update semantics:**

- **Immutable**: Original Thesis.
- **DB-backed, regenerated view** (database accumulates the history, the
  page section is just a current-state view, no append logic needed in the
  page itself): Earnings and Catalysts (`earnings_events`), Dividend
  Analysis (`dividend_events`), Financial Analysis (`financial_snapshots`,
  see `docs/plans/financial-snapshots-pipeline.md`).
- **Append-only, in the page itself** (not DB-backed — tied to actual
  decisions, not raw market data): Decision History.
- **Replace/snapshot** (old value is stale, gets overwritten): Status
  block, Valuation Analysis, Technical Analysis, Options Activity, Market
  Sentiment (synthesis only — historical trend lives in the separately
  accumulating dated `sentiment-notes/` files), Portfolio Fit, Updated
  Thesis, Bull Case, Bear Case, Analyst View, Key Risks, Open Questions,
  Monitoring Checklist, Company Overview, **Insider Activity** (see below).
- **Additive in practice, not strictly enforced**: Sources.

**Insider Activity — deferred DB-table candidate.** Same argument as
Financial Analysis applies (the rubric's `insider_activity` dimension reads
`derived:net_insider_shares`, recomputed fresh each run with nothing
persisted) but a third new table in one sitting is more than needed right
now, before the first two have run in practice. Stays replace/snapshot
(today's behavior) for now; logged in `docs/project/todo.md` Backlog as a
future candidate to revisit once `earnings_events`/`dividend_events`/
`financial_snapshots` have proven out.

**Follow-up work this now requires** (NOT YET DONE — nothing below has been
applied to the real repo files. These are decisions captured here so they
aren't lost):
- `Knowledge-Base/templates/stock-thesis-template.md` — mark Financial
  Analysis/Earnings and Catalysts/Dividend Analysis sections as
  database-regenerated, reusing the existing `<!-- kb-index:begin -->`/
  `<!-- kb-index:end -->` marker convention already used for `index.md`
  files.
- `Knowledge-Base/templates/front-matter-spec.md` — add `competitor-note`
  to the `type` enum, note `sentiment-note` is now actually in use, add a
  confidence/reliability field to `sources` (decision #15), note `related`
  is now secondary to derived tag-based lookup (decision #8), and add the
  `section_updated` field (decision #16).
- **`src/kb_pages.py` — the spec doc is descriptive, this file is the real
  enforcement.** `PAGE_TYPES` is the actual allowed-`type` list
  `validate_kb.py` checks against, and `STATUS_VALUES` maps each type to
  its allowed `status` values (`competitor-note` would use the existing
  `NOTE_STATUSES = ("draft", "final", "archived")`, same as
  `sector-note`/`macro-note`/etc.). Both the doc and this code file need to
  change together, plus whatever test file covers `kb_pages.py`'s
  validation.
- Create `Knowledge-Base/market-research/competitor-notes/` + its
  `index.md`, matching the existing `sector-notes/`/`macro-notes/` pattern.

The old `stocks/TICKER.md` template (`templates/stock-thesis-template.md`,
confirmed against the real `stocks/AAPL.md`) already covers most of what
we've discussed — reuse as-is:

- **Market Sentiment** section is the synthesis point for social media
  data (curated summary + pointer to `market-research/social-notes/`, not
  raw scraped content).
- **Earnings and Catalysts** already covers both historical earnings and
  forward-looking catalysts — needs the data feed behind it, not a new
  section.
- **Key Risks** already covers risk factors.
- **Options Activity**, **Insider Activity**, **Technical Analysis**
  already present (`Technical Analysis` is a placeholder pending the
  deferred technical-analysis phase).
- **Decision History** (append-only) and **Analyst View** (rewritten each
  update, never overrides Decision) — immutability rules unchanged.
- No Peer Comparison section — correctly absent, stays in
  `market-research/` per decision #7.

**One real gap:** the `sources` front-matter field (`{title, ref, date}`)
has no confidence/reliability marker prior to decision #15. Resolved by
decision #15 below.

## More decisions made (continued)

12. **DB/API connection method: wrapper scripts + CLI commands, matching
    the pattern already proven for earnings/dividends and
    financial-snapshots.** Rejected: raw DB access from an agent (violates
    the "constrain by tool access, not prompt wording" lesson) and a new
    MCP server (no existing precedent in this repo for wrapping its own
    DuckDB file, added infrastructure with no clear payoff over what
    already works). Known, accepted downsides: more scripts to maintain
    over time, per-call process-startup overhead, text-based handoff, no
    built-in caching across steps. **Design rule to mitigate the real risk
    (token cost, not parallelism — process overhead does not block
    concurrent calls):** every read script must consolidate into **one**
    versioned-JSON stdout output per invocation (e.g. a
    `financial-snapshot.v1`-style schema id), never several thin separate
    calls — this is what actually controls token cost, per the
    retrospective's core lesson (minimize round-trips, never make a cheap
    agent read more than it needs to report).
    **MCP explicitly reconsidered and declined for now:** hosting an MCP
    server costs no tokens, and calling one doesn't cost more tokens than a
    wrapper script either — token cost is driven by response size and
    round-trip count, not transport, so this was not a token-cost decision.
    MCP would gain a persistent connection (no process-startup overhead)
    and a structured schema "for free" from its protocol — real advantages
    — but at the cost of standing up a genuinely new, always-running
    service this repo has zero precedent for operating against its own
    database (it consumes external MCPs, but hosts none of its own). Not
    worth that operational overhead at the current scale (a handful of
    tickers/day). Revisit if that scale changes materially.
13. **Unattended-mode safety nets: split into behavior (decide now) vs.
    numeric budget (defer until real usage data exists).** No sense picking
    a token/cost ceiling with zero runs to calibrate against. Behavior
    (skip-vs-halt on a failed step, what gets logged for after-the-fact
    review) resolved in decision #17. The specific number gets set after a
    handful of manual runs establish a real baseline.
14. **First-run depth gap, resolved.** Originally assumed no persistent
    memory at all, so "first run reaches back a couple years" made sense
    unconditionally. Now that earnings/dividends/financials persist and
    accumulate forward instead of backfilling deep history in one pull (a
    single quarterly sync is only ~1-1.75 years deep, confirmed live), a
    gap existed for a truly new ticker's first analysis. Fixed: on a
    ticker's first-ever run only (no existing `stocks/TICKER.md` — a plain
    existence check), the prep step additionally pulls yfinance's **annual**
    financial statements as a one-time, ephemeral context input for the
    initial narrative — not persisted into `financial_snapshots`, no schema
    impact. Documented as decision #9 in
    `docs/plans/financial-snapshots-pipeline.md`.
    **Fully specced and implemented (2026-07-21)** — `fetch_annual_financial_context`,
    the `annual-financial-context --ticker` CLI command, and the
    `annual-financial-context.v1` JSON schema now live in the
    `bootstrap-stock-research` skill (`.claude/skills/bootstrap-stock-research/`),
    not `src/` — moved there per Adithya's explicit direction ("that's what
    it should have been from the start"). Live verification against real
    annual yfinance data is the one remaining gate before it's fully
    trusted — see `financial-snapshots-pipeline.md` section 5.
15. **Source confidence/reliability field: named 4-tier enum, adopted on a
    probationary basis.** `verified` (filings/regulatory documents),
    `vetted` (established data providers — yfinance), `scraped`
    (tracker-pulled news/articles), `social` (forum/social posts,
    unvetted). Chosen over a numeric score because the tier is largely
    determined by source *type* (a 10-Q is always `verified`) rather than a
    subjective per-instance judgment call, and matches this system's
    existing categorical-registry style (the rubric's `sources:` registry
    is named entries, not scored). Explicitly provisional — revisit once
    used in practice, not treated as locked in.

## More decisions made (continued, 2)

16. **Staleness gate: resolved, per-section cadence, two tiers.** Not a
    single page-level threshold — the section classification from
    "Stock page schema" above already sorts sections by how fast they
    actually go stale, so the gate follows that same split:
    - **Weekly tier**: Status block, Market Sentiment (synthesis),
      Options Activity, Technical Analysis (once built) — these are
      market-driven and go stale fast.
    - **Monthly tier**: Valuation Analysis, Company Overview, Bull Case,
      Bear Case, Key Risks, Open Questions, Monitoring Checklist,
      Portfolio Fit, Updated Thesis, Analyst View, Insider Activity —
      fundamentals/judgment sections that don't meaningfully change
      week to week.
    - **No gate, always current** (DB-backed regenerated views —
      freshness is a property of when `earnings-dividends-sync`/
      `financial-snapshots-sync` last ran, not page staleness): Earnings
      and Catalysts, Dividend Analysis, Financial Analysis.
    - **No gate, event-driven not time-based**: Original Thesis
      (immutable), Decision History (append-only), Sources (additive).

    **Mechanism**: a new optional front-matter field
    `section_updated: {<section-key>: DATE, ...}`, populated by the
    writer agent only for sections it actually rewrites. A page written
    before this field existed simply has no keys yet — missing keys fall
    back to the page's existing top-level `updated:` date, so this is
    backward compatible with every page already in the wiki, no
    migration needed.

    **Gate logic**: a section is "due" when
    `today - section_updated[section] >= cadence_days` (7 for weekly, 30
    for monthly). A ticker enters this run if *any* of its sections are
    due; the prep/analyst/writer steps only touch the due sections for
    that ticker, not the whole page — this is what keeps a daily run
    cheap (only what's actually stale gets reprocessed, not a full
    rewrite every time).

    **First-run exception**: no existing page at all — everything is
    due by definition, full page gets written, and decision #9's
    ephemeral annual-context pull applies.

    **Test/dev bypass, decided**: two flags on whatever script computes
    the due-ticker list — `--force <TICKER...>` (ignore the gate, run
    for real) and `--dry-run` (report what would be due, write nothing).
    `--dry-run` is what keeps testing the gate logic itself from ever
    touching real KB state.

17. **Unattended-mode safety net behavior: resolved** (number stays
    deferred per decision #13 — this is the behavior half only).
    - **Prep/analyst failures: per-ticker isolation, skip and continue.**
      A failure on ticker X does not stop tickers Y/Z in the same run —
      mirrors the per-ticker exception isolation already proven in
      `financial_snapshots_extractor.py`/`earnings_dividends_extractor.py`.
    - **Writer failures: halt the whole run immediately.** The writer is
      the one serialized bottleneck every ticker's output must pass
      through; if it's broken, every remaining ticker would also fail to
      write, so letting prep/analyst keep running for them just burns
      model calls on output that can never land. Failing fast conserves
      the day's budget (the real concern decision #13 flags) instead of
      spending it on guaranteed waste.
    - **Logging**: extend the existing `update-log.md` per-run entry
      with an outcome per ticker — `updated` (sections written),
      `skipped-stale` (nothing due), `skipped-error` (prep or analyst
      failed — record which step and the exception message, not a full
      traceback), or `halted` (a writer failure ended the run early —
      record how many of N tickers completed first). This log is the
      after-the-fact review surface, not raw agent transcripts.

18. ~~**No dedicated Orchestrator subagent — revises decision #3.**~~
    **REVERTED 2026-07-21 — Adithya did not agree to this and explicitly
    overrode it.** Original reasoning kept below for the record, but it
    is no longer this project's direction:

    > Discovered while drafting the agent implementation plan:
    > `docs/architecture/claude_agent_skill_structure.md` states plainly
    > "No agent in this repo holds the Task tool" and documents the
    > existing convention instead — the *top-level* Claude Code session
    > dispatches multiple subagent invocations directly (see
    > `docs/architecture/decision_support_flow.md`'s "Multi-ticker/batch
    > runs": one top-level session fans out `stock-data-prep`+
    > `stock-analyst` per ticker in parallel, then makes one sequential
    > `kb-intake` call). Proposed swapping decision #3's dedicated
    > Orchestrator agent for a scheduled top-level prompt instead.

    **Correction**: decision #3 stands as originally written. The
    Orchestrator is a real agent holding the `Task` tool, invoked
    directly (manually, on demand) — not a scheduled top-level prompt.
    Scheduling it is explicitly deferred to a later decision, per
    Adithya: "Later we can think about running schedules." This makes
    the Orchestrator a first-of-its-kind exception to this repo's
    "no agent holds Task" convention — `claude_agent_skill_structure.md`
    needs a small update noting that exception once it's built. Four
    agents total, per the architecture table above (Orchestrator, Prep,
    Analyst, Writer) — not three.

## Open design questions

- **Required-fields schema.** What exactly must be populated per page/
  ticker before Consolidation can run — needs an explicit contract, like
  the old `front-matter-spec.md` + `thesis-contract.md`. Now includes:
  accurate `tickers:`/`tags:` tagging as a validated requirement (not
  optional), since discovery's recall depends entirely on it (see decision
  #8); and new page types/subfolders for peer/competitor and social
  content under `market-research/` (see decision #7). **Deliberately
  deferred** (Adithya's call) — figure this out once the agent rebuild is
  far enough along to know what's actually needed in practice, rather
  than designing it up front. Tracked in `docs/project/todo.md` Backlog.
  Not a blocker for `docs/plans/kb-population-agent-rebuild.md`.

All other previously-open items (staleness gate mechanics, unattended-mode
safety net behavior) are resolved — see decisions #16-17 above. Decision
#18 was proposed, then reverted — see its entry above. The
required-fields schema is deliberately deferred, not open — see above.

## Non-goals (for now)

- Not rebuilding `kb-discovery` in this pass — read-only search is a
  separate concern.
- Not committing to a rebuild sequence relative to the development
  roadmap's phases — scoped independent of that document per Adithya's
  direction.
- Not scheduling the Orchestrator agent yet (decision #18's reversal) —
  it runs manually/on-demand for now; automation is a later decision.

## Next steps

Design is settled (decisions #1-18, with #18 reverted back to #3's
original design). Next is building
`docs/plans/kb-population-agent-rebuild.md`, starting with its Step 0
prerequisites.
