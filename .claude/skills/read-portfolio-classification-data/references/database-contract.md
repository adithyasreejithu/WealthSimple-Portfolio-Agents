# Database contract

Open `config.DATABASE_PATH` with DuckDB `read_only=True`. Require the active application schema. Execute only the hard-coded holdings query. Expose no SQL or production database-path argument and perform no writes, migrations, or schema changes.

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
