# Output contract

The JSON envelope contains `schema_version`, `generated_at`, `workflow`, `database_mode`, `summary`, and `holdings`. Each holding contains classification fields, normalized `fields`, `field_provenance`, and an `enrichment` audit. Raw transactions and unrestricted provider payloads are forbidden.

## Position data-quality signals

`fields` includes `provisional_quantity`, `data_quality_flags` (a list), and
the derived `has_provisional_activity` boolean, passed through unchanged from
`read-portfolio-classification-data` (see that skill's `database-contract.md`).
A consumer citing `classification:holdings.*` evidence -- today, the decision
rubric's `portfolio_fit` dimension -- can check these before treating a
holding's weight as fully settled. This is data surfacing only: the rubric
itself is not changed by this skill (edit it via `author-decision-rubric`).
