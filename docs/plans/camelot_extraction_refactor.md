# Camelot Extraction Refactor Plan

## Summary

Move the Wealthsimple statement extraction implementation into `src/` as the runtime source of truth. The parser should cover the full `Activity - Current period` section for the two known statement layouts and expose statement-code glossary data separately for later database work.

Runtime file:

- `src/statement_extractor.py` contains extraction logic and can be run directly until `src/main.py` exists.

## Implementation Steps

1. Keep the existing hard-coded activity page search based on `Activity - Current period`.
2. Replace fixed table-header dropping with content-based trimming that starts at the first dated activity row.
3. Stop parsing activity rows when the table reaches `Transactions for Future Settlement`.
4. Parse all statement-code-shaped activity rows, not only the currently handled subset.
5. Preserve `statement_code` as the raw code from the statement for audit and later mapping.
6. Extract the `Information about Statement Codes` page into a separate glossary dataframe.
7. Keep transaction rows and glossary rows separate; database storage for the glossary will be added later.
8. Add focused tests for row trimming, wrapped rows, expanded transaction-code parsing, and glossary extraction.
9. Keep `src/statement_extractor.py` directly runnable until the future `src/main.py` entrypoint exists.

## Success Criteria

- Every transaction code found in the `Activity - Current period` section of the sample statements is parsed or explicitly retained for later mapping.
- No activity-section transaction type is silently dropped by the extraction step.
- The extractor preserves the existing upload columns: `date`, `transaction`, `ticker_id`, `quantity`, `execDate`, `fx_rate`, `debit`, `credit`, and `balance`.
- The extractor also provides `statement_code` for raw-code auditability.
- The statement-code glossary can be extracted separately with `code` and `description` columns.
- Wrapped rows, multi-line descriptions, `record date of`, `FX Rate`, loan/recall rows, non-resident tax, fractional interest, and unknown activity codes are retained correctly.
- Future-settlement rows are excluded from current-period activity extraction.
- The future main CLI can use the `src/` extractor.
- The extractor can be run directly with `python src/statement_extractor.py`.

## Later Database Direction

When the database is updated, store statement-code metadata in a separate lookup table rather than mixing glossary rows into transactions.

Recommended future table shape:

```text
statement_codes
- code TEXT PRIMARY KEY
- description TEXT
- category TEXT NULL
- normalized_type TEXT NULL
- active BOOLEAN DEFAULT TRUE
```

The transaction tables should keep factual transaction records. They may keep a raw `statement_code` field later if useful for validation, reconciliation, or mapping.

## Assumptions

- The two sample statement layouts are the initial supported layouts.
- Page detection remains unchanged for this iteration.
- No Camelot-named runtime or reference folder is required.
- Database changes for statement-code storage are intentionally deferred.
