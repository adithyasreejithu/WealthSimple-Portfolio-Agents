# Claude Code Instructions

This file is the canonical repo-wide context for implementation work in this repository.

## Project Structure & Module Organization

This repository is a Python data pipeline for Wealthsimple exports, statements, and email extraction.

- `src/` contains the application code, including pipeline orchestration (`app.py`), extraction logic (`email_extractor.py`, `statement_extractor.py`, `yfinance_extractor.py`), storage (`database.py`, `database_command.py`), and utilities (`data_sorter.py`, `staging.py`, `system_logger.py`).
- `tests/` contains `unittest` test cases that mirror the main modules.
- `docs/` contains design notes, handover material, and success criteria.
- `requirements.txt` lists runtime dependencies.

## Agent and Skill Documentation

Claude Code agents live in `.claude/agents/*.md` and skills live in `.claude/skills/*/SKILL.md`. When adding or updating an agent, also keep its human-readable documentation under `docs/agents/<agent>/`.

- Define each agent as a markdown file with YAML front matter (`name`, `description`, `model`, `color`, and an optional least-privilege `tools` array) plus a system-prompt body.
- Keep reusable workflows as skills in `.claude/skills/`, each a directory with a `SKILL.md` and optional `scripts/` and `references/`.
- Put Python that is used only by one agent/skill beside that skill in its `scripts/` directory, not in `src/`. `src/` is for general pipeline code that is not owned by a single agent. For example, the classifier workflow modules live in `.claude/skills/classify-portfolio/scripts/`.
- Document each agent's purpose, runtime settings, skill dependencies, workflow, and guardrails under `docs/agents/<agent>/`, and keep it aligned with the actual agent config and skills.
- See `docs/architecture/claude_agent_skill_structure.md` for the full recommended layout.

### Research Knowledge Base

`Knowledge-Base/` holds two zones: `ref/*.yaml` (the approved classifier
reference, unchanged by this section) and the research wiki (everything
else — portfolio, stocks, theses, earnings, dividends, market-research,
sources, taxonomy, templates, logs). Every wiki page carries YAML front
matter per `Knowledge-Base/templates/front-matter-spec.md` — pages without
it are invisible to `kb-search`. Generated pages (`portfolio/holdings.md`,
`portfolio/portfolio-overview.md`, and every `index.md` except the
hand-curated `theses/index.md`, `logs/index.md`, `taxonomy/index.md`) are
rebuilt by their owning skill, never hand-edited. KB agents (`kb-discovery`,
`kb-intake`) never edit `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md`. See
`docs/architecture/knowledge_base.md` for the full layout.

## Build, Test, and Development Commands

Use a Python virtual environment and install dependencies from `requirements.txt`.

- `python -m unittest discover -s tests` runs the full test suite.
- `python -m unittest tests.test_app` runs one test module.
- `python src/app.py --help` shows the CLI entry points for the pipeline.

## Coding Style & Naming Conventions

Follow standard Python 3 style with 4-space indentation and snake_case for functions, variables, and filenames. Keep modules small and focused on one responsibility. Prefer explicit imports and type hints where they improve clarity. Test classes use `CamelCase` and test methods use descriptive `test_*` names.

## Testing Guidelines

The project uses the built-in `unittest` framework. Add tests alongside the module behavior they cover, and prefer deterministic fixtures over live service calls. When testing pipeline behavior, mock external dependencies such as network, email, and database operations.

## Documentation

`docs/reference/cli.md` is the canonical, complete guide to every user-facing
`python src/app.py <command>` — every command registered in `app.py`'s
`_print_root_help`/`main()` dispatch must have a section there, kept in sync
with its actual arguments and behavior. When a change adds, renames, or alters
the behavior of a CLI command (new flags, new subcommand, changed defaults,
changed output format), update `docs/reference/cli.md` in the same change,
not as a follow-up — a command missing from this file is a bug. Related
architecture docs (`docs/architecture/*.md`, `docs/agents/<agent>/*.md`) should
also be updated when the change affects the behavior they describe.

## Planning & Approved Plans

All implementation plans approved during the planning process must be stored in
`docs/plans/` with a descriptive filename (e.g., `docs/plans/position-engine.md`,
`docs/plans/holdings-reconciliation.md`). This keeps the rationale, design
decisions, and scope of major features retrievable for future reference and
architecture reviews. Plans are reference material and should not be updated
after implementation completes — they capture the approved approach at the time
of execution.

## Commit & Pull Request Guidelines

Recent commits are short, imperative, and task-focused, for example: `Add normalized activity export storage` and `Consolidate statement extraction`. Keep commit messages in that style.

Pull requests should include:

- a short summary of the behavior change,
- any setup or migration notes,
- test evidence (`python -m unittest discover -s tests` output or equivalent),
- screenshots only when the change affects UI or rendered output.

## Security & Configuration Tips

Do not commit secrets, mailbox credentials, or local database files. Keep environment-specific values in `.env` or local config overrides, and verify file moves carefully because the pipeline renames and archives downloaded statements.
