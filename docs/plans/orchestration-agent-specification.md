# Orchestration Agent Specification

**Status:** Built — see [`docs/agents/investment-orchestrator/`](../agents/investment-orchestrator/) for the implemented agent and its design rationale. This document remains the original design spec.
**Priority:** High (highest-value item in backlog)  
**Context:** Identified in "Wishlist Track Post-Mortem" audit (finding F5)

---

## Problem Statement

The investment analyst and portfolio manager agents currently run independently, with no coordinating logic to:

1. **Determine scope dynamically** — runs used hardcoded `mode: data_pull` with `required_analysis: []`, and scope came from CLI flags the orchestrator chose rather than a recorded analysis request.
2. **Detect blockers up front** — every blocker the audited session hit was discoverable with read-only queries before token cost escalated. No preflight gate prevented downstream investment into a doomed run.
3. **Coordinate stage order** — no enforcement of the documented workflow sequence (`resources → security-status → security-technicals → build-worksheet → analyst → build-policy-context → portfolio-manager`).
4. **Track request intent** — no explicit record of what question the run is answering or what scope constraints apply.

The highest-value fix is a read-only preflight gate that reports blockers and halts—never remediates—so all waste is discovered before subagent invocations.

---

## Subagent Capabilities

What each of this spec's three agents (the two direct investment subagents,
plus the orchestrator itself) actually depends on — useful context for
reasoning about what the preflight gate below needs to guarantee before each
subagent runs.

| Agent | Skill | What it does |
| --- | --- | --- |
| `investment-analyst` | `security-status` | Resolves ticker portfolio status (owned/wishlist/avoid/retired/unknown) from DuckDB. Always invoked. |
| `investment-analyst` | `security-technicals` | Computes price technicals (SMAs, drawdown, volatility, relative strength, beta/alpha) from DuckDB history. Always invoked unless already present in the run. |
| `investment-analyst` | `investment-analyst-resources` | Builds the DB-first resource bundle for the ticker. Conditional — only invoked directly if no bundle exists yet for this run (see that agent's Handoffs table). |
| `investment-portfolio-manager` | `security-status` | Same shared skill; also determines owned vs. not-owned action vocabulary. Always invoked. |
| `investment-orchestrator` | `Task -> investment-analyst` | Fixed dispatch target; always invoked when the preflight gate passes. |
| `investment-orchestrator` | `Task -> investment-portfolio-manager` | Fixed dispatch target; invoked when requested and the analyst produced a thesis. |
| `investment-orchestrator` | `Bash -> analytics.py` read functions | Direct read-only preflight checks (ticker existence, price history, classification, status) — never via the skills above. |

## Orchestration Agent Design

### Model & Tools

- **Model:** Haiku
- **Tools:** Task, Bash, Read (no Write, no database-mutating commands)

The haiku model is appropriate because orchestration is mechanical—it gathers facts, checks gates, and reports findings without judgment. Read-only tools enforce that the agent surfaces blockers rather than fixing them.

### Workflow

1. **Accept a scope specification** — what ticker(s), mode (initial_research vs. revisit), time horizon, and which agents to invoke (analyst only, or analyst → portfolio manager).
2. **Run preflight checks** (read-only):
   - Does the ticker exist in the database?
   - Is the historical price data present and recent?
   - Is any required classification present (for portfolio manager)?
   - Can the thesis/policy worksheet be loaded if this is a revisit?
   - Are any blockers present in the security_status table (declared avoid/retired)?
3. **Report findings:**
   - If blockers exist, report them with remediations and **stop**—do not invoke subagents.
   - If all gates pass, proceed to invoke subagents.
4. **Invoke subagents in sequence** — analyst, then portfolio manager if requested:
   - Each receives the same `--run-id` so artifacts land in one run.
   - Never paraphrase artifacts for downstream agents; pass exact paths and document references.
   - Collect the run ID and final status from each invocation.
5. **Audit the run** — after all agents complete:
   - Confirm the run transitioned to the expected final state.
   - No remediation, just reporting.

### Guardrails

- **Read-only preflight only.** The orchestrator never runs investment-analyst-resources, security-status, security-technicals, or any skill that mutates state. All preparation is the run orchestrator's responsibility (or a human, via the CLI).
- **Never declare a security's status.** `security_status` writes (via `database status --set`) are human-only. The orchestrator reads status but never attempts to clear `avoid` or `retired` flags.
- **Never relay an unverified subagent claim.** If an agent's output looks suspicious (e.g., a thesis with no cited evidence, a decision with no reasoning), report it as a finding, not as success.
- **Never paraphrase an artifact into a subagent prompt.** Pass the actual path or hash. If a downstream agent needs a summary, that's an escalation, not a capability.
- **Fixed stage order.** The workflow is always `analyst → portfolio-manager` if both are requested. No conditional reordering.
- **One run per subject.** A subject receives one `--run-id` per orchestration invocation. No batching tickers into a single run; each ticker is independent.
- **Maintain the recorded question.** The scope specification (ticker, horizon, analysis mode, which agents) is written into the run's audit event, not computed from CLI flags. Future audits can see what question the run was meant to answer.

---

## Preflight Gate Details

The gate runs before any subagent is invoked. It is the answer to: "Can this run succeed without hitting a known blocker?"

### Checks (read-only)

1. **Ticker existence** — the ticker is registered in the `tickers` table.
2. **Price history minimum** — trailing 365+ days of closes exist from `price_history` (via `analytics.get_price_history`). Requirement: at least one price per week on average over the past year, so historical aggregations (moving averages, trend) are computable.
3. **Classification (whenever the analyst is requested — not only for portfolio manager)** — the ticker must already be classified (present in `portfolio_classifications` with `ownership_status` field set). This is required as soon as `investment-analyst` is in the requested agent list: that agent's own `build-worksheet` step treats a missing classification as a blocking evidence gap for a declared-wishlist subject, independent of whether a portfolio decision is ever requested afterward — a real incident (a BSX run) hit exactly this and blocked twice before recovering. The remediation is `python src/app.py classify` (the combined command that classifies **and** syncs the result into DuckDB) — **not** the read-only `portfolio-classifier` skill/agent alone, which only writes the JSON export and never touches the `portfolio_classifications` table this check and `build-worksheet` actually read. Presence in the table doesn't guarantee the row is current relative to the latest classification export — see the note at the end of this section.
4. **Status blockers** — if `security_status.declared_status` is `avoid` or `retired`, the run cannot proceed (thesis/decision are unobtainable). This is reported as a user-facing blocker, not silently skipped.
5. **Thesis/policy worksheet loadability (revisit mode only)** — if the mode is revisit (re-run analyst/PM on an existing run), confirm that the prior run's thesis and policy worksheet exist and are readable. This catches deleted runs or corrupt artifacts early.

### Output

**On gate pass:** "All checks passed. Proceeding to invoke [analyst|portfolio-manager]."

**On gate failure:** Report each failed check:

```
Ticker MP: Blocker — declared status is 'avoid'.
Remediation: Use `python src/app.py database status --ticker MP --set clear` to remove the avoid declaration.

Ticker OUST: Blocker — fewer than 180 days of price history.
Remediation: Price data for OUST is unavailable. Unable to compute technicals or market context.

Ticker BSX: Blocker — no classification record in portfolio_classifications for a declared-wishlist ticker.
Remediation: Run `python src/app.py classify` (NOT the read-only portfolio-classifier skill alone — it does not sync DuckDB).
```

Each remediation is actionable by a human (clear a flag, wait for data ingestion, run `classify`).

**Known gap, not implemented:** the classification check (item 3) verifies
*presence* only, not freshness relative to the latest classification export
— a ticker could in principle have a stale row instead of a missing one and
still pass. This wasn't the shape of the BSX incident (a missing row, not a
stale one) and is left for a future iteration rather than guessed at now.

