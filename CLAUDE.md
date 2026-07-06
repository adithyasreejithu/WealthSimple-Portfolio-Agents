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

## Build, Test, and Development Commands

Use a Python virtual environment and install dependencies from `requirements.txt`.

- `python -m unittest discover -s tests` runs the full test suite.
- `python -m unittest tests.test_app` runs one test module.
- `python src/app.py --help` shows the CLI entry points for the pipeline.

## Coding Style & Naming Conventions

Follow standard Python 3 style with 4-space indentation and snake_case for functions, variables, and filenames. Keep modules small and focused on one responsibility. Prefer explicit imports and type hints where they improve clarity. Test classes use `CamelCase` and test methods use descriptive `test_*` names.

## Testing Guidelines

The project uses the built-in `unittest` framework. Add tests alongside the module behavior they cover, and prefer deterministic fixtures over live service calls. When testing pipeline behavior, mock external dependencies such as network, email, and database operations.

## Commit & Pull Request Guidelines

Recent commits are short, imperative, and task-focused, for example: `Add normalized activity export storage` and `Consolidate statement extraction`. Keep commit messages in that style.

Pull requests should include:

- a short summary of the behavior change,
- any setup or migration notes,
- test evidence (`python -m unittest discover -s tests` output or equivalent),
- screenshots only when the change affects UI or rendered output.

## Security & Configuration Tips

Do not commit secrets, mailbox credentials, or local database files. Keep environment-specific values in `.env` or local config overrides, and verify file moves carefully because the pipeline renames and archives downloaded statements.
