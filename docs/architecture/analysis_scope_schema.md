# `analysis-scope.v1` schema (Phase 0 draft)

**Status:** approved draft — Phase 0 deliverable of
[`investment-analyst-rebuild-roadmap.md`](../plans/investment-analyst-rebuild-roadmap.md).
Not yet enforced by code; Phase 1 turns this into a pydantic model
(`src/workspace/analysis_models.py`) and Phase 3's worksheet builder is the
first consumer that enforces it.

**Companion documents:**
[`investment_thesis_schema.md`](investment_thesis_schema.md) (the artifact
produced under this scope),
[`../../config/policies/investment-analysis-policy.yml`](../../config/policies/investment-analysis-policy.yml)
(`mode_section_map` and `evidence_domain_registry` are this schema's
deterministic source of truth — the scope mapper reads the policy file, it
does not hardcode section lists).

Example artifacts: [`investment_analyst_examples/`](investment_analyst_examples/).

---

## 1. Purpose

`analysis-scope.v1` is produced once per run, before any resource collection
or worksheet building happens. It answers, deterministically (never by agent
judgment): which of the 16 report sections does this run evaluate, which does
it leave untouched, which evidence domains are required versus optional, and
what happens if a required domain can't be filled.

It extends the existing workspace `Request` (`src/workspace/models.py`)
rather than replacing it — a run still has a `request.yaml`; this is an
additional, optional structured scope attached to that request.

## 2. Top-level shape

```yaml
schema: analysis-scope.v1
run_id: string
subject: string                      # ticker
asset_track: equity | etf
mode: initial_research | scheduled_review | earnings_update
    | material_event | price_move_review | thesis_monitor | portfolio_decision
trigger:
  type: schedule | earnings | material_event | price_move | user_request | monitor_trigger
  occurred_at: datetime | null
  detail: string | null
decision_horizon: Short-term | Medium-term | Long-term   # exact strings from decision-framework.yml
evaluate_sections: [string]           # subset of the 16 stable section IDs
preserve_sections: [string]           # subset of the 16; byte-identical carry-forward from prior_thesis
not_applicable_sections: [string]     # subset of the 16; asset_track makes these meaningless
required_evidence_domains: [string]
optional_evidence_domains: [string]
critical_gap_policy: stop | proceed_with_gap_disclosure
```

Invariant checked by the validator: `evaluate_sections`, `preserve_sections`,
and `not_applicable_sections` are pairwise disjoint, and their union is
exactly the 16-entry `section_registry` from
`investment-analysis-policy.yml`. Every section has exactly one state; there
is no implicit fourth bucket.

## 3. Field notes

### `mode`

One of the seven run modes defined in `00-overview.md` §5.1. `mode` is the
only input the deterministic scope mapper needs to look up
`investment-analysis-policy.yml`'s `mode_section_map` and produce
`evaluate_sections` / `preserve_sections` / `required_evidence_domains` /
`optional_evidence_domains` / `critical_gap_policy` automatically. An agent
never hand-picks these lists — that would defeat the purpose of a
deterministic scope contract.

`material_event` and `thesis_monitor` have a **policy default** section set
that the scope mapper may extend (never shrink) based on `trigger.detail`
mapping to specific prior `key_claims` or `conditions` entries — e.g. a
management-change event pulls in `business_quality` even though the default
`material_event` set does not evaluate it by default. This extension logic
lives in the worksheet builder (Phase 3), not in this schema.

### `decision_horizon`

Reuses `decision-framework.yml`'s `time_horizons` enum verbatim rather than
introducing a second horizon vocabulary. This is a scope-level input (how far
out is the question being asked), distinct from `conclusion.analysis_horizon`
in the thesis artifact (the analyst's own horizon judgment) — they usually
agree but are not the same field, so both are kept.

### `required_evidence_domains` / `optional_evidence_domains`

Domain names are the top-level group names already produced by
`investment-analyst-resources` and `market-analyst-resources`:

```text
position, ledger, prices, financials, earnings, dividends, classification,
portfolio_context, overview, valuation, analyst, options, news, insider,
institutional, funds, market_context, company_filings
```

`company_filings` is listed for forward compatibility with Phase 6
(`company-research-resources`) and is not obtainable in any run before that
phase ships — a scope that requires it before Phase 6 will always report it
`missing`, never fabricate it.

### `critical_gap_policy`

Two values only, matching `00-overview.md`'s Stage 7 behavior:

- `stop` — a required evidence domain that fails TRACE (see policy file's
  `trace_policy.blocking_threshold`) halts the run before the analyst is
  invoked; the run's status becomes `insufficient_evidence` and no
  `investment-thesis.v1` artifact with `validation.status: valid*` can be
  produced without a human override.
- `proceed_with_gap_disclosure` — the run proceeds; every unmet required
  domain must appear in the resulting thesis's `unknowns` list, and
  `thesis_confidence` is capped per `investment-analysis-policy.yml`'s
  `confidence_caps`.

`initial_research` and `earnings_update` default to `stop` (a first
impression or an earnings read built on a large data hole is worse than no
output). The remaining five modes default to
`proceed_with_gap_disclosure` (a monitor or incremental update should not
become unusable because one optional domain is stale). See the policy file's
`mode_section_map` for the authoritative per-mode default — this document
states the rationale, the policy file states the value.

## 4. Non-goals of this draft

- Does not implement the deterministic scope mapper function (Phase 3).
- Does not define the `material_event`/`thesis_monitor` claim-to-section
  extension algorithm in full — only that it may extend, never shrink, the
  policy default (Phase 3/9).
- Does not define ETF-track `not_applicable_sections` beyond the two entries
  already fixed in the policy file.
