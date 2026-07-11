---
title: "Allocation Policy"
type: portfolio-page
tickers: []
tags: [allocation, policy, portfolio]
status: final
created: 2026-07-11
updated: 2026-07-11
summary: "Narrative summary of the approved allocation policy; source of truth is ref/policy_v1_1.yaml."
related:
  - ../taxonomy/index.md
---

# Allocation Policy

The approved policy is the versioned
[`../ref/policy_v1_1.yaml`](../ref/policy_v1_1.yaml) — this page is a
human-readable summary and stays subordinate to it. If they ever disagree,
the YAML wins.

## Portfolio model

**Hybrid Core + Satellite**, classified role-first, sector-second: broad
market exposure forms the foundation, quality compounders come next,
income-first holdings serve explicit yield roles, and higher-volatility
growth names stay satellite-sized.

## Allocation targets

| Group | Target % | Min % | Max % | Risk level |
|---|---|---|---|---|
| Core | 60 | 55 | 70 | medium |
| Income | 15 | 10 | 20 | low-to-medium |
| Quality | 15 | 10 | 20 | medium |
| Growth | 10 | 5 | 15 | high |
| Alternatives | 5 | 0 | 5 | medium-to-high |
| Cash | flexible | 0 | — | low |

Constraint: no single name above **10%** of the portfolio.

## What the policy avoids

- Classifying by sector only.
- Creating unapproved subgroups.
- Treating every dividend payer as Income.
- Hiding manual review flags.

Current group weights vs these targets are maintained in the generated
[portfolio-overview.md](portfolio-overview.md).
