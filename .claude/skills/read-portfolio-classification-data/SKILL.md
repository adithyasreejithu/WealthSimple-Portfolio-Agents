---
name: read-portfolio-classification-data
description: Read normalized portfolio classification inputs from the configured DuckDB using fixed queries and a read-only connection. Use only as an internal dependency of the complete classify-portfolio workflow, never for arbitrary SQL or database paths.
---

# Read Portfolio Classification Data

Use `scripts/read_classification_data.py` only through the classify-portfolio orchestrator. Do not add SQL, ticker, or database-path arguments. Read [database-contract.md](references/database-contract.md) for the executable boundary.
