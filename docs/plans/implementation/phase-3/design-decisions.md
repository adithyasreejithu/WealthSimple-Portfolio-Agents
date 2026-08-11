# Phase 3 Design Decisions

**Date:** 2026-08-10
**Status:** Decided during implementation. Binding on Phase 3 code and on
whoever next touches `investment_worksheet.py`, `valuation.py`, or
`investment-analysis-policy.yml`'s `mode_section_map`.

---

## Decision 1 — Technicals come from the run's registered artifact, never recomputed

The Phase 2 HANDOFF left this open: does the worksheet builder call
`security_technicals.compute_security_technicals` directly, or read the
`security-technicals` skill's already-written artifact?

**Decision: read `calculations/security-technicals-<TICKER>-<date>.json` if
a `derived_calculation` evidence record for the ticker exists in the run;
otherwise report technicals as an explicit gap.** `investment_worksheet.py`
does zero DB/network access of its own — its only I/O is reading files
already registered as evidence in the run it was asked to build a worksheet
for (`build_worksheet_for_run`'s `_find_latest_evidence`, matching on the
ticker appearing in `artifact_path` since `EvidenceRecord` carries no
`ticker` field).

Reasons: `00-overview.md` §5.7 lists the worksheet builder's inputs as "the
investment resource bundle... the investment resource bundle's existing
TRACE record" — no DB access. Reading an already-registered run artifact
reuses the skill's own `ok`/`missing`/`not_applicable` grading instead of a
third implementation of that split, which is what the gate's "TRACE
`not_applicable` handling is preserved, not turned into a gap" actually
requires. Sequencing (run `security-technicals` before the worksheet stage)
is a controller/orchestration concern, out of scope for a deterministic
module — no phase task list item asks for one, so none was added.
`tests/test_investment_worksheet.py`'s `MissingTechnicalsTest` and
`BuildWorksheetForRunTest.test_missing_technicals_artifact_is_tolerated`
cover the absent-artifact path: no crash, no recomputation, a named gap.

---

## Decision 2 — `evidence_domain_registry` names don't 1:1 match the bundle's TRACE domain names

