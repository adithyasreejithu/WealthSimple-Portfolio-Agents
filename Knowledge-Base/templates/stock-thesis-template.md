---
title: "Stock Thesis Template"
type: template
tickers: []
tags: [template, thesis]
status: final
created: 2026-07-11
updated: 2026-07-11
summary: "Template for canonical per-ticker stock pages in stocks/TICKER.md."
---

# Stock Thesis Template

Copy everything below the rule into `stocks/<TICKER>.md` (the
`kb-update-thesis` skill's `create` command does this automatically and
pre-fills held positions from the classification JSON). Replace the
placeholder front matter. Status/Decision/Confidence/Time Horizon values are
constrained by [../taxonomy/decision-framework.yml](../taxonomy/decision-framework.yml).

**Immutability rules:** once written, *Original Thesis* is never edited — new
thinking goes in *Updated Thesis*. *Decision History* rows are append-only.

---

```markdown
---
title: "Company Name (TICKER) — Stock Page"
type: stock-page
tickers: [TICKER]
tags: []
status: research
created: YYYY-MM-DD
updated: YYYY-MM-DD
summary: "One line: role, current decision, what is being monitored."
related: []
sources: []
---

# TICKER — Company Name

## Status

- Portfolio Status: research | watchlist | active | closed | rejected
- Portfolio Role: Core | Income | Quality | Growth | Alternatives | Cash
- Decision: Buy | Sell | Hold | Trim | Add | Watchlist | Avoid
- Confidence: Low | Medium | High
- Time Horizon: Short-term | Medium-term | Long-term
- Last Updated: YYYY-MM-DD

## Company Overview

What the company does, how it makes money, segments, competitive position.

## Original Thesis

The initial reasoning, written once and preserved verbatim.

## Updated Thesis

The current view. State explicitly whether the thesis is stronger, weaker,
unchanged, or broken versus the original, and why.

## Financial Analysis

## Valuation Analysis

## Technical Analysis

## Options Activity

## Market Sentiment

## Insider Activity

## Earnings and Catalysts

## Dividend Analysis

## Portfolio Fit

How the position interacts with the rest of the portfolio: group, weight,
overlap, currency, diversification effect.

## Bull Case

## Bear Case

## Key Risks

## Open Questions

## Decision History

| Date | Action | Verdict | Note |
|---|---|---|---|

## Monitoring Checklist

- [ ] Assumption or metric to watch

## Sources
```
