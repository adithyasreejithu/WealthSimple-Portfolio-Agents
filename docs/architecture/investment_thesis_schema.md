# `investment-thesis.v1` schema (Phase 0 draft)

**Status:** approved draft — Phase 0 deliverable of
[`investment-analyst-rebuild-roadmap.md`](../plans/investment-analyst-rebuild-roadmap.md).
Not yet enforced by code; Phase 1 (`src/workspace/analysis_models.py`,
`src/workspace/thesis_validation.py`) turns this into pydantic models and a
deterministic validator. This document is the contract those models must
match.

**Companion documents:**
[`analysis_scope_schema.md`](analysis_scope_schema.md) (the request-side
contract this artifact is produced against),
[`../../Knowledge-Base/taxonomy/investment-analysis-policy.yml`](../../Knowledge-Base/taxonomy/investment-analysis-policy.yml)
(the tunable policy this schema reads: section registry, TRACE thresholds,
confidence caps, vocabulary).

Example artifacts: [`investment_analyst_examples/`](investment_analyst_examples/).

---

## 1. What this artifact is and is not

`investment-thesis.v1` is the Investment Analyst's sole output. It states
whether a security is fundamentally attractive, at what valuation stance, and
why — with every factual claim traced to registered evidence.

It is **not** a portfolio decision. Per the rebuild's most important decision
(`00-overview.md` §1, confirmed in `stock-analysis-agent-design-review.md`
"Decisions already made"), this schema **forbids** any field that assigns
position size, capital amount, target weight, trade timing, or a
Buy/Sell/Hold/Trim/Add/Watchlist/Avoid-style portfolio action. Those belong
to the later, separate Portfolio Manager (Phase 11) artifact, which is a
different schema consuming this one as an input.

## 2. Forbidden fields (validator hard-fails on any of these)

The validator (Phase 1) must reject an artifact — anywhere in its tree, not
only at the top level — that contains:

- `target_weight`, `target_portfolio_weight`, `position_size`,
  `capital_to_deploy`, `shares_to_buy`, `shares_to_sell`, `dollar_amount`
- `trade_action`, `order_type`, `broker`, `fill_price`, `fill_quantity`,
  `execution_venue`
- `portfolio_action` or any field whose value is drawn from
  `decision-framework.yml`'s `actions` enum (`Buy|Sell|Hold|Trim|Add|
  Watchlist|Avoid`) — that enum is reserved for the Portfolio Manager
- `confidence` expressed as a bare numeric percentage (e.g. `78`, `"78%"`) —
  confidence fields must be one of `high|medium|low`
- any field named `score` holding an unanchored 1–10 value — the rubric's
  anchored 1–5 scale belongs to the legacy `decision-rubric.yml` path only
  and is not part of this schema

A fixture containing `target_weight` is a **required** Phase 1 test case
(roadmap Phase 1 gate).

## 3. Top-level shape

```yaml
schema: investment-thesis.v1
run_id: string                       # workspace run this artifact belongs to
artifact_id: string                  # th_<hex>, unique per generation (not per ticker)
generated_at: datetime               # ISO 8601, UTC
security:
  ticker: string
  provider_symbol: string            # verified yfinance/provider symbol, may differ from ticker
  asset_track: equity | etf
analysis_mode: initial_research | scheduled_review | earnings_update
              | material_event | price_move_review | thesis_monitor
              | portfolio_decision
policy_version: string               # investment-analysis-policy.yml `version` this run applied
worksheet_ref:
  path: string                       # run-relative path to the worksheet JSON
  hash: string                       # sha256:...
prior_thesis:
  exists: bool
  artifact_id: string | null
  version: integer | null
  content_hash: string | null        # sha256 of the KB thesis this run compared against
  approved_at: datetime | null
