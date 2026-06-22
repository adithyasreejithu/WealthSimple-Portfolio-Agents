# Handover

Use this document when signing off, clearing context, or resuming work after a break.

## Current Focus

- WealthSimple portfolio tooling.
- Main active modules: `src/data_sorter.py` and `src/statement_extractor.py`.
- Supporting runtime helper: `src/system_logger.py`.
- Repo-wide working rules: `instructions.md`.

## What We Were Doing

- Consolidated the Wealthsimple PDF statement extraction refactor into one flat runtime file.
- Removed the Camelot package folder and separate run script from `src/`.
- Removed the old extracted Camelot reference folder.
- Kept glossary extraction available, but made it opt-in because it is expected to be run once later for database setup.
- Kept logging centralized through `src/system_logger.py`.

## Current State

- The sorter reads `Data/activities-export-*.csv`.
- Trailing footer rows are trimmed before processing.
- Known rows are normalized into the cleaned dataframe.
- Unknown activity rows are separated into the unknown dataframe.
- The source file is moved into `Data/processed_data/` with the same filename.
- No separate cleaned CSV file is written by the sorter.
- The statement extractor reads Wealthsimple statement PDFs from `Data/*.pdf`.
- Transaction extraction runs by default with `python src/statement_extractor.py`.
- Glossary extraction runs only with `python src/statement_extractor.py --include-glossary`.
- `src/statement_extractor.py` exposes `extract_statement_pdf`, `extract_statement_glossary_pdf`, and `extract_folder`.
- Transaction output keeps `date`, `transaction`, `ticker_id`, `quantity`, `execDate`, `fx_rate`, `debit`, `credit`, `balance`, `statement_code`, and `description`.
- Glossary output keeps `code` and `description`.
- `src/` is intended to stay flat; there should be no Camelot subfolder or separate extractor runner file.
- `instructions.md` is the repo-wide implementation guide.

## Important Files

- `src/data_sorter.py`
- `src/statement_extractor.py`
- `src/system_logger.py`
- `tests/test_statement_extractor.py`
- `docs/SuccessCritera.md`
- `docs/camelot_extraction_refactor_plan.md`
- `instructions.md`

## Known Constraints

- Do not create extra tracked files unless the task explicitly requires them.
- Do not rename the source file during the processed-file move.
- Keep future changes aligned with `instructions.md`.
- Keep logging behavior predictable and repository-relative.
- Keep statement extraction consolidated in `src/statement_extractor.py` until a future `src/main.py` is introduced.
- Do not recreate `src/wealthsimple_camelot/`, `src/run_camelot_extract.py`, or `extracted_camelot_method/`.
- Do not run glossary extraction by default; use the `--include-glossary` flag only when explicitly needed.

## Last Completed Work

- Consolidated statement extraction into `src/statement_extractor.py`.
- Removed the old Camelot package folder, direct run script, and extracted reference folder.
- Updated tests to import from `statement_extractor`.
- Updated the success criteria and refactor plan docs to reflect the flat structure.
- Verified transaction-only direct run and opt-in glossary direct run.
- Verified sample extraction across `Data/2023-07.pdf` and `Data/2025-04.pdf`: 45 transaction rows and 54 glossary rows.

## Open Checks Before Resuming

- Confirm `src/__pycache__/` is not left behind.
- Confirm `src/data_sorter.py` still matches the latest requested move-only behavior.
- Confirm `src/statement_extractor.py` remains the only statement extraction runtime file.
- Confirm no Camelot-named folders or `run_camelot_extract.py` were recreated.
- Confirm no new generated files were introduced accidentally.
- Confirm the repo is in the expected git state before continuing.
- Current expected untracked/modified work includes `.gitignore`, `requirements.txt`, `src/statement_extractor.py`, `tests/`, and the docs updated during this session.

## Last Verification Commands

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py --include-glossary
```

## Sign-Off Checklist

- Write down the exact file or behavior being worked on.
- Note any user constraints that changed the direction of the task.
- Record the last verification command that passed.
- Record any blockers or environment issues.
- Record the next concrete step, not just the broad goal.
