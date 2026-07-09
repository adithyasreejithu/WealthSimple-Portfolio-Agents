---
name: reconcile-holdings-report
description: Generate a deterministic holdings-reconciliation markdown report comparing computed holdings against a Wealthsimple broker CSV export, in the same format as docs/holdings_reconciliation_2026-07-07.md. Reports are written to exports/holdings-reconciliation/ by default. Use when the user asks to reconcile holdings or generate/regenerate a holdings reconciliation report.
---

# Reconcile Holdings Report

Run only `python .claude/skills/reconcile-holdings-report/scripts/generate_reconciliation_report.py --report <path-to-broker-csv>`.

Permit only `--report`, `--database`, and `--output`. Do not run arbitrary SQL or Python, edit `src/position_engine.py`/`src/analytics.py`/`src/holdings_reconciler.py` reconciliation logic, or fabricate root-cause narrative beyond the flag mapping in [report-contract.md](references/report-contract.md). The script is the sole source of the report's content; relay its output path verbatim.
