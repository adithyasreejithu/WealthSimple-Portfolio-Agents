# Database contract

Open `config.DATABASE_PATH` with DuckDB `read_only=True`. Require the active application schema. Execute only the hard-coded holdings query (`read_classification_data`) or the hard-coded wishlist query (`read_wishlist_classification_data`). Expose no SQL or production database-path argument and perform no writes, migrations, or schema changes.

## Two queries, disjoint by construction

`read_classification_data` reads `position_snapshots WHERE quantity <> 0`
(owned holdings). `read_wishlist_classification_data` reads tickers with a
`security_status.declared_status` in `market_data.RESEARCH_STATUSES`
(currently just `wishlist`), explicitly excluding any ticker with a non-zero
`position_snapshots.quantity` -- ownership always wins over a stale
declaration (see `database._create_security_status_table`'s docstring), so
the two result sets never overlap and a caller may safely concatenate them.
`read_wishlist_classification_data` does not call
`_validate_positions_fresh`: it only reads `position_snapshots` as a live
exclusion filter, never for cached position pricing.

## Position data-quality fields

The holdings query reads `position_snapshots.provisional_quantity` and
`data_quality_flags` (see `docs/architecture/ingestion_and_reconciliation.md`)
alongside `quantity`. `data_quality_flags` comes back from DuckDB as JSON text
and is unwrapped into a list before being returned. A derived
`has_provisional_activity` boolean (`provisional_quantity != 0`) is added with
`field_provenance` `"derived"`, mirroring `analytics.py`'s
`Holding.has_provisional_activity`. These let a downstream consumer (e.g. the
stock decision-support workflow) tell whether a holding's quantity/weight is
still partly sourced from an unconfirmed email trade before trusting it.
