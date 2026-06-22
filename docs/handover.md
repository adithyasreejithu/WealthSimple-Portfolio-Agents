# Handover

Use this document when signing off, clearing context, or resuming work after a break.

## Current Focus

- WealthSimple portfolio tooling.
- Main active module: `src/data_sorter.py`.
- Supporting runtime helper: `src/system_logger.py`.
- Repo-wide working rules: `instructions.md`.

## What We Were Doing

- Keeping the sorter focused on one job: read the latest WealthSimple export and move the processed file into `Data/processed_data/`.
- Preserving the original filename when moving the file.
- Printing the cleaned and unknown dataframes for review.
- Keeping logging centralized through `src/system_logger.py`.

## Current State

- The sorter reads `Data/activities-export-*.csv`.
- Trailing footer rows are trimmed before processing.
- Known rows are normalized into the cleaned dataframe.
- Unknown activity rows are separated into the unknown dataframe.
- The source file is moved into `Data/processed_data/` with the same filename.
- No separate cleaned CSV file is written by the sorter.
- `instructions.md` is the repo-wide implementation guide.

## Important Files

- `src/data_sorter.py`
- `src/system_logger.py`
- `instructions.md`

## Known Constraints

- Do not create extra tracked files unless the task explicitly requires them.
- Do not rename the source file during the processed-file move.
- Keep future changes aligned with `instructions.md`.
- Keep logging behavior predictable and repository-relative.

## Last Completed Work

- Removed the shared config refactor.
- Kept the sorter self-contained.
- Kept the move operation as a true rename into `Data/processed_data/`.
- Verified imports for `src/data_sorter.py` and `src/system_logger.py`.

## Open Checks Before Resuming

- Confirm `src/__pycache__/` is not left behind.
- Confirm `src/data_sorter.py` still matches the latest requested move-only behavior.
- Confirm no new generated files were introduced accidentally.
- Confirm the repo is in the expected git state before continuing.

## Sign-Off Checklist

- Write down the exact file or behavior being worked on.
- Note any user constraints that changed the direction of the task.
- Record the last verification command that passed.
- Record any blockers or environment issues.
- Record the next concrete step, not just the broad goal.
