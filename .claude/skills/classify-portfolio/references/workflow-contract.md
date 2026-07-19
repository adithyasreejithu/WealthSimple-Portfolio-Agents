# Workflow contract

Load approved v1.1 YAML policy, open only the configured DuckDB read-only, preserve populated database fields, enrich only missing allowlisted fields in memory, apply deterministic rules, validate schema 1.0, and write JSON inside `exports/portfolio-classification`.

Per-ticker enrichment failures continue as reviewable records. Database, policy, and output failures stop the run.
