# Investment Orchestrator Agent

This document is the human-readable companion to the Claude Code agent
defined at
[`.claude/agents/investment-orchestrator.md`](../../../.claude/agents/investment-orchestrator.md).
Design rationale is in [`plan.md`](plan.md).

## Purpose

The coordinating layer for the `investment-analyst` -> `investment-portfolio-manager`
workflow: a read-only preflight gate that catches every blocker discoverable
without fetching or writing anything, reported before any subagent is
invoked, followed by a fixed two-stage `Task` dispatch that shares one
`--run-id` across both agents. Built after a manual run against four
wishlist tickers hit exactly the kind of blocker (a missing classification
row) this gate exists to catch before token cost escalates — see `plan.md`
for the incident this agent formalizes a fix for.

## Runtime Settings

- `model: haiku` — orchestration here is mechanical: read a few DB rows,
  compare against thresholds, dispatch two agents in a fixed order. No
  investment judgment happens in this agent at all, matching `kb-orchestrator`'s
  same reasoning for the same model choice.
- `tools: ["Task", "Bash", "Read"]` — `Task` dispatches `investment-analyst`
  and `investment-portfolio-manager`, nothing else; `Bash` drives the
  preflight's read-only `analytics.py` calls and the `run create`/`run show`
  CLI stages; `Read` opens artifact paths to confirm they parse in revisit
  mode. **No `Write`** — this agent never produces an artifact of its own; a
  request YAML for `run create` is built via a `Bash` heredoc, not the
  `Write` tool.
- **No `skills:` field.** The preflight deliberately calls `src/analytics.py`
  functions directly (`get_ticker_id`, `resolve_security_status`,
  `get_price_history`, `get_classification`) rather than invoking the
  `security-status` skill or any other skill — see Guardrails in the agent
  definition for why: skills in this track fetch/write/register evidence,
  and this agent must never do any of that.
- **The second of two agents in this repo holding `Task`.** `kb-orchestrator`
  is the first, explicitly-authorized exception to the
  no-agent-holds-`Task` convention
  (`docs/architecture/claude_agent_skill_structure.md`). This agent is a
  second, separately-authorized exception — not a general precedent opening
  the door to a third.

## Inputs

- A scope specification: ticker(s), mode (`initial_research`/`revisit`),
  horizon, and which agent(s) to invoke.
- Read-only queries against the live pipeline database via
  `src/analytics.py`'s `get_ticker_id`, `resolve_security_status`,
  `get_price_history`, `get_classification` — no data-collection skill is
  ever invoked by this agent itself.
- In revisit mode, an existing run's evidence list (`run show`) and the
  thesis/policy-worksheet artifacts it references.

## Outputs

