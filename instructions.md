# Repository Instructions

This file is the first thing to read before implementing or changing code in this repository.

## Purpose

- This repo is for WealthSimple portfolio tooling and related future features.
- Treat this file as the repo-wide source of truth for implementation work.
- Apply these rules to new files as well as existing ones unless a task explicitly overrides them.

## Repository Structure

- `src/` contains active application code.
- `docs/` contains supporting notes, criteria, and implementation guidance.
- `Data/` contains input, intermediate, or processed artifacts only when the application requires them.

## Implementation Rules

- Prefer small, focused changes.
- Preserve existing behavior unless the task explicitly asks for a change.
- Keep names clear and consistent with the surrounding code.
- Add comments only where the logic would otherwise be hard to follow.
- Reuse shared helpers and avoid duplicating logic when a common utility already exists.
- Do not rename files or create extra copies unless the task explicitly asks for that behavior.

## Logging Rules

- Use the shared logger helper in `src/system_logger.py` for runtime code that needs logging.
- Keep logger paths predictable and repository-relative.
- Log major state transitions, warnings, and failures.
- Use exception logging for top-level failures when a command or script can fail visibly.

## Data and File Handling

- Validate input assumptions before processing.
- Treat source data carefully and make file movement behavior explicit.
- Keep transformations testable and easy to review.
- If a task involves file movement, make the success and failure path clear.

## Verification Rules

- Verify changed behavior before considering the work complete.
- Check both success and failure paths when a change affects error handling.
- Confirm any expected outputs, side effects, or file operations.
- Prefer targeted checks over broad assumptions.

## Git and Repo Hygiene

- Keep changes scoped to the task.
- Avoid unrelated edits.
- Keep generated or temporary files out of version control unless intentionally required.

## Comments Format

- Please follow this format:
  ```text
  """
  Comment
  """
  ```

## Future Work

- This file should guide future utilities, modules, and features across the repo.
- Task-specific rules can live in code comments or supporting docs, but this file remains the implementation baseline.