section_states: {<section_id>: unchanged | changed | not_evaluated | not_applicable}
conclusion: {...}                    # §4
key_claims: [...]                    # §5
sections: {...}                      # §6
valuation: {...}                     # §7
scenarios: {bull: {...}, base: {...}, bear: {...}}   # §8
conditions: {...}                    # §9
known_conflicts: [...]               # §10
unknowns: [string]
evidence_ids_used: [string]          # every evidence_id cited anywhere in this artifact
challenger:
  required: bool
  completed: bool
  material_objections: [string]
validation:
  status: pending | valid | valid_with_warnings | invalid
  errors: [string]
  warnings: [string]
human_review_required: true          # pinned; validator refuses false
```

`section_states` keys are exactly the 16 stable section IDs defined in
`investment-analysis-policy.yml`'s `section_registry` (mirrored below for
convenience — the policy file is the source of truth):

```text
executive_conclusion
thesis_and_variant_perception
company_profile
business_quality
financial_trajectory
expectations_and_results
valuation
price_and_market_context
options_and_positioning
insider_institutional_capital_allocation
market_and_macro_sensitivity
catalysts
risks_and_disconfirming_evidence
scenarios
portfolio_context
conclusion_and_triggers
```

A section marked `not_evaluated` or `unchanged` **must not** have a
corresponding entry in `sections` with new narrative content (validator rule,
`00-overview.md` §5.10: "preserved sections have no replacement content").
A section marked `not_applicable` (e.g. `expectations_and_results` for an
ETF with no earnings) never appears in `sections` at all.

## 4. `conclusion` block

```yaml
conclusion:
  fundamental_rating: attractive | neutral | unattractive | insufficient_evidence
  valuation_stance: discounted | reasonable | demanding | indeterminate
  thesis_direction: initial | strengthening | unchanged | weakening | broken
  thesis_confidence: high | medium | low
  evidence_completeness_pct: float    # informational, copied from the run's TRACE record — NOT the same axis as thesis_confidence, see policy §confidence_model
  investment_case: string             # one paragraph
  case_against: string                # one paragraph: why this conclusion may be wrong
  most_important_catalyst: string
  most_important_risk: string
  most_important_unknown: string
  analysis_horizon: Short-term | Medium-term | Long-term   # exact strings from decision-framework.yml, reused rather than re-defined
  as_of: datetime
```

`fundamental_rating` / `valuation_stance` / `thesis_direction` /
`thesis_confidence` are the rating vocabulary confirmed in
`stock-analysis-agent-design-review.md` "Decisions already made" and
`00-overview.md` §3.1. No numeric score stands in for any of these — see
Doc A's change-table item 2 and 3 (percentages and 1–10 scores are both
rejected).

`thesis_confidence` is capped deterministically by the validator, not
self-assigned by the analyst without limit — see
`investment-analysis-policy.yml`'s `confidence_caps` block.

## 5. `key_claims` — the variant-perception core

```yaml
key_claims:
  - claim_id: thesis_1
    statement: string
    claim_type: fact | inference | assumption | opinion
    importance: critical | supporting
    evidence_ids: [ev_xxx, ...]
    status_vs_prior: new | strengthened | unchanged | weakened | invalidated
    confidence: high | medium | low
```

2–4 claims is the target density (`00-overview.md` §6.2: "which two to four
claims carry most of the thesis"). `importance: critical` claims are the ones
the confidence-cap and challenger-trigger policy read.

## 6. `sections` — narrative content, only for `changed` sections

```yaml
sections:
  <section_id>:
    narrative: string                 # prose, cites evidence inline as (ev_xxx)
    evidence_ids: [string]
    capability_label: null | snapshot_only | current_snapshot_only | history_available
                                       # required for options_and_positioning and
                                       # expectations_and_results per §6.9/§6.6 of
                                       # 00-overview.md — states what the data can and
                                       # cannot support, e.g. "snapshot_only" until
                                       # historical IV/OI or estimate-revision history
                                       # exists (Phase 12 / Phase 8)
