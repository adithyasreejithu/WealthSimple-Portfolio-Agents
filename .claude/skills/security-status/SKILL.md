---
name: security-status
description: Resolve one or more tickers' effective portfolio status -- owned, wishlist, avoid, retired, or unknown -- from stored DuckDB state, and write the result as a run-workspace calculation artifact with an evidence entry, audit event, and completeness trace. Shared, read-only skill invoked by both investment-analyst and investment-portfolio-manager before they judge or size a ticker. Never declares or changes a status.
model: haiku
---

# Security Status

Deterministic, no-judgment lookup. Reads stored ownership and any declared
non-ownership status, resolves one effective answer, writes an artifact. It
never writes a declaration and never decides an action.

**This is a shared skill.** Both `investment-analyst` and
`investment-portfolio-manager` invoke this exact skill -- there is no second
copy for either agent. A change here reaches both by construction; there is
nothing to keep in sync. See Guardrails for how the two callers stay
attributable in the audit trail despite sharing one implementation.

Run `uv run python .claude/skills/security-status/scripts/security_status_cli.py`:

- `--ticker TICKER [TICKER ...]` — one or more pipeline ticker symbols.
- `--actor {investment-analyst,investment-portfolio-manager}` — **required.**
  The calling agent, stamped into the evidence record and audit event.
- `--output PATH` — write the artifact here instead of into a run
  (single-ticker runs only).
- `--no-run` / `--run-id ID` — see below.
- `--no-trace` — skip the completeness trace.
- `--pretty` / `--no-pretty` — pretty-print the JSON digest (default: pretty).

## Workflow

1. Run the CLI for the ticker(s) in question, passing `--actor` as your own
   agent name.
2. Read the **stdout digest**. It carries the resolved `status`, the
   quantity held, and any declaration. This is what you report from.
3. **Do not read the artifact JSON.** The digest already contains every field
   in it; the artifact exists for the audit trail and downstream programmatic
   consumers.
4. Use the status as context for framing your output. It is never itself a
   recommendation — see Guardrails.

## When to invoke

- `investment-analyst`: after `run show`, before `build-worksheet` — the
  thesis should be framed against whether the subject is currently held.
- `investment-portfolio-manager`: before `build-policy-context` — a non-owned
  subject changes which actions are even legal (see Guardrails).
- Do **not** invoke it to declare or change a status. That is a human/CLI-only
  write path: `python src/app.py database status --ticker T --set
  wishlist|avoid|retired|clear`. No agent has write access to this table.

## Run workspace: default-on

Every invocation attaches to a run workspace, creating one if it does not
exist. The artifact lands in that run's **`calculations/`** directory — not
`evidence/` — because a resolved status is arithmetic over data the run
already reaches (ownership from `position_snapshots`, a declaration from
`security_status`), not a fact obtained from outside. It is registered in the
evidence registry as `security_status` (its own type, distinct from
`security_technicals` and `policy_worksheet` — the run's other two
`calculations/`-only producers), an audit event (`security_status_read`) is
appended with `actor` set to the caller, and the completeness trace is
written to `logs/SkillTrace.txt`/`.jsonl`.

- `--run-id ID` — name the run explicitly; attaches if it exists, creates it
  under that name if not. Passing the same ID to both agents' invocations
  groups them into one run — this is how the analyst and the PM end up
  citing status evidence from the same run.
- `--no-run` — opt out; the artifact goes to `--output` (or `exports/`). The
  opt-out is recorded in the trace's `workspace.skip_reason`.
- A non-default `--db-path` also skips attachment (the existing test/debug
  convention).

See `docs/architecture/run_workspace.md`.

## What it resolves

| Status | Meaning |
|---|---|
| `owned` | `position_snapshots.quantity > 0` for this ticker. Always wins over any declaration. |
| `wishlist` | Not owned; declared as a candidate to buy via `database status --set wishlist`. |
| `avoid` | Not owned; declared as a name to avoid. |
| `retired` | Not owned; a previously-relevant idea declared closed. |
| `unknown` | Not owned, and nothing declared. The default state for the vast majority of tickers — not a gap. |

Full field-by-field contract, including the trace-domain grading rules, is in
[status-contract.md](references/status-contract.md).

## Guardrails

- **Resolves and reports; never interprets.** "OUST is wishlist" is this
  skill's output. "OUST looks attractive" is not — that is the analyst's
  judgment.
- **Never a trade signal by itself.** The status is context for framing a
  thesis or a decision, not a reason to buy, sell, trim, or hold anything.
- **A non-owned subject may not receive `Trim` or `Sell`.** You cannot trim
  or sell a position you do not hold. `investment-portfolio-manager` enforces
  this as a deterministic validator check (`src/workspace/decision_validation.py`),
  not an agent instruction — read the status, but do not rely on your own
  judgment to catch this case.
- **Never writes a declaration.** `wishlist`/`avoid`/`retired` are set only
  via `python src/app.py database status`, run by a human. This skill is
  read-only end to end.
- **Ownership is always derived, never declared.** `security_status` never
  stores "owned" — see `database._create_security_status_table`'s docstring.
  If you find yourself wanting to declare a ticker "owned," that is a bug:
  ownership already comes from `position_snapshots`.
- **A shared skill, not a coincidence.** If this skill's behavior needs to
  change for one agent's workflow, it changes for both. There is no
  `investment-analyst`-specific or `investment-portfolio-manager`-specific
  variant to maintain separately.
- **Caller stamping, not code duplication, keeps this auditable.** `--actor`
  is required precisely so two agents reading the same ticker in the same run
  produce two distinct, attributable `sources.jsonl` rows and audit events
  from one implementation, rather than needing two copies of this skill to
  tell them apart.

## Dependencies

- `read-security-price-history` — the read-only DuckDB connect/validate/identity
  boundary (dependency-only skill; do not invoke it directly). Reused here
  exactly as `security-technicals` reuses it, rather than re-implementing a
  third copy of "how do I safely open this database."
- `src/analytics.py`'s `resolve_security_status` — the equivalent resolution
  logic for non-skill, writable-connection consumers (the worksheet builder,
  the dashboard). This skill does not call it directly: it needs a read-only
  connection, and `resolve_security_status` may trigger a position recompute.
  Both implement the same owned > declared > unknown precedence; keep them in
  agreement if that rule ever changes.
- `src/database_command.py`'s `set_security_status` — the sole write path,
  CLI/human-only.
