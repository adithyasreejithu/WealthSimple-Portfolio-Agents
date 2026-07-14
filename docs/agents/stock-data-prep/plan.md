# Stock Data Prep — Design Notes

Part of the stock decision-support scoring system. The approved design is in
[`docs/plans/implementation/stock_decision_support_scoring.md`](../../plans/implementation/stock_decision_support_scoring.md).

## Why a separate agent

One Claude Code agent runs on one model. The workflow deliberately splits the
cheap, mechanical steps (fetch data, build the worksheet) from the expensive
judgment (score criteria, write the Analyst View). `stock-data-prep` is the
cheap half on `haiku`; `stock-analyst` is the judgment half on `opus`
(Opus 4.8). Keeping them separate means the fetch/scaffold work never pays for
the stronger model, and the judgment work always gets it.

## Boundary

This agent stops at a filled-in worksheet with empty score/result slots. It
never scores, opines, or writes to the wiki. Everything it produces lands in
`exports/stock-recommendations/` and is input for `stock-analyst`.