```

Each `section_id` present here must resolve to a `section_states` entry of
`changed`. The `evidence_ids` list is checked against the run's
`evidence/sources.jsonl` registry exactly as `AgentOutput.evidence_ids_used`
already is (`src/workspace/validation.py` pattern, reused not reinvented).

## 7. `valuation` — Python-calculated, analyst-interpreted

```yaml
valuation:
  methods:
    - method: earnings_multiple | fcf_yield | ev_revenue | ev_ebitda
             | dividend_or_dcf_distributable | simplified_dcf | reverse_dcf
             | sum_of_the_parts | nav
      base_metric: string
      base_period: string
      normalization_adjustments: [string]
      assumptions: {growth: ..., margin: ..., discount_rate: ..., terminal: ...}
      currency: string
      resulting_equity_value_per_share: {low: float, mid: float, high: float}
      sensitivity_note: string
  interpretation: string               # analyst prose over the Python-computed ranges
```

A single point target without a range is invalid (`00-overview.md` §6.7).
The range and every method's arithmetic is Python-computed by
`src/workspace/valuation.py` (Phase 2/3); the analyst may only interpret it,
never restate a different number.

## 8. `scenarios`

```yaml
scenarios:
  bull: {probability: float, horizon: string, revenue_assumption: string,
         margin_assumption: string, dilution_assumption: string,
         valuation_method: string, fair_value_per_share: float,
         expected_return_pct: float, conditions: [string]}
  base: {...same shape...}
  bear: {...same shape...}
```

Validator rules (unchanged from `00-overview.md` §6.14, restated here for
schema completeness):

- `bull.probability + base.probability + bear.probability == 1.0` (± float
  tolerance, e.g. 0.001)
- `fair_value_per_share` and `expected_return_pct` recompute from the stated
  assumptions and current price
- bear is not mechanically required to sit below the current price, but an
  unusual ordering must be explained in `conditions`

## 9. `conditions` — what changes the conclusion

```yaml
conditions:
  upgrade_conditions:
    - trigger_id: string
      metric: string                  # a worksheet-computable metric name
      operator: gte | lte | eq | qualitative
      threshold: float | null
      confirmation_periods: integer | null
      affected_rating: attractive | neutral | unattractive
      qualitative_description: string | null   # required when operator == qualitative
  downgrade_conditions: [...]          # same shape
  invalidation_conditions: [...]       # same shape, affected_rating typically unattractive
  monitoring_items: [string]
```

## 10. `known_conflicts`

```yaml
known_conflicts:
  - conflict_id: cf_001
    field: string
    values: [{value: ..., evidence_id: ev_xxx}, {value: ..., evidence_id: ev_yyy}]
    resolution: unresolved | source_precedence | period_mismatch | restatement
    selected_value: null
    notes: string
```

Reused unchanged from `00-overview.md` §7.3 — the worksheet builder produces
these, the analyst may not silently pick a side.

## 11. Validation states

```text
pending             — not yet run through the validator
valid               — no errors, no warnings
valid_with_warnings — no errors; analytical-limitation warnings present
                       (e.g. thesis_confidence capped, a section is
                       snapshot_only, TRACE completeness below the warning
                       threshold)
invalid             — schema, citation, arithmetic, scope, or forbidden-field
                       error; may not proceed to human review
```

Only `valid` and `valid_with_warnings` artifacts may proceed to human review
and (later) KB promotion, per `00-overview.md` §5.10.

## 12. Non-goals of this draft

This document defines shape and validation rules only. It does not:

- implement the pydantic models (Phase 1);
- define the worksheet JSON that feeds this artifact (Phase 3);
- define the ETF-track variant of this schema in full (deferred per
  `00-overview.md` §9 — ETF support is designed for, not built, in v1);
- define the ETF asset-track's exact `not_applicable` section list beyond
  the two called out in `investment-analysis-policy.yml`
  (`expectations_and_results`, `insider_institutional_capital_allocation`)
  — Phase 3/9 will refine this as real ETF runs are produced.
