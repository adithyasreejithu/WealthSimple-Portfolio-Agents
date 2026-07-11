---
title: "Sell Decision Template"
type: template
tickers: []
tags: [template, decisions]
status: final
created: 2026-07-11
updated: 2026-07-11
summary: "Template for documenting a sell/trim decision as a decision record."
---

# Sell Decision Template

For material exits or trims where the reasoning deserves a standalone record
beyond the stock page's Decision History row. File it as
`portfolio/decisions/<YYYY-MM-DD>-<TICKER>-sell.md` and add it to the stock
page's `related` list. For routine decisions, a Decision History row plus a
decision-log entry is enough — no standalone record needed.

---

```markdown
---
title: "Sell Decision — TICKER (YYYY-MM-DD)"
type: decision-record
tickers: [TICKER]
tags: [sell-decision]
status: final
created: YYYY-MM-DD
updated: YYYY-MM-DD
summary: "One sentence: what was sold and the core reason."
related:
  - ../../stocks/TICKER.md
---

# Sell Decision — TICKER

## Decision

Sell or Trim, size, and effective date.

## Original Thesis Recap

What the position was supposed to do (quote or link the Original Thesis).

## What Changed

The facts that broke or weakened the thesis.

## Alternatives Considered

Hold, trim instead of sell, hedges.

## Lessons

What to do differently next time.
```
