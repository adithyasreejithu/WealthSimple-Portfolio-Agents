# KB Orchestrator Agent

This document is the human-readable companion to the Claude Code agent defined
at [`.claude/agents/kb-orchestrator.md`](../../../.claude/agents/kb-orchestrator.md).
Design rationale is in
[`docs/plans/kb-population-agent-rebuild.md`](../../plans/kb-population-agent-rebuild.md)
and [`docs/plans/kb-intake-rebuild-scope.md`](../../plans/kb-intake-rebuild-scope.md)
(decisions #3, #16, #17).

## Purpose

Runs the KB population workflow as a fixed sequence: compute which owned tickers
are due for a refresh, fan out `stock-data-prep` + `stock-analyst` per due
ticker, then make one sequential `kb-intake` call to commit the results. It
carries no judgment of its own — it dispatches other agents in order and
summarizes the outcome. It is invoked manually/on-demand for now; scheduling is
a deliberately deferred, separate decision.

## Runtime Settings

- `model: haiku` — like `stock-data-prep`, this agent makes no judgment calls;
  it runs a fixed sequence and reports. The judgment lives in the agents it
  dispatches (`stock-analyst` on Opus, `kb-intake` on Sonnet).
- `tools: ["Task", "Bash", "Read"]` — `Bash` runs the staleness-gate skill;
  `Read` inspects gate output / artifacts as needed; **`Task`** dispatches the
  prep/analyst/intake agents.

## The `Task` exception

This is the **only** agent in this repo that holds `Task`. The repo's default
convention (`docs/architecture/claude_agent_skill_structure.md`) is that no
agent holds `Task` and the top-level session does any fan-out. The KB
population workflow needs a single agent to drive the fixed per-ticker sequence
on demand, so `kb-orchestrator` is granted `Task` as a narrow,
explicitly-authorized exception (Adithya's direction — an agent should run
this, not a scheduled top-level prompt). It is **not** a precedent for giving
other agents `Task`. The grant is documented inline in the agent's Guardrails
and in the structure doc's "Handoffs between agents" section.

## Skill Dependencies

```yaml
skills:
  - kb-staleness-gate
```

| Skill | Role |
|---|---|
| `kb-staleness-gate` | Compute the due-ticker list (`kb-staleness-gate.v1` JSON), with `--dry-run` / `--force`. |

## Workflow

1. Run `kb-staleness-gate` via `Bash` (`--dry-run` first when testing, `--force`
   for a targeted subset). Parse the `kb-staleness-gate.v1` JSON. Empty due set →
   report "nothing due" and stop.
2. Per due ticker, dispatch `stock-data-prep` then `stock-analyst` via `Task`,
   passing `first_run` and `due_sections` from the gate. Equity and ETF batches
   run concurrently; the **ETF batch's analysts** take a `model: sonnet`
   override.
3. Wait for every recommendation artifact. A prep/analyst failure on one ticker
   is isolated and recorded as `skipped-error` (decision #17) — it never aborts
   the run.
4. One single `kb-intake` `Task` call loops `ingest_recommendation.py` over all
   artifacts sequentially (the wiki index/log helpers are not concurrency-safe).
5. If `kb-intake` reports a halt (a commit failed partway), stop — do not retry
   the rest — and report how many of N committed and which ticker failed
   (decision #17).

## Guardrails

- No judgment: never scores, writes narratives, or writes to `Knowledge-Base/`.
- `Task` use is confined to the fixed sequence above — no recursive dispatch, no
  invented targets, no retry-through-another-agent.
- Prep/analyst failures are skipped per-ticker; a writer (`kb-intake`) halt ends
  the whole run. These are decision #17 and are not interchangeable.
- Manual/on-demand only — never wired into `schedule`/`cron`.

## Handoffs

Dispatched (via `Task`), not handed off: `stock-data-prep`, `stock-analyst`,
`kb-intake`. This agent is the top of the KB-population chain; its output (the
run summary) is terminal.

## Code Location

No logic beyond the fixed sequence in the agent body. The staleness computation
lives in `.claude/skills/kb-staleness-gate/scripts/staleness_gate.py`; the
per-ticker work lives in the dispatched agents and their skills. See
[`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md)
for the underlying fan-out pattern.