---

## Dependency on Phases 0-3

**Status: satisfied.** Phases 0-3 are merged as of commits `cd3a1f2` ("Add
Phase 0-3 vocabulary/order-guidance track") and `76706d2` ("Wire
security-status/classify state through analytics, app CLI, and market-data
scope") on `Agent-Development`. `investment-orchestrator` was built on top of
this landed track — see
[`docs/agents/investment-orchestrator/`](../agents/investment-orchestrator/).

**Why this was the gate:** Phases 0-3 establish the contracts this agent orchestrates:

- **Phase 0** fixes the evidence-type collision that would mask missing inputs. The orchestrator's preflight checks assume technicals is either present and real or absent and declared—no silent substitution.
- **Phase 1** makes wishlist classification durable and first-class. The orchestrator assumes classification includes both owned and wishlist tickers, with `ownership_status` fields.
- **Phase 2** introduces the buy/watch/wait/pass vocabulary. The orchestrator relays this decision vocabulary to users; it must be stable before the orchestrator is built.
- **Phase 3** adds order guidance to the portfolio manager. The orchestrator must be aware of this advisory block so it can detect when a decision proposal lacks it (for a trade action) and surface it as a validation failure.

Building the orchestrator before these phases would have required guessing at the contracts or encoding fragile assumptions about artifact shape.

---

## Relationship to Conflict 6

**Conflict 6 (from merged plan):** The orchestration spec says the orchestrator must not paraphrase artifacts for subagents. Features B (Buy/Watch/Wait/Pass) and C (order guidance) make the PM's output richer, which increases the temptation to summarise.

**Resolution:** The orchestrator's no-paraphrase rule is carried forward explicitly in its design. If a downstream consumer (e.g., a human, a dashboard, a portfolio-construction tool) needs the decision in a different form, that is a separate layer—not the orchestrator's job. The orchestrator is pass-through: it invokes agents, collects artifacts, and reports findings. Summarization is a different responsibility.

---

## Success Criteria

When implemented, the orchestration agent should:

1. Accept a ticker (or list of tickers), analysis mode, time horizon, and agent list as input.
2. Run all preflight checks in under 5 seconds per ticker (pure read-only queries).
3. Report blockers with clear remediations before invoking subagents.
4. Invoke the analyst and/or portfolio manager in the documented order, passing the same run ID.
5. Collect and report each agent's output status without paraphrasing or summarizing.
6. Maintain an audit trail showing the scope specification and gate results.

---

## Backlog Items (F4, F6, pending)

Not part of the orchestration agent proper, but discovered during the post-mortem and live in Phase 4 backlog:

- **F4 / rec 06 — run lifecycle gap:** The analyst closes with `awaiting_human_review`; the PM only reads status from `created`, so it gets no signal when the analyst is done. Either add an `analysis_complete` status, or leave the run `in_progress` until the PM closes it.
- **F6 / rec 07 — duplicate status resolution:** `security-status` runs once per consuming agent. The second consumer should reuse the same-run artifact. (Downgraded to "minor waste" once Phase 0's type split lands; no longer a correctness bug.)

These are independent of the orchestrator but are part of the same audit trail and should be tackled together in Phase 4.
