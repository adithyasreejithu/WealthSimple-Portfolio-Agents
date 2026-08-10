---
name: read-security-price-history
description: Read one security's daily OHLCV history, and the configured benchmark's, from the configured DuckDB using fixed queries and a read-only connection. Use only as an internal dependency of the security-technicals workflow, never for arbitrary SQL or database paths.
---

# Read Security Price History

Use `scripts/read_price_history.py` only through the security-technicals orchestrator. Do not add SQL, database-path, or date-range arguments. Read [database-contract.md](references/database-contract.md) for the executable boundary.
