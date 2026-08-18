# Claude Agent and Skill Structure

This document explains the recommended Claude Code structure for this repo. It shows where repo instructions, custom agents, and reusable skills should live, and how agent-owned code and documentation are organized.

## Recommended Schema

Use this target layout for Claude Code files:

```text
WealthSimple-Portfolio-Agents/
  CLAUDE.md
  .claude/
    agents/
      portfolio-classifier.md
      reviewer.md
      data_pipeline.md
    skills/
      classify-portfolio/
        SKILL.md
        references/
        scripts/
      email-extraction/
        SKILL.md
        scripts/
      statement-extraction/
        SKILL.md
        scripts/
  docs/
    architecture/
      claude_agent_skill_structure.md
    agents/
      portfolio-classifier/
        architecture.md
        plan.md
  src/
    config.py
    data_sorter.py
    email_extractor.py
    statement_extractor.py
    system_logger.py
  tests/
    test_config.py
    test_email_extractor.py
    test_statement_extractor.py
```

Claude Code uses these folders for different jobs:

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Repo-level instructions Claude Code auto-loads while working here. Contains the canonical guidance for this repository. |
| `.claude/agents/*.md` | Custom subagent role definitions for focused work such as classification, review, or data-pipeline analysis. |
| `.claude/skills/*/SKILL.md` | Repo-scoped skills that package reusable Claude Code workflows, plus their `scripts/` and `references/`. |
| `.claude/skills/*/scripts/*.py` | Python used exclusively by a skill lives beside that skill, not in `src/`. |
| `docs/agents/<agent>/` | Human-readable, per-agent documentation (architecture and design notes) grouped in one folder. |
| `docs/` | Human-readable planning, architecture, criteria, and handover notes. |
| `src/` | Active Python application code that is not owned by a single agent/skill. |
| `tests/` | Unit tests and regression coverage for the source modules. |

## Current Repo Mapping

The repo has `CLAUDE.md` and the `.claude/agents/` tree with eight real
agents: `portfolio-classifier` (a single deterministic report workflow),
`kb-intake` and `kb-orchestrator` (writing/orchestrating the research wiki),
the stock decision-support pipeline `stock-data-prep` and `stock-analyst`
(mechanical prep vs. rubric judgment; see
`docs/architecture/decision_support_flow.md`), and the rebuilt Investment
Analyst track's `investment-analyst`, `investment-portfolio-manager`, and
`investment-orchestrator` (fundamental judgment vs. portfolio-sizing
judgment vs. read-only preflight-and-dispatch coordination, the first two
sharing the read-only `security-status` skill between them; see
`docs/agents/investment-analyst/`, `docs/agents/investment-portfolio-manager/`,
and `docs/agents/investment-orchestrator/`). `investment-orchestrator` and
`kb-orchestrator` are the only two agents in this repo authorized to hold
`Task`, each an explicit, narrowly-scoped exception -- not a precedent for
any other agent to acquire it (see their own Guardrails sections).
`holdings-reconciliation` and
`kb-discovery`, present in earlier revisions of this document, no longer
have a `.claude/agents/*.md` file on disk (both archived 2026-07-16 and
never restored, unlike `kb-intake`/`stock-analyst`/`stock-data-prep`/
`portfolio-classifier`/`author-decision-rubric` from that same archive,
which were).

