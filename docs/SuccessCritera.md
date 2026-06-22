# Camelot Extraction Refactor Success Criteria

Use this checklist to decide whether the Camelot extraction refactor is ready to move forward. The detailed implementation plan lives in `docs/camelot_extraction_refactor_plan.md`.

## Scope

- Runtime extraction code lives under `src/`.
- Runtime extraction is consolidated in `src/statement_extractor.py`.
- `src/statement_extractor.py` can be run directly until `src/main.py` exists.
- Database storage changes for statement-code metadata are deferred.

## Required Outcomes

- [x] The extractor locates activity pages using the existing `Activity - Current period` search.
- [x] The extractor trims table headers by content and starts at the first dated activity row.
- [x] The extractor stops current-period activity parsing at `Transactions for Future Settlement`.
- [x] Future-settlement rows are excluded from current-period transaction output.
- [x] All statement-code-shaped activity rows in the current-period section are parsed or retained for later mapping.
- [x] No current-period transaction type is silently dropped during extraction.
- [x] Unknown or unsupported activity codes are retained with their raw statement code.
- [x] The raw statement code is preserved as `statement_code`.
- [x] Transaction rows keep the upload columns `date`, `transaction`, `ticker_id`, `quantity`, `execDate`, `fx_rate`, `debit`, `credit`, and `balance`.
- [x] The extractor separately returns or writes statement-code glossary rows with `code` and `description` columns.
- [x] Glossary rows are not mixed into transaction output.
- [x] `src/main.py`, when it exists, can import the `src/` extractor without depending on a Camelot package folder.
- [x] `src/statement_extractor.py` can run extraction directly without invoking the full app pipeline.

## Parsing Coverage

- [x] Wrapped rows are retained and associated with the correct activity record.
- [x] Multi-line descriptions are retained without corrupting adjacent rows.
- [x] `record date of` rows are handled.
- [x] `FX Rate` rows are handled.
- [x] Loan and recall rows are handled.
- [x] Non-resident tax rows are handled.
- [x] Fractional interest rows are handled.
- [x] Rows from both known statement layouts are covered.

## Test Expectations

- [x] Focused tests cover content-based row trimming.
- [x] Focused tests cover wrapped and multi-line activity rows.
- [x] Focused tests cover expanded transaction-code parsing.
- [x] Focused tests cover retention of unknown activity codes.
- [x] Focused tests cover extraction of the statement-code glossary.
- [x] Focused tests confirm future-settlement rows are excluded from current-period activity.

## Completion Checks

- [x] Runtime imports do not depend on removed Camelot folders.
- [x] The sample statements produce current-period transaction output with no unexpected dropped rows.
- [x] The glossary extraction output can be inspected independently from transaction output.
- [x] Existing upload-facing transaction columns remain backward compatible.
- [x] The direct extraction command path is documented or obvious from the file name.

## Out of Scope For This Plan

- Creating database tables for statement-code metadata.
- Normalizing statement codes into final categories.
- Replacing the existing hard-coded activity page search.
- Supporting statement layouts beyond the two known samples.
