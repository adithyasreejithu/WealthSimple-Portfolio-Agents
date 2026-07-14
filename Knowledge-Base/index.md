---
title: "Research Wiki"
type: index
tickers: []
tags: [index]
status: generated
created: 2026-07-11
updated: 2026-07-11
summary: "Main index of the investment research knowledge base."
---

# Research Wiki

LLM-maintained investment research knowledge base. Every page carries YAML
front matter per [templates/front-matter-spec.md](templates/front-matter-spec.md);
the `kb-discovery` agent searches it, the `kb-intake` agent writes to it.

## Sections

- [Portfolio](portfolio/index.md) — overview, holdings, allocation policy, risk log
- [Stocks](stocks/index.md) — canonical per-ticker pages with theses
- [Theses](theses/index.md) — status views (active / watchlist / closed / rejected)
- [Earnings](earnings/index.md) — earnings notes and catalysts
- [Dividends](dividends/index.md) — income and dividend-safety notes
- [Market Research](market-research/index.md) — sector, macro, sentiment, options notes
- [Sources](sources/index.md) — ingested external documents
- [Taxonomy](taxonomy/index.md) — classification rules (links to `ref/`) and decision framework
- [Templates](templates/index.md) — page templates and front-matter spec
- [Logs](logs/index.md) — decision, research, and update logs

## Recently Updated

<!-- kb-index:begin -->
| Page | Type | Tickers | Status | Updated | Summary |
|---|---|---|---|---|---|
| [Apple Inc. (AAPL) — Stock Page](stocks/AAPL.md) | stock-page | AAPL | active | 2026-07-13 | Research page for AAPL, created 2026-07-13. |
| [Allocation Policy](portfolio/allocation-policy.md) | portfolio-page |  | final | 2026-07-11 | Narrative summary of the approved allocation policy; source of truth is ref/policy_v1_1.yaml. |
| [Holdings](portfolio/holdings.md) | portfolio-page | AAPL, AMZN, BN, DGRO, DRAM, ENB, HIMS, INDA, L, MDA, META, NOW, NVDA, PLTR, PZA, RTX, SCHD, SMH, SPYM, T, WN, XEQT, XNDU, ZEB, ZEQT, ZGLD | generated | 2026-07-11 | Current holdings synced from the classification workflow (26 positions). |
| [Portfolio Overview](portfolio/portfolio-overview.md) | portfolio-page |  | generated | 2026-07-11 | 26 holdings, 4 needing review, total market value 4,112.85. |
| [Portfolio Risk Log](portfolio/risk-log.md) | portfolio-page |  | final | 2026-07-11 | Append-only register of portfolio-level risks being monitored. |
<!-- kb-index:end -->