`investment-analysis-policy.yml`'s `evidence_domain_registry` names 16
domains (`position, ledger, prices, financials, ..., options, insider,
institutional, funds, ...`) that `analysis-scope.v1`'s
`required_evidence_domains`/`optional_evidence_domains` are drawn from. The
*actual* TRACE record `investment-analyst-resources` writes
(`investment_analyst_resources.py`'s `DOMAIN_MANIFEST`) does not carry a
same-named domain for eight of these: `overview`, `valuation`, `analyst`,
`options`, `news`, `insider`, `institutional`, `funds` are graded as
**membership in a single `live` domain's `ok`/`missing`/`not_applicable`
lists**, not as eight separate top-level TRACE domains. (`ledger` is also
spelled `ledger_summary` in the actual trace.)

**Resolution: `investment_worksheet._DOMAIN_TRACE_MAP` translates every
policy evidence-domain name to where it actually lives in the bundle's
trace** — either a real top-level domain (`("domain", name)`) or a
membership test inside `live` (`("live_field", group)`). `market_context`
and `company_filings` have no producer at all this phase (Phase 10/6) and
are hardcoded `missing` — matching `thesis_validation.confidence_cap`'s
existing rule that an unrecognized required domain caps confidence exactly
like one TRACE already graded `missing`, not like a free pass.

**Consequence for `evidence_health.scoped_completeness_pct`:** it is
computed **per-domain, not per-field**, because the `live_field` domains
only ever carry group-level grading in the bundle trace to begin with — a
field-level percentage there would be false precision the source data
doesn't support. This is coarser than the bundle's own
`trace.completeness_pct` (which *is* field-level, just unscoped to this
run's required domains) — both numbers are carried in `evidence_health`
(`scoped_completeness_pct` and `bundle_completeness_pct`) so neither is lost.

---

## Decision 3 — Valuation and scenarios are explicit placeholders, not real assumptions

No peer-multiple dataset or DCF growth/discount-rate source exists yet
(both are Phase 4-analyst-supplied or Phase 6-company-research inputs).
`valuation.py` therefore **reconstructs the metric the current market
multiple already prices in** (so `mid` always equals today's market value
by construction: EPS × trailing P/E, EV/implied-revenue × EV/Revenue, etc.)
and applies `DEFAULT_SENSITIVITY_BAND = 0.15` (±15%) around that multiple to
produce `low`/`high`. Every method's `normalization_adjustments` names this
explicitly (`"current_multiple_sensitivity_band"`), and every
`sensitivity_note` states in prose that this is not a growth/discount-rate
estimate — so nothing downstream can mistake it for an independent
valuation opinion. `scenarios.py` derives bull/base/bear directly from this
range (lowest `low` → bear, highest `high` → bull, median-`mid` method →
base) with a fixed 25/50/25 split, and every scenario's
`revenue_assumption` states the same placeholder disclaimer.

This is a deliberate scope choice, not an oversight: the worksheet must
always carry a schema-valid `valuation_methods`/`scenario_inputs` block (a
single point estimate is invalid per `investment_thesis_schema.md` §7, and
Phase 4's analyst needs *something* structured to interpret or override),
but Phase 3 has no legitimate source for real growth/discount assumptions.
Superseded whenever Phase 4's analyst supplies real assumptions, or a real
peer-multiple/DCF-input source is built.

**Method selection is data-availability-driven, not exhaustive:**
`earnings_multiple`, `fcf_yield`, `ev_revenue`, `ev_ebitda` are the four
methods this phase's inputs (the bundle's `live.valuation` group and
`derived` ratios) can support without fabricating anything.
`simplified_dcf`/`reverse_dcf`/`sum_of_the_parts`/`nav` from the schema's
`ValuationMethodName` enum are legitimate future additions once their
required inputs exist — not implemented here because there is nothing to
compute them from yet. A method is omitted from the list (never filled with
a guess) when its own inputs are missing; `test_no_valuation_methods_means_no_scenarios`
covers the resulting empty-list / `None`-scenarios cascade.

---

## Decision 4 — Context-size ceiling: 12,000 characters

`CONTEXT_SIZE_CEILING_CHARS = 12_000` (~3,000 tokens), chosen against the
PLTR fixture's actual rendered context (~1,600 characters for a fully
populated run with 4 valuation methods, a full technicals block, and 7
gap/unknown lines) — the ceiling leaves roughly 7x headroom for a run with
more financial periods, more valuation methods, or a longer unknowns list,
while still being tight enough that a raw series accidentally dumped into a
block (a full options chain, a full OHLCV history) would blow past it
immediately. `ContextSizeCeilingTest.test_no_raw_price_series_in_context`
backs this with a structural check (≤20 bullet lines per `##` section)
rather than relying on the character ceiling alone to catch a regression.

---

## Decision 5 — Found and fixed: two `mode_section_map` entries didn't cover all 16 sections

While writing `build_analysis_scope` (which asserts `AnalysisScope`'s own
`_sections_partition_registry` validator — evaluate ∪ preserve ∪
not_applicable must equal exactly the 16-entry `section_registry`), two of
the seven `investment-analysis-policy.yml` `mode_section_map` entries failed
that check:

- **`material_event`**: `evaluate_sections` (6) ∪ `preserve_sections` (9) =
  15 sections. `valuation` appeared in neither list.
- **`price_move_review`**: `evaluate_sections` (6) ∪ `preserve_sections` (8)
  = 14 sections. `catalysts` and `risks_and_disconfirming_evidence` appeared
  in neither list.

This is a genuine Phase 0 data-completeness defect, not a tunable the phase
gate asked to revisit — every mode must partition all 16 sections by
construction, and two silently didn't. **Fixed by adding the missing
sections to `preserve_sections`** (the conservative default already used for
every other unlisted-but-relevant section in these two modes: byte-identical
carry-forward, no new evidence required, consistent with `material_event`'s
own `extension_rule` — "extension only adds sections; it never removes one
from the default evaluate_sections list" — which already assumes extension
moves sections *into* `evaluate_sections` from a `preserve_sections`
baseline, not from nowhere). Verified all seven modes now partition cleanly
with a one-off script (kept as `BuildAnalysisScopeTest.test_every_policy_mode_produces_a_valid_scope`,
which loops every registered mode and asserts scope construction doesn't
raise — a regression here means the policy file broke the partition again).

---

## Decision 6 — `prior_thesis` and `market_context` stay optional passthrough params

Combined-plan Stage 6 lists a prior-KB-thesis loader and a Market Researcher
digest as worksheet inputs, but neither is buildable in Phase 3: the current
KB stock pages (`Knowledge-Base/stocks/*.md`) are markdown with a
Decision/Confidence/Time-Horizon front matter, not the versioned
`PriorThesisRef` artifact (`artifact_id`/`version`/`content_hash`) this
schema expects — that's Phase 5/7 territory. The Market Researcher doesn't
exist until Phase 10. `build_worksheet` accepts both as optional
already-loaded dicts; when absent, the worksheet carries
`{"exists": false}` / `{"available": false}` and an explicit `unknowns`
entry rather than a silent gap. No loader code was written for either this
phase, matching the roadmap's four-file deliverable list.

---

## Summary of the file boundary for Phase 3

| Action | Files |
|---|---|
| **Create** | `src/workspace/investment_worksheet.py`, `financial_metrics.py`, `valuation.py`, `scenarios.py`; `tests/test_investment_worksheet.py`; `tests/fixtures/investment_analyst/*.json` |
| **Modify** | `src/workspace/analysis_models.py` (Decision 2's `get_mode_section_map()` accessor only — additive, no existing behavior changed); `config/policies/investment-analysis-policy.yml` (Decision 5 — a data-completeness fix, not a redesign) |
| **Read, never write** | `investment-analyst-resources` bundles (`evidence/*.json`), `security-technicals` artifacts (`calculations/security-technicals-*.json`) |
| **Must not appear in the diff** | `src/portfolio_metrics.py`, `src/analytics.py`, `src/security_technicals.py`, `dashboard/**`, `.claude/skills/investment-analyst-resources/**`, `.claude/skills/security-technicals/**` |
