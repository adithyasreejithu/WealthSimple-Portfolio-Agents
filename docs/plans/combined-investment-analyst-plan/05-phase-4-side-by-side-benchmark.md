# Phase 4 — Side-by-side benchmark before adding breadth

**Priority:** critical  
**Dependency:** Phase 3

Compare separately produced legacy output and rebuilt-analyst output for the same securities and dates. The rebuilt workflow must not invoke the legacy agents or their non-resource skills.

Benchmark set should include:

- one well-covered growth stock;
- one dividend/value stock;
- one Canadian/TSX listing with mapping/currency complexity;
- one company with sparse analyst/options coverage;
- one unowned/watchlist ticker;
- one ETF, evaluated only to confirm the equity track rejects/routes it correctly.

Score:

| Dimension | Measurement |
|---|---|
| Factual accuracy | Verified claims / sampled factual claims |
| Citation quality | Claims with valid and specific support |
| Coverage | Required sections adequately addressed |
| Unknown discipline | Missing evidence explicitly acknowledged |
| Stability | Material conclusion changes with identical inputs |
| Scope isolation | Unrequested sections unchanged in incremental runs |
| Reasoning value | Human review of insights not captured by rubric |
| Cost | Agent token/log usage per ticker |
| Latency | End-to-end duration per ticker |
| Operational reliability | Failed/partial runs and causes |
| Resource TRACE quality | Existing completeness, graded, missing, not-applicable, failed-domain, and workspace-outcome fields |

Do not retire the separate legacy path unless the new path passes integrity gates and provides materially better thesis value at acceptable cost. TRACE percentages are recorded for comparison, but no universal pass threshold is assumed here.

[Back to overview](00-overview.md)