| Current file or folder | What it does now | Recommendation |
| --- | --- | --- |
| `instructions.md` | Legacy repo instructions kept as a compatibility note. | Prefer `CLAUDE.md` for active repo guidance. |
| `src/config.py` | Central non-secret static constants used by the app. | Keep as application config. |
| `src/email_extractor.py` | Loads email credentials from environment variables, fetches Wealthsimple and Interac emails, and normalizes extracted rows. | Good candidate for an `email-extraction` skill later. |
| `src/statement_extractor.py` | Extracts activity and glossary data from Wealthsimple statement PDFs. | Good candidate for a `statement-extraction` skill later. |
| `src/data_sorter.py` | Cleans Wealthsimple activity CSV exports and moves processed source files. | Good candidate for a data-pipeline subagent to inspect during refactors. |
| `src/system_logger.py` | Provides shared file-backed logging. | Mention in `CLAUDE.md` so future runtime code uses it. |
| `.claude/skills/classify-portfolio/scripts/` | Holds the classification workflow modules (`classification_workflow.py`, `portfolio_classifier.py`) used only by the classifier agent. | Keep agent-only Python beside its skill, not in `src/`. |
| `.claude/skills/security-technicals/scripts/` | Holds `security_technicals_cli.py`, the agent-invoked orchestrator for per-security price technicals. Imports the pure math from `src/security_technicals.py` rather than vendoring it, because that math has non-skill consumers too. | A skill may wrap `src/` code it does not own; the wrapper (CLI, run attachment, evidence/audit/trace) is what belongs beside the skill. |
| `.claude/skills/read-security-price-history/scripts/` | Holds `read_price_history.py`, the read-only DuckDB boundary for the security-technicals workflow, including the repository's canonical benchmark price-series reader. | Dependency-only skill; same shape as `read-portfolio-classification-data`. |
| `.claude/skills/security-status/scripts/` | Holds `security_status_cli.py`, the read-only orchestrator that resolves a ticker's owned/wishlist/avoid/retired status. Reuses `read-security-price-history`'s connect/validate/identity boundary rather than re-implementing it a third time. One shared skill invoked, unchanged, by both `investment-analyst` and `investment-portfolio-manager` -- caller-stamped via a required `--actor`, not duplicated. | A skill genuinely shared by two agents stays a single directory; auditability comes from stamping the caller into every evidence/audit record, not from maintaining a copy per consumer. |
| `tests/` | Verifies config, extraction, and classification behavior. | Mention the test command in `CLAUDE.md`. |
| `.env` | Local runtime secrets and environment-specific values. | Do not move secrets into `CLAUDE.md` or `src/config.py`. |

## File Responsibilities

### `CLAUDE.md`

`CLAUDE.md` holds durable repo guidance: project layout, coding conventions, test commands, security boundaries, and what counts as done. Claude Code auto-loads this file.

Do include in `CLAUDE.md`:

- Repo purpose and important folders.
- Commands for tests and common checks.
- Python style and logging rules.
- Data-handling rules for `Data/`, `exports/`, and `logs/`.
- Reminder that secrets stay in `.env` or environment variables.

Do not include:

- API keys, passwords, access tokens, or account-specific credentials.
- Large task plans that belong in `docs/`.

### `.claude/agents/*.md`

Use custom agent files for reusable subagent roles. Each agent file is a markdown file with YAML front matter followed by a system-prompt body. Keep each agent narrow and role-specific.

Front matter fields:

| Field | Required | Purpose |
| --- | --- | --- |
| `name` | Yes | Agent identifier (lowercase, hyphens). |
| `description` | Yes | When Claude should dispatch this agent, including trigger phrasing. |
| `model` | Yes | `inherit`, `haiku`, `sonnet`, or `opus`. |
| `color` | Yes | Visual identifier in the UI. |
| `tools` | No | Array restricting the agent to specific tools (least privilege). |
| `skills` | No | Array of skill names this agent depends on — dependency documentation only, not enforced by Claude Code. |

Example `.claude/agents/reviewer.md`:

```markdown
---
name: reviewer
description: Use this agent to review repo changes for correctness, security, regressions, and missing tests. Typical triggers include a request to review a diff or check whether a refactor preserved behavior.
model: sonnet
color: yellow
tools: ["Read", "Grep", "Glob"]
---

You review changes like an owner of this repo.

Prioritize correctness, security, behavioral regressions, and missing tests.
Check Python modules under src/ and tests under tests/.
Reference exact files and line numbers in findings.
Do not rewrite code unless the main agent explicitly asks you to implement a fix.
```

Example `.claude/agents/data_pipeline.md`:

```markdown
---
name: data-pipeline
description: Use this agent to inspect Wealthsimple extraction, transformation, file movement, and export flows. Typical triggers include auditing data-loss risks or reviewing a pipeline refactor.
model: sonnet
color: green
tools: ["Read", "Grep", "Glob"]
---

You focus on the repo's data pipeline behavior.

Inspect src/email_extractor.py, src/statement_extractor.py, src/data_sorter.py, and src/config.py.
Look for broken assumptions, data loss risks, weak validation, and missing tests.
Treat Data/, exports/, logs/, and .env as sensitive local artifacts.
Return a concise summary with risks, affected files, and recommended tests.
```

