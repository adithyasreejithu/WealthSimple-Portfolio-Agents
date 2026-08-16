# Output contract

The JSON envelope contains `schema_version`, `generated_at`, `workflow`, `database_mode`, `summary`, and `holdings`. Each holding contains classification fields, normalized `fields`, `field_provenance`, and an `enrichment` audit. Raw transactions and unrestricted provider payloads are forbidden.

## Ownership scope

`fields.ownership_status` is `"owned"` (currently held, `quantity <> 0`) or
`"wishlist"` (declared via `database status --set wishlist`, not owned).
Every other declared status (`avoid`, `retired`) and any ticker with no
declaration at all are absent from `holdings` entirely -- this skill does not
classify them. A wishlist holding's ownership-derived `fields` (`quantity`,
`cost_basis`, `position_market_value`, `current_weight_percent`,
`unrealized_gain_loss_percent`, ledger/dividend/account summaries) are fixed
"not owned" defaults (`0`/`None`/`[]` as appropriate), not gaps -- see
`read-portfolio-classification-data`'s `database-contract.md`.

## Position data-quality signals

`fields` includes `provisional_quantity`, `data_quality_flags` (a list), and
the derived `has_provisional_activity` boolean, passed through unchanged from
`read-portfolio-classification-data` (see that skill's `database-contract.md`).
A consumer citing `classification:holdings.*` evidence -- today, the decision
rubric's `portfolio_fit` dimension -- can check these before treating a
holding's weight as fully settled. This is data surfacing only: the rubric
itself is not changed by this skill (edit it via `author-decision-rubric`).
