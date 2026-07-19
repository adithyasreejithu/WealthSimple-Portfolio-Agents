---
name: classify-portfolio
description: Run the repository's complete constrained portfolio-classification workflow when the user asks to classify their portfolio or says "Classify my portfolio." Produce deterministic JSON from the configured read-only DuckDB, approved YAML rules, and ephemeral allowlisted yfinance enrichment.
---

# Classify Portfolio

Run only `python .claude/skills/classify-portfolio/scripts/classify_portfolio.py --pretty`.

Permit only `--output` and `--pretty`. Do not run arbitrary SQL or Python, edit policy YAML, select tickers or yfinance fields, persist enrichment, or invoke supporting scripts directly. Read [workflow-contract.md](references/workflow-contract.md) and [output-contract.md](references/output-contract.md) when contract details are needed.
