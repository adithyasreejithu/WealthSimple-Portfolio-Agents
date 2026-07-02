# Email Extraction Plan

## Summary

Create one flat runtime file under `src/` that combines the existing Wealthsimple email filters and Interac email filters into a single read-only extraction flow. The runtime should print one combined dataframe at the end of the run and keep the extracted fields aligned with the current source scripts.

## Implementation Steps

1. Move the email parsing logic into `src/email_extractor.py` as the runtime source of truth.
2. Keep the runtime flat under `src/` and avoid introducing a package folder or separate runner.
3. Reuse the existing Wealthsimple sender and subject filters from `email_export/Purchase_Validation.py`.
4. Reuse the existing Interac sender and strict subject filter from `email_export/email_test.py`.
5. Normalize both sources into one dataframe with the columns `account`, `transaction`, `ticker_id`, `ticker`, `quantity`, `avg_price`, `total_cost`, `debit`, and `date`.
6. Leave non-applicable fields blank for now, except for the existing Interac defaults `ticker_id=0` and `ticker=EMAIL`.
7. Read credentials from environment variables and use `START_DATE` as the temporary start-date source.
8. If `START_DATE` is not configured, run without a date filter and log that the database-backed checkpoint is still future work.
9. Resolve `date` from the email body first and fall back to the IMAP received date when the body does not provide one.
10. Warn when neither the in-email date nor the received date is available.
11. Print the full combined dataframe at the end of the run.
12. Add section-level comments across the runtime so the flow is easy to follow.
13. Add focused tests for parsing, filtering, date fallback, and combined output behavior.
14. Delete `email_export/` after the new runtime is verified.

## Success Criteria

- Both email filter sets are included in one runtime file.
- Matching Wealthsimple and Interac emails are retrieved and normalized into one dataframe.
- The final `date` field is populated from the email body when available and otherwise from the message received date.
- The runtime prints the full combined data at the end of the run.
- The extracted fields stay aligned with the current source scripts.
- The runtime can be run directly with `python src/email_extractor.py`.
- The main implementation sections are commented for readability.

## Deferred Work

- Database-backed email start-date checkpoint retrieval.
- Email upload or checkpoint update flows.
- Replacing environment-based credentials with a later permanent mechanism.
