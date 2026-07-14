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

The repo has `CLAUDE.md` and the `.claude/` tree with six real agents:
`portfolio-classifier` and `holdings-reconciliation` (each a single
deterministic report workflow), `kb-discovery` and `kb-intake` (read and
write sides of the research wiki), and the stock decision-support pipeline
`stock-data-prep` and `stock-analyst` (mechanical prep vs. rubric judgment;
see `docs/architecture/decision_support_flow.md`).

| Current file or folder | What it does now | Recommendation |
| --- | --- | --- |
| `instructions.md` | Legacy repo instructions kept as a compatibility note. | Prefer `CLAUDE.md` for active repo guidance. |
| `src/config.py` | Central non-secret static constants used by the app. | Keep as application config. |
| `src/email_extractor.py` | Loads email credentials from environment variables, fetches Wealthsimple and Interac emails, and normalizes extracted rows. | Good candidate for an `email-extraction` skill later. |
| `src/statement_extractor.py` | Extracts activity and glossary data from Wealthsimple statement PDFs. | Good candidate for a `statement-extraction` skill later. |
| `src/data_sorter.py` | Cleans Wealthsimple activity CSV exports and moves processed source files. | Good candidate for a data-pipeline subagent to inspect during refactors. |
| `src/system_logger.py` | Provides shared file-backed logging. | Mention in `CLAUDE.md` so future runtime code uses it. |
| `.claude/skills/classify-portfolio/scripts/` | Holds the classification workflow modules (`classification_workflow.py`, `portfolio_classifier.py`) used only by the classifier agent. | Keep agent-only Python beside its skill, not in `src/`. |
| `.claude/skills/reconcile-holdings-report/scripts/` | Holds `generate_reconciliation_report.py`, the deterministic markdown-report renderer used only by the `holdings-reconciliation` agent (see `docs/agents/holdings-reconciliation/`). Calls existing `src/holdings_reconciler.py`/`src/analytics.py` functions rather than reimplementing reconciliation logic. | Keep agent-only Python beside its skill, not in `src/`. |
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

No agent in this repo holds the `Task` tool, so agents cannot invoke each
other directly. When an agent's work is meant to continue in another agent
(e.g. `stock-data-prep` finishing a worksheet that `stock-analyst` must
score), document that as a `## Handoffs` section in the agent's body, placed
after `## Guardrails` and before `## Output Format`:

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
$env:PYTHONPATH='src'; python -m unittest tests.test_email_extractor tests.test_config
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
$env:PYTHONPATH='src'; python -m unittest tests.test_statement_extractor tests.test_config
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
