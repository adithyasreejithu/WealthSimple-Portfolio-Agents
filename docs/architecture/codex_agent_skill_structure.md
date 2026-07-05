# Codex Agent and Skill Structure

This document explains the recommended Codex structure for this repo. It shows where repo instructions, Codex configuration, custom agents, and reusable skills should live when the project is ready to add them.

## Recommended Schema

Use this target layout for Codex-specific files:

```text
WealthSimple-Portfolio-Agents/
  AGENTS.md
  .codex/
    config.toml
    agents/
      reviewer.toml
      data_pipeline.toml
  .agents/
    skills/
      email-extraction/
        SKILL.md
        scripts/
      statement-extraction/
        SKILL.md
        scripts/
  docs/
    codex_agent_skill_structure.md
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

Codex uses these folders for different jobs:

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Repo-level instructions Codex should follow automatically while working here. |
| `.codex/config.toml` | Project-specific Codex settings such as model, sandbox, approval policy, and subagent limits. |
| `.codex/agents/*.toml` | Optional custom subagent role definitions for focused work such as review or data-pipeline analysis. |
| `.agents/skills/*/SKILL.md` | Repo-scoped skills that package reusable Codex workflows. |
| `docs/` | Human-readable planning, architecture, criteria, and handover notes. |
| `src/` | Active Python application code. |
| `tests/` | Unit tests and regression coverage for the source modules. |

## Current Repo Mapping

The current repo already has most project folders, but it does not yet have `AGENTS.md`, `.codex/`, or `.agents/skills/`.

| Current file or folder | What it does now | Codex recommendation |
| --- | --- | --- |
| `instructions.md` | Current repo instructions for implementation, logging, data handling, verification, and comments. | Keep it for humans or migrate the durable Codex-facing parts into `AGENTS.md`. |
| `src/config.py` | Central non-secret static constants used by the app. | Keep as application config, not Codex config. |
| `src/email_extractor.py` | Loads email credentials from environment variables, fetches Wealthsimple and Interac emails, and normalizes extracted rows. | Good candidate for an `email-extraction` skill later. |
| `src/statement_extractor.py` | Extracts activity and glossary data from Wealthsimple statement PDFs. | Good candidate for a `statement-extraction` skill later. |
| `src/data_sorter.py` | Cleans Wealthsimple activity CSV exports and moves processed source files. | Good candidate for a data-pipeline subagent to inspect during refactors. |
| `src/system_logger.py` | Provides shared file-backed logging. | Mention in `AGENTS.md` so future runtime code uses it. |
| `tests/` | Verifies config, email extraction, and statement extraction behavior. | Mention the test command in `AGENTS.md`. |
| `.env` | Local runtime secrets and environment-specific values. | Do not move secrets into `AGENTS.md`, `.codex/config.toml`, or `src/config.py`. |

## File Responsibilities

### `AGENTS.md`

Use `AGENTS.md` for durable repo guidance. This is where Codex should learn the project layout, coding conventions, test commands, security boundaries, and what counts as done.

Do include:

- Repo purpose and important folders.
- Commands for tests and common checks.
- Python style and logging rules.
- Data-handling rules for `Data/`, `exports/`, and `logs/`.
- Reminder that secrets stay in `.env` or environment variables.

Do not include:

- API keys, passwords, access tokens, or account-specific credentials.
- Large task plans that belong in `docs/`.
- Codex runtime settings that belong in `.codex/config.toml`.

Example `AGENTS.md`:

````md
# WealthSimple Portfolio Agents Instructions

## Purpose

This repo contains Python tools for extracting, normalizing, and preparing Wealthsimple portfolio data from emails, PDF statements, and CSV exports.

## Project Layout

- `src/` contains active Python application code.
- `src/config.py` contains non-secret static constants shared by modules.
- `tests/` contains unit and regression tests.
- `docs/` contains plans, notes, criteria, and handover material.
- `Data/` contains local input and processed data artifacts.
- `exports/` contains generated CSV exports.
- `logs/` contains runtime logs.

## Implementation Rules

- Keep changes scoped to the requested task.
- Preserve existing behavior unless the task explicitly asks for a change.
- Put shared non-secret constants in `src/config.py`.
- Keep secrets and environment-specific values out of `src/config.py`.
- Use `src/system_logger.py` for runtime logging.
- Validate input assumptions before processing files or data.

## Verification

Run tests with:

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

For narrow documentation-only changes, a readback check is enough.

## Security

- Do not commit `.env` values, API keys, app passwords, or account credentials.
- Treat Wealthsimple, Gmail, Interac, statement, and portfolio data as sensitive.
- Avoid logging raw secrets or full private input payloads.
````

### `.codex/config.toml`

Use `.codex/config.toml` for Codex behavior in this repo. This is not application config. It should not contain Wealthsimple, Gmail, OpenAI, or other private credentials.

Example `.codex/config.toml`:

```toml
# Project-scoped Codex settings for WealthSimple-Portfolio-Agents.

