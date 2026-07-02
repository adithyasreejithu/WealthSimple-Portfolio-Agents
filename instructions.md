# Repository Instructions

This file is the first thing to read before implementing or changing code in this repository.

## Purpose

- This repo is for WealthSimple portfolio tooling and related future features.
- Treat this file as the repo-wide source of truth for implementation work.
- Apply these rules to new files as well as existing ones unless a task explicitly overrides them.

## Repository Structure

- `src/` contains active application code.
- `docs/` contains supporting notes, criteria, and implementation guidance.
- `ref/` is a working reference drop folder for files used to aid development.
- `Data/` contains input, intermediate, or processed artifacts only when the application requires them.
- `tests/` contains the expected verification surface for behavior changes.
- `src/app.py` is the main pipeline entrypoint.
- `src/database_command.py` and `src/ticker_mapping.py` provide the reusable database and ticker-management flows.

## Implementation Rules

- Prefer small, focused changes.
- Preserve existing behavior unless the task explicitly asks for a change.
- Keep names clear and consistent with the surrounding code.
- Every new feature must include docstrings or comments that explain its purpose,
  important constraints, non-obvious decisions, and significant data flow.
- Comments should explain why the code exists or why an approach was chosen; do
  not restate straightforward code line by line.
- Reuse shared helpers and avoid duplicating logic when a common utility already exists.
- Do not rename files or create extra copies unless the task explicitly asks for that behavior.
- Keep non-secret static values in `src/config.py` so modules share one source of truth.
- Keep secrets, tokens, passwords, and environment-specific values out of `src/config.py`; load those from the environment at runtime instead.
- Treat `instructions.md` as the repo-wide default context for all implementation work unless a task overrides it.
- Prefer adding or updating tests in `tests/` when behavior changes.
- Keep new code aligned with the existing staged pipeline pattern instead of bypassing it with ad hoc inserts.

## Logging Rules

- Use the shared logger helper in `src/system_logger.py` for runtime code that needs logging.
- Any new runtime code added under `src/` should include logger setup by default unless the task explicitly says not to add logging.
- Keep logger paths predictable and repository-relative.
- Log major state transitions, warnings, and failures.
- Use exception logging for top-level failures when a command or script can fail visibly.
- Log batch-level outcomes for pipeline work and include the source/file context when available.
- Prefer consistent structured message fragments so failures are easy to grep in `logs/SystemLogs.txt`.

## Data and File Handling

- Validate input assumptions before processing.
- Treat source data carefully and make file movement behavior explicit.
- Keep transformations testable and easy to review.
- If a task involves file movement, make the success and failure path clear.
- Preserve quarantining behavior for bad inputs instead of silently dropping failed files.
- When adding file movement, keep archive paths deterministic and repository-relative.

## Data Sources

Use these sources according to their role and priority:

1. Monthly reports are the main hub for transaction tracking because they contain FX rates and other important transaction details.
2. The entire Wealthsimple data export is the most trustworthy source and the primary fallback when monthly-report data is missing, unclear, conflicting, or requires verification. Most data pulls may come from this export because of its broader coverage.
3. Email confirmations are the tertiary source for near-real-time transaction information while monthly reports and full data exports are delayed.

When sources conflict, use the entire Wealthsimple data export for verification while preserving additional details, such as FX information, that are available only in monthly reports.

## Database Normalization

- Use `ticker_id` as the canonical database key for securities in every domain, including statements, email transactions, yfinance metadata, and historical prices.
- Raw ticker text may exist at extraction and ticker-resolution boundaries, but normalized database tables must store the related `ticker_id` instead of duplicating ticker symbols.
- Resolve or create the ticker record before inserting ticker-backed data.
- Keep source-specific extraction and dataframe formats independent from the database schema. The future pipeline layer is responsible for mapping extracted ticker values to `ticker_id`.
- Database initialization must be safe to run at process startup. If the current schema is active, continue without modifying it; if no schema exists, create the complete schema transactionally.
- Do not automatically reset, drop, or partially migrate an existing database during normal startup.
- Prefer schema additions over in-place rewrites unless the migration is explicitly planned and tested.
- Keep schema changes and `DATABASE_SCHEMA_VERSION` updates synchronized.
- Avoid publishing staged data when ticker resolution remains unresolved.

## Verification Rules

- Verify changed behavior before considering the work complete.
- Check both success and failure paths when a change affects error handling.
- Confirm any expected outputs, side effects, or file operations.
- Prefer targeted checks over broad assumptions.
- Run the smallest relevant test subset first, then broaden only if needed.
- Always implement regression testing for behavior changes, bug fixes, and refactors that could affect existing flows.
- Always add or update test cases for new features before considering the work complete.
- For CLI and pipeline changes, confirm both exit code and printed status output.
- Keep every user-facing CLI feature accessible through `src/app.py` and update
  `docs/reference/cli.md` whenever a command or option changes.
- For database changes, verify initialization and any migration path touched by the change.

## Git and Repo Hygiene

- Keep changes scoped to the task.
- Avoid unrelated edits.
- Keep generated or temporary files out of version control unless intentionally required.
- Do not clean up or revert unrelated work in the tree unless explicitly requested.

## Comments Format

- Use Python docstrings for modules, classes, and public functions.
- Use `#` comments for local implementation rationale and non-obvious behavior.
- Keep comments accurate when changing the code they describe.

## Documentation and Context

- Treat `docs/project/handover.md` as the current repository context file.
- After every completed task that changes the repository, update the handover
  with the current state, completed work, remaining limitations, and verification.
- Keep `docs/README.md` and the relevant reference documents synchronized when
  files, commands, or supported workflows change.

## Future Work

- This file should guide future utilities, modules, and features across the repo.
- Task-specific rules can live in code comments or supporting docs, but this file remains the implementation baseline.
- If a new recurring workflow appears, add a short rule here rather than relying on transient chat context.
