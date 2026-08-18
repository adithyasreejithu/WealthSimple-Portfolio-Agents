# Investment Orchestrator Agent — Design Rationale

Companion to [`architecture.md`](architecture.md). Records why this agent is
shaped the way it is, not just what it does.

## The incident this agent formalizes a fix for

A manual, human-driven run of `investment-analyst` -> `investment-portfolio-manager`
across four wishlist tickers (MP, WST, UBER, BSX) hit a real blocker on BSX:
its `build-worksheet` step failed with a missing `classification` evidence
domain. The `portfolio_classifications` table hadn't been synced since
before BSX was declared a wishlist candidate. Two things about that incident
directly shaped this agent:

1. **The fix took two retries to land**, because the first remediation
   attempt used the read-only `portfolio-classifier` skill/agent — which
   writes the classification JSON export but never syncs it into DuckDB.
   The actual fix needed `python src/app.py classify` (the combined
   export-and-sync command). This is exactly the kind of blocker a read-only
   preflight check, run *before* any subagent is invoked, would have caught
   immediately and named the correct remediation for on the first try —
   which is why the preflight failure examples in the agent definition name
   `classify` explicitly and warn against the read-only skill alone.
2. **The original spec draft scoped the classification check to
   "portfolio-manager-only.**" That was wrong: `investment-analyst`'s own
   `build-worksheet` step blocks on missing classification for a
   declared-wishlist subject, independent of whether a portfolio decision is
   ever requested afterward. An orchestrator built to the original scoping
   would have let an analyst-only request sail past preflight and still hit
   the blocker one stage later — no better than not having a gate at all.
   This agent's preflight (`architecture.md`'s table) fires whenever
   `analyst` is in the requested agent list, not gated on `portfolio-manager`.

## Why the preflight never invokes a skill, even a read-only one

The spec's guardrail — "never runs `investment-analyst-resources`,
`security-status`, `security-technicals`, or any skill that mutates state" —
reads at first like it should still permit invoking a skill that happens to
be read-only in a given mode (e.g. `security-status --no-run`). It
deliberately doesn't: `security-status --no-run` still writes a report file
to `exports/`, which is itself a side effect this agent has no business
producing. The preflight instead calls `analytics.resolve_security_status`
directly — the same underlying function the skill wraps — with zero file
output. This keeps the boundary bright-line simple: this agent's `Bash` use
is *always* read-only-and-side-effect-free, never "read-only in this
particular invocation."

## Why `Task` is a second exception, not a reopened door

`kb-orchestrator.md`'s guardrail states it is "not a precedent for granting
other agents `Task`." Building this agent with `Task` makes that literal
sentence describe a world that no longer exists — there are now two
Task-holding agents. This is treated as an intentional second,
separately-justified exception (the orchestration spec this agent
implements was written with that grant in mind), not a reinterpretation of
`kb-orchestrator`'s guardrail into a general allowance. Both agents' guardrail
sections say so explicitly, and `docs/architecture/claude_agent_skill_structure.md`
was updated in the same change to acknowledge the second exception without
weakening the "not a general precedent" framing for a hypothetical third.

## Why `model: haiku` and no independent judgment

Every check the preflight performs is a threshold comparison against a
value read straight from the database — there is no interpretation step
that would benefit from a stronger model. This mirrors `kb-orchestrator`'s
identical reasoning: dispatch logic and gate-checking are mechanical, so the
fast/cheap model is correct, and every point of actual judgment (the
thesis's fundamental rating, the portfolio decision's sizing) stays with the
dispatched `investment-analyst` (Opus) and `investment-portfolio-manager`
(Sonnet) agents, unchanged by this agent's existence.

## What was deliberately left out of the first version

- **Classification freshness drift.** The preflight checks *presence* in
  `portfolio_classifications`, not whether that row is current relative to
  the latest classification export. A ticker could in principle have a
  stale row instead of a missing one and pass the gate anyway. This wasn't
  observed in the BSX incident (which was a missing-row case) and adding a
  freshness check would require deciding what "current enough" means without
  a concrete failure to design against — left as a known gap for a future
  iteration rather than guessed at now.
- **Batched multi-ticker runs.** The spec and this agent both keep one
  `--run-id` per ticker. A future version could investigate whether a
  portfolio-wide sweep (many tickers, one orchestration invocation, parallel
  dispatch) is worth building, but that's a different concurrency model than
  today's fixed sequential dispatch and wasn't part of the incident that
  motivated this agent.