model = "gpt-5.5"
model_reasoning_effort = "medium"
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[agents]
max_threads = 4
max_depth = 1
job_max_runtime_seconds = 1800

[agents.reviewer]
description = "Review Python code for correctness, security, regressions, and missing tests."
config_file = "./agents/reviewer.toml"

[agents.data_pipeline]
description = "Analyze extraction, normalization, and data movement flows in this repo."
config_file = "./agents/data_pipeline.toml"
```

### `.codex/agents/*.toml`

Use custom agent files when you want reusable subagent roles. Each custom agent file should be narrow and role-specific.

Required fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `name` | string | Agent name Codex uses when spawning or referring to the role. |
| `description` | string | When this agent should be used. |
| `developer_instructions` | string | The agent's durable role instructions. |

Optional fields include `nickname_candidates`, `model`, `model_reasoning_effort`, `sandbox_mode`, and other supported Codex config keys.

Example `.codex/agents/reviewer.toml`:

```toml
name = "reviewer"
description = "Review repo changes for correctness, security, regressions, and missing tests."
model = "gpt-5.5"
model_reasoning_effort = "high"
nickname_candidates = ["Atlas", "Delta", "Echo"]

developer_instructions = """
Review changes like an owner of this repo.
Prioritize correctness, security, behavioral regressions, and missing tests.
Check Python modules under src/ and tests under tests/.
Reference exact files and line numbers in findings.
Do not rewrite code unless the main agent explicitly asks you to implement a fix.
"""
```

Example `.codex/agents/data_pipeline.toml`:

```toml
name = "data_pipeline"
description = "Inspect Wealthsimple extraction, transformation, file movement, and export flows."
model = "gpt-5.5"
model_reasoning_effort = "high"

developer_instructions = """
Focus on the repo's data pipeline behavior.
Inspect src/email_extractor.py, src/statement_extractor.py, src/data_sorter.py, and src/config.py.
Look for broken assumptions, data loss risks, weak validation, and missing tests.
Treat Data/, exports/, logs/, and .env as sensitive local artifacts.
Return a concise summary with risks, affected files, and recommended tests.
"""
```

### `.agents/skills/*/SKILL.md`

Use skills for reusable workflows. A skill is a directory with a `SKILL.md` file and optional `scripts/` or reference files.

Skills are a good fit when you repeatedly ask Codex to do the same kind of work, such as:

- Add support for a new Wealthsimple email format.
- Update PDF statement extraction rules.
- Review whether a refactor preserved current output columns.
- Build a new export workflow with tests.

Required `SKILL.md` frontmatter:

| Field | Purpose |
| --- | --- |
| `name` | Stable skill name. |
| `description` | Clear trigger condition that tells Codex when to use the skill. |

Example `.agents/skills/email-extraction/SKILL.md`:

````md
---
name: email-extraction
description: Use when modifying Wealthsimple or Interac email parsing, mailbox filtering, output columns, or email transaction exports in this repo.
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

Example `.agents/skills/statement-extraction/SKILL.md`:

````md
---
name: statement-extraction
description: Use when modifying Wealthsimple PDF statement parsing, activity row extraction, glossary extraction, output columns, or statement transaction exports in this repo.
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

## Practical Adoption Order

Use this order if you decide to add these files later:

1. Create `AGENTS.md` from the current `instructions.md` rules.
2. Add `.codex/config.toml` only when you want repo-specific Codex settings.
3. Add `.codex/agents/*.toml` only after you have repeated subagent workflows.
4. Add `.agents/skills/*/SKILL.md` only for workflows you expect to reuse.
5. Keep this docs file as the reference schema and update it when the repo's Codex setup changes.
