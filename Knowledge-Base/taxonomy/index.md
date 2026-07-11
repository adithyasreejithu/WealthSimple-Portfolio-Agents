---
title: "Taxonomy"
type: index
tickers: []
tags: [index, taxonomy]
status: final
created: 2026-07-11
updated: 2026-07-11
summary: "Where the portfolio taxonomy lives: links to the approved ref/ YAMLs plus the decision framework."
---

# Taxonomy

The portfolio classification taxonomy is **not duplicated here**. The approved
source of truth is the versioned YAML set in [`../ref/`](../ref/), loaded
directly by the deterministic classifier (`src/portfolio_classifier.py` via
`src/config.py`). This folder only documents that set and adds vocabularies
that have no equivalent there.

## Approved classification reference (source of truth: `ref/`)

| File | Owns |
|---|---|
| [`../ref/policy_v1_1.yaml`](../ref/policy_v1_1.yaml) | Approved groups (Core, Income, Quality, Growth, Alternatives, Cash, Needs Review), portfolio roles, allocation targets/min/max, single-name cap. |
| [`../ref/classification_rules_v1_1.yaml`](../ref/classification_rules_v1_1.yaml) | Rule priority and decision logic for assigning groups. |
| [`../ref/security_grouping_reference_v1_1.yaml`](../ref/security_grouping_reference_v1_1.yaml) | Asset classes, ETF categories, sector map, geography/currency/theme/risk tags. |
| [`../ref/manual_overrides_v1_1.yaml`](../ref/manual_overrides_v1_1.yaml) | Ticker-level overrides (highest priority) with rationale. |
| [`../ref/required_fields_v1_1.yaml`](../ref/required_fields_v1_1.yaml) | Minimum classification inputs and missing-data behavior. |
| [`../ref/group_examples_v1_1.yaml`](../ref/group_examples_v1_1.yaml) | Worked example classifications. |

Changes to those files are versioned in `Knowledge-Base/CHANGELOG.md` and must
go through the classification workflow's review process — **wiki agents never
edit `ref/`**.

## Wiki-owned vocabularies

| File | Owns |
|---|---|
| [decision-framework.yml](decision-framework.yml) | Decision actions (Buy/Sell/Hold/Trim/Add/Watchlist/Avoid), confidence levels, time horizons, thesis verdicts, stock-page statuses. |

Risk levels per group are defined inside `ref/policy_v1_1.yaml` (`risk_level`
on each group) — no separate risk-levels file is needed.