No artifact of its own. A structured **Orchestration Run Summary** (see the
agent definition's Output Format) reporting, per ticker: the gate result,
which subagents ran and their final status, the run_id, and the exact
artifact paths `investment-analyst`/`investment-portfolio-manager` produced.

It also writes into each run it creates: `run log-event` calls recording the
preflight pass, the dispatch outcome of each stage, and the final
orchestration summary — see "Audit trail" below.

## Audit trail

Before this agent's own logging, a run's `audit_log.jsonl` only ever recorded
what the specialist stages wrote (`evidence_registered`, `analyst_drafted`,
`pm_drafted`, `validated`, ...) — the coordinating layer's own actions (which
preflight checks passed, which stage got dispatched, the final report) lived
nowhere but the chat transcript, invisible to anyone reading the run later.
Step 9 of the workflow closes that gap: after the run is created (step 4),
right before and right after each dispatched stage (steps 5-6), and once the
final report is assembled (step 8), this agent calls
`uv run python src/app.py run log-event --run-id <run-id> --event <name> --actor investment-orchestrator ...`
(see `docs/reference/cli.md`'s `run log-event` section).

Event names follow the same terse `<noun>_<past-tense-verb>` convention
every other producer in `src/workspace/` already uses (`run_created`,
`manifest_built`, `cache_purged`, `pm_drafted`) rather than a per-run ad hoc
string, so a reader diffing two runs' timelines or `grep`-ing across
`workspace/runs/*/audit_log.jsonl` sees the same six names every time:

| Event | Logged | Meaning |
| --- | --- | --- |
| `preflight_passed` | Right after `run create` (step 4) | The gate cleared for this ticker; `details` lists which checks ran. |
| `analyst_dispatched` | Right before the `investment-analyst` `Task` call (step 5) | The stage started — present even if the run later crashes mid-dispatch. |
| `analyst_completed` | After confirming the outcome via `run show` (step 5) | `--status success` (a thesis was saved) or `--status insufficient_evidence`. |
| `pm_dispatched` | Right before the `investment-portfolio-manager` `Task` call (step 6) | Only logged when the stage actually runs. |
| `pm_completed` | After the stage finishes, or immediately if skipped (step 6) | `--status success` or `--status skipped` (with `skip_reason` in `details`), so the timeline shows the stage was *considered*, not silently absent. |
| `orchestration_completed` | Just before the final report is handed back (step 8) | Closes the timeline; `details` carries the run's final lifecycle status. |

Because every ticker that reaches step 4 gets its own run (see "One run per
subject" in Guardrails), this history is inherently split by stock — one
run's audit log holds only that ticker's orchestration story, nothing about
any other ticker in the same invocation, and the fixed event names make it
trivial to compare the same stage across many runs over time. A blocked
ticker never gets a run, so it has nothing to log into; its blocker stays
chat-level only. Logging is best-effort: a `log-event` failure is a note in
the report, never a reason to stop dispatching or reporting.

## The preflight gate

Five checks, all read-only, run before any `Task` call:

| Check | Source | Blocks |
| --- | --- | --- |
| Ticker existence | `analytics.get_ticker_id` | Unregistered symbol |
| Status | `analytics.resolve_security_status` | `avoid`/`retired` declaration |
| Price-history depth | `analytics.get_price_history` | Fewer than ~52 points over the trailing year |
| Classification presence | `analytics.get_classification` | No `portfolio_classifications` row, whenever `analyst` is requested |
| Revisit artifact loadability (revisit mode only) | `run show` + `Read` | Missing/corrupt prior thesis or policy worksheet |

The classification check is scoped to **whenever `investment-analyst` is
requested**, not only when a portfolio decision is also requested —
`investment-analyst`'s own `build-worksheet` step treats a missing
classification as a blocking evidence gap for a declared-wishlist subject,
independent of whether `investment-portfolio-manager` ever runs afterward.
See `plan.md` for the incident that surfaced this scoping error in the
original spec draft.

## What this agent does not do

- Does not score a thesis or propose a portfolio decision — both remain
  `investment-analyst`'s and `investment-portfolio-manager`'s jobs
  respectively.
- Does not fetch, refresh, or register evidence — `investment-analyst-resources`,
  `security-status`, `security-technicals` all stay the dispatched agents'
  own responsibility, invoked from inside their own workflows.
- Does not remediate a blocker it finds. It reports the exact command a
  human should run (e.g. `python src/app.py classify`) and stops — it never
  runs that command itself.
- Does not write to `Knowledge-Base/`.
- Does not batch multiple tickers into one shared run — one `--run-id` per
  ticker, matching the one-ticker-per-run design of the agents it dispatches.

## Guardrails

See the agent definition's Guardrails section for the full list. The two
most load-bearing: **read-only preflight only** (this agent never invokes a
mutating skill, even one of the very skills its dispatched subagents will go
on to call), and **`Task` use confined to the fixed two-agent sequence** —
not a precedent for any other agent in this repo to acquire `Task`.

## Handoffs

| Label | Action |
| --- | --- |
| Preflight blocker | Report it with the named remediation command and stop — a human must act (declare status, run `classify`, wait for price data) before retrying. |
| Analyst run ends `insufficient_evidence` | Report it; do not invoke `investment-portfolio-manager` (there is no thesis to size) until a human resolves the gap and reopens the run. |

## Code Location

`src/analytics.py` (`get_ticker_id`, `resolve_security_status`,
`get_price_history`, `get_classification`) holds the read functions the
preflight calls; `src/workspace/cli.py` (`run create`, `run show`,
`run log-event`) holds the run-management CLI stages this agent drives via
`Bash`. See
[`docs/plans/orchestration-agent-specification.md`](../../plans/orchestration-agent-specification.md)
for the original design spec and
[`docs/architecture/claude_agent_skill_structure.md`](../../architecture/claude_agent_skill_structure.md)
for the `Task`-holder convention this agent is a second exception to.
