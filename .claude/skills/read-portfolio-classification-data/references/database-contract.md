# Database contract

Open `config.DATABASE_PATH` with DuckDB `read_only=True`. Require the active application schema. Execute only the hard-coded holdings query. Expose no SQL or production database-path argument and perform no writes, migrations, or schema changes.
