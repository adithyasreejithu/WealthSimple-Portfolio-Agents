# Email Extraction Success Criteria

Use this checklist to decide whether the email extraction consolidation is ready to move forward. The detailed implementation plan lives in `docs/email_extraction_plan.md`.

## Scope

- Runtime email extraction code lives under `src/`.
- Runtime extraction is consolidated in `src/email_extractor.py`.
- `src/email_extractor.py` can be run directly.
- Database-backed start-date retrieval is deferred.

## Required Outcomes

- [ ] Wealthsimple email filtering matches the current sender and subject rules.
- [ ] Interac email filtering matches the current sender and strict subject rules.
- [ ] Both sources are combined into one dataframe.
- [ ] The combined dataframe keeps the columns `account`, `transaction`, `ticker_id`, `ticker`, `quantity`, `avg_price`, `total_cost`, `debit`, and `date`.
- [ ] Existing intended extracted fields from both source scripts are preserved.
- [ ] The `date` field is populated from the email body when available and otherwise from the message received date.
- [ ] Non-applicable fields remain blank where requested, except for the existing Interac defaults.
- [ ] The direct run prints the full combined dataframe.
- [ ] A clear no-results message is shown when no matching emails are found.
- [ ] The major runtime sections are commented for readability and maintainability.
- [ ] The legacy `email_export/` folder is removed after migration.

## Test Expectations

- [ ] Focused tests cover Wealthsimple email parsing.
- [ ] Focused tests cover Interac email parsing.
- [ ] Focused tests cover sender and subject filters.
- [ ] Focused tests cover parsed-date and received-date fallback behavior.
- [ ] Focused tests cover combined dataframe ordering and normalization.
- [ ] Focused tests cover the empty-result path.

## Out of Scope For This Plan

- Reading the start date from the database.
- Updating database checkpoints after a run.
- Uploading email transactions into the database.