### Handoffs between agents

With one deliberate exception (`kb-orchestrator`, below), no agent in this
repo holds the `Task` tool, so agents cannot invoke each other directly. When
an agent's work is meant to continue in another agent (e.g. `stock-data-prep`
finishing a worksheet that `stock-analyst` must score), document that as a
`## Handoffs` section in the agent's body, placed after `## Guardrails` and
before `## Output Format`:

| Label | Agent | Prompt |
| --- | --- | --- |
| Short description of the trigger | target-agent-name | Exact prompt text to send when invoking it |

This is the single canonical place for cross-agent handoff instructions --
don't duplicate the same "call X next" guidance in `When to invoke`,
`Workflow`, or `Output Format`; point back to `Handoffs` instead. The table
is prose read by the orchestrating Claude session or the user, not an
executable mechanism. Agents with no downstream handoff should state that
explicitly (e.g. "None -- this agent's output is terminal.") rather than
omitting the section, so its absence is never ambiguous with an oversight.

**The `Task`-holder exception: `kb-orchestrator` and `investment-orchestrator`.**
The KB population workflow (`docs/plans/kb-population-agent-rebuild.md`)
needs one agent that runs a fixed fetch → score → commit sequence per due
ticker on demand, rather than relying on a human or a scheduled top-level
prompt to drive the fan-out. That agent, `.claude/agents/kb-orchestrator.md`,
was granted `Task` deliberately and was the first agent in this repo to hold
it. Its `Task` use is confined to dispatching the fixed sequence
(`stock-data-prep`, `stock-analyst`, one `kb-intake`); it carries no
judgment of its own. `.claude/agents/investment-orchestrator.md`
(`docs/agents/investment-orchestrator/`) is a second, independently-justified
exception for its own fixed `investment-analyst` → `investment-portfolio-manager`
dispatch sequence, built after a manual run surfaced blockers a read-only
preflight gate could catch earlier — see that agent's `plan.md`. These are
narrow, explicitly-authorized exceptions (Adithya's direction in both cases),
**not** a precedent for granting `Task` to any other agent — the
Handoffs-table convention above remains the default for every other agent.

### `.claude/skills/*/SKILL.md`

Use skills for reusable workflows. A skill is a directory with a `SKILL.md` file and optional `scripts/` or `references/` files. Claude Code auto-discovers any subdirectory of `.claude/skills/` that contains a `SKILL.md`.

Skills are a good fit when you repeatedly ask Claude to do the same kind of work, such as:

- Add support for a new Wealthsimple email format.
- Update PDF statement extraction rules.
- Review whether a refactor preserved current output columns.
- Build a new export workflow with tests.

Required `SKILL.md` front matter:

| Field | Purpose |
| --- | --- |
| `name` | Stable skill name. |
| `description` | Third-person trigger condition that tells Claude when to use the skill. |

Python that only the skill uses lives in that skill's `scripts/` directory. For example, the `classify-portfolio` skill keeps `classification_workflow.py` and `portfolio_classifier.py` in its own `scripts/` folder; the dependency skills import that shared module by adding the folder to `sys.path`.

#### Two kinds of skill

- **Agent-invoked** (`classify-portfolio`, `investment-analyst-resources`, `security-technicals`, most of them): Claude reads the `SKILL.md` and follows its workflow, running the scripts via `Bash`.
- **Dependency-only** (`read-portfolio-classification-data`, `fetch-yfinance-classification-data`, `read-security-price-history`): never invoked directly. Their `SKILL.md` is a few lines whose `description` ends "Use only as an internal dependency of X, never for arbitrary SQL or database paths", and a `references/*-contract.md` states the executable boundary. Another skill's scripts import them via `sys.path`.

#### Skill-owned code vs. wrapped `src/` code

"Python used exclusively by a skill lives beside that skill" is about **ownership**, not about whether a skill exists. A skill may wrap `src/` code it does not own, and should when that code has other consumers:

- `security-technicals` imports `src/security_technicals.py`. The math has non-skill consumers (the Phase 3 worksheet builder, potentially a dashboard route), so it stays in `src/` — while the CLI, run-workspace attachment, evidence/audit registration, and completeness trace, which only this skill needs, live in the skill.
- `security-status` does the same, and adds a second wrinkle: it is shared by *two agents* (`investment-analyst`, `investment-portfolio-manager`). It still stays one skill directory, not two — the resolution logic (`analytics.resolve_security_status`, for writable-connection, non-skill callers like the worksheet builder) lives in `src/`, and the read-only CLI/evidence/trace wrapper both agents invoke lives in the skill.

The invariant to preserve: **skills import `src/`; `src/` never imports `.claude/`.** Moving multi-consumer math into a skill would force a `src/` module to import from `.claude/`, which is the dependency direction this layout exists to prevent. See `docs/plans/implementation/phase-2/design-decisions.md` (Decision 4) for a worked example.

Example `.claude/skills/email-extraction/SKILL.md`:

````md
---
name: email-extraction
description: This skill should be used when modifying Wealthsimple or Interac email parsing, mailbox filtering, output columns, or email transaction exports in this repo.
---

# Email Extraction Skill

Use this skill for changes involving `src/email_extractor.py`, email output columns, sender matching, subject matching, or email CSV export behavior.

## Context To Read

- `src/config.py`
- `src/email_extractor.py`
- `tests/test_email_extractor.py`
- `tests/test_config.py`

## Workflow

1. Confirm whether the change affects parsing, mailbox filtering, output shape, or exports.
2. Keep credentials in environment variables only.
3. Keep shared static values in `src/config.py`.
4. Preserve `OUTPUT_COLUMNS` compatibility unless the task explicitly changes the output contract.
5. Add or update focused tests for parser behavior and edge cases.
6. Run:

```powershell
uv run python -m unittest tests.test_email_extractor tests.test_config
```

## Completion Criteria

- Existing email extraction tests pass.
- New or changed parser behavior is covered by tests.
- No secrets or private email content are logged or committed.
````

Example `.claude/skills/statement-extraction/SKILL.md`:

````md
---
name: statement-extraction
description: This skill should be used when modifying Wealthsimple PDF statement parsing, activity row extraction, glossary extraction, output columns, or statement transaction exports in this repo.
---

# Statement Extraction Skill

Use this skill for changes involving `src/statement_extractor.py`, PDF activity parsing, glossary extraction, or statement export behavior.

## Context To Read

- `src/config.py`
- `src/statement_extractor.py`
- `tests/test_statement_extractor.py`
- `tests/test_config.py`

## Workflow

1. Identify whether the change affects page detection, row merging, field parsing, cleaning, glossary extraction, or exports.
2. Keep output columns stable unless the task explicitly changes them.
3. Put reusable non-secret static values in `src/config.py`.
4. Add focused tests for any new statement format or parser edge case.
5. Run:

```powershell
uv run python -m unittest tests.test_statement_extractor tests.test_config
```

## Completion Criteria

- Existing statement extraction tests pass.
- New parser behavior has test coverage.
- File movement, export, and logging behavior remain explicit.
````

### `docs/agents/<agent>/*.md`

Group per-agent documentation in one folder named for the agent. For the classifier, `docs/agents/portfolio-classifier/` holds:

- `architecture.md` — the human-readable companion to the agent definition: purpose, runtime settings, skill dependencies, workflow, and guardrails.
- `plan.md` — the original design rationale and acceptance criteria.

Keep these aligned with the actual agent config under `.claude/agents/` and the skills under `.claude/skills/`.

## Practical Adoption Order

Use this order when adding new agent tooling later:

1. Keep `CLAUDE.md` current with repo-wide guidance.
2. Add `.claude/skills/*/SKILL.md` for workflows you expect to reuse; keep skill-only Python in the skill's `scripts/`.
3. Add `.claude/agents/*.md` once you have a repeated subagent role.
4. Add a `docs/agents/<agent>/` folder for each agent's human-readable docs.
5. Keep this doc as the reference schema and update it when the repo's Claude Code setup changes.
