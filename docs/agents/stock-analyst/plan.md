# Stock Analyst — Design Notes

Part of the stock decision-support scoring system. The approved design is in
[`docs/plans/implementation/stock_decision_support_scoring.md`](../../plans/implementation/stock_decision_support_scoring.md).

## The core idea

The LLM is not trained on trading data and cannot be trusted to predict markets.
So it never does. Instead, the portfolio owner authors an explicit rubric —
hard gates plus weighted 1-5 criteria with written anchors — and this agent's
job is to *apply* that rubric to fetched evidence: score each criterion, cite
the evidence, and let deterministic scripts recompute the weighted score,
verdict, and confidence. The judgment is "does this evidence meet this written
criterion", not "what will the stock do".

## Why Opus, why read-only

Scoring criteria against evidence and writing the Analyst View is the one
judgment step, so it runs on Opus 4.8. It stays read-only on the wiki to
preserve the single-writer invariant: `kb-intake` remains the only agent that
writes thesis pages, ingesting this agent's recommendation artifact.

## Analyst View

A deliberate escape hatch: the model records its own opinion separately from the
mechanical verdict. It cannot override the band-derived action, but its dissent
is captured so the owner can retune the rubric via `author-decision-rubric`.
