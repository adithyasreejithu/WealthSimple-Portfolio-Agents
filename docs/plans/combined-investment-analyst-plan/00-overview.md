# Combined Investment Analyst — Architecture and Implementation Overview

**Status:** proposed implementation plan  
**Scope:** security research, evidence preparation, thesis generation, validation, monitoring, and handoff to later portfolio decision-making  
**Primary inputs combined:** the institutional-style Stock Analysis Agent design, the repository's investment-analyst v2 design, the implemented `investment-analyst-resources` and `market-analyst-resources` skills, and the implemented run-workspace controls  
**Most important decision:** the Investment Analyst decides whether a security is fundamentally attractive. It does not decide portfolio position size, place trades, or own the final Buy/Hold/Trim/Sell portfolio action.

**Rebuild constraint:** this is a from-scratch agent and skill rebuild. The only existing skills retained by the new runtime are `investment-analyst-resources` and `market-analyst-resources`. More generally, the only skills permitted in the rebuilt workflow are purpose-built skills whose names end in `-resources`. Existing non-resource skills and all existing agents are excluded from the target runtime. They may be inspected or run separately as benchmark references, but they are not dependencies of the rebuilt system.

---

## 1. Executive decision

The target system should combine the broad analytical coverage of the Stock Analysis Agent design with the operational discipline already present in this repository.

The external design contributes the strongest **research framework**:

- business quality, competitive position, and management analysis;
- financial trajectory and deterministic valuation;
- expectations versus actual results;
- market behavior, options, insider, and institutional context;
- explicit bull, base, and bear cases;
- catalysts, risks, invalidation conditions, and recommendation-change triggers;
- a readable investment memorandum rather than only a numeric score.

The repository contributes the stronger **execution framework**:

- DB-first data reuse through `investment-analyst-resources`;
- per-domain freshness gates and earnings-window overrides;
- a separate, reusable market data layer through `market-analyst-resources`;
- one auditable run workspace per research question;
- registered evidence IDs, content hashes, explicit gaps, and append-only audit events;
- deterministic calculations and validators;
- read/write isolation for DuckDB concurrency;
- a controlled Knowledge Base writer rather than direct analyst edits;
- human review and a hard prohibition on trade execution.

The combined design is therefore:

```text
Request and analysis scope
        |
        v
Run workspace
        |
        +--> Investment Analyst Resources (implemented)
        +--> Company Research Resources (new)
        +--> Market Analyst Resources (implemented, incomplete coverage)
        +--> Prior validated thesis / KB context
        |
        v
Deterministic context and worksheet builder (new)
        |
        v
Investment Analyst — judgment only (new)
        |
        +--> Conditional challenger pass (new)
        |
        v
Deterministic thesis validator (new)
        |
        v
Validated security thesis
        |
        +--> Human-reviewed KB promotion
        +--> Monitoring triggers
        +--> Portfolio Manager handoff (later phase)
```

### Where development should start

Do **not** start by building more data collectors, a Portfolio Manager, technical-analysis features, or ten separate analytical skills. Start by defining and validating the artifact that every later component must produce or consume.

The first implementation slice should be:

1. Define `investment-thesis.v1` and `analysis-scope.v1`.
2. Implement deterministic validation for those contracts.
3. Build a worksheet adapter over the already implemented `investment-analyst-resources` bundle and run workspace.
4. Implement the Investment Analyst against that worksheet.
5. Compare its output side by side with separately produced legacy output on PLTR or another well-covered holding. The rebuilt workflow must not invoke the legacy agents or their non-resource skills.

This produces a useful, testable vertical slice before introducing more sources. It also reveals which missing external evidence actually affects decisions instead of speculatively building every possible collector.

---

## 2. Goals, non-goals, and success definition

### 2.1 Goals

The system must:

1. Produce a traceable, evidence-backed security thesis.
2. Separate facts, deterministic calculations, assumptions, and analyst judgment.
3. Evaluate business quality, financial trajectory, valuation, expectations, catalysts, risks, and scenario outcomes.
4. State what the market may be mispricing.
5. Identify what would strengthen, weaken, or invalidate the thesis.
6. Reuse stored data and refresh only what is stale or event-sensitive.
7. Record missing, stale, conflicting, or unavailable evidence explicitly.
8. Support first-time research, scheduled reviews, earnings updates, material-event updates, and price-move reviews.
9. Preserve unaffected thesis sections during incremental updates.
10. Produce a compact model context while retaining the full evidence bundle on disk.
11. Validate citations, hashes, schema, arithmetic, scenario probabilities, scope behavior, and prohibited actions.
12. Keep durable KB updates separate from research generation and subject to review.
13. Support a later Portfolio Manager without embedding portfolio-action authority in the analyst.
14. Remain measurable against the current v1 workflow for quality, cost, latency, and stability.

### 2.2 Non-goals for the first release

The first release will not:

- execute trades or connect analysis output directly to a broker;
- decide final position size, target weight, account placement, or tax treatment;
- recommend a specific options strategy;
- claim complete options-flow analysis without historical IV/OI data;
- claim robust insider intent analysis without filing-level transaction context;
- claim institutional trend analysis from a single current holders table;
- replace the existing stock analyst before a benchmark and migration decision;
- implement autonomous open-ended web browsing inside the analyst;
- create a separate agent for every report section;
- treat arbitrary numeric confidence percentages as calibrated probabilities;
- force equities and ETFs through one analytical schema.

### 2.3 Success definition

A first production-capable version is complete when a single command or documented handoff sequence can:

1. create or attach to one run;
2. register the request and analysis scope;
3. gather a valid investment resource bundle;
4. load relevant prior thesis context;
5. build a compact, deterministic worksheet;
6. generate a structured equity thesis;
7. validate every cited evidence ID and calculation;
8. state material gaps without inventing replacements;
9. preserve out-of-scope sections on an incremental run;
10. produce a human-readable report and machine-readable artifact;
11. leave the artifact pending human review;
12. perform no KB write or trade action automatically.

---

## 3. Non-negotiable architectural decisions

These decisions resolve contradictions between the two source designs and should be made before coding.

| Decision | Selected design | Reason |
|---|---|---|
| Security rating vs portfolio action | Analyst outputs security attractiveness; Portfolio Manager outputs Buy/Hold/Trim/Sell and sizing | Security quality and portfolio suitability are separate decisions |
| Data retrieval | Controlled resource skills run before judgment | Prevents uncontrolled retrieval and makes evidence reproducible |
| Analyst access | Analyst consumes a manifest and worksheet, not raw DB/network access | Keeps reasoning focused and evidence bounded |
| Gap handling | One bounded evidence-gap request, then proceed with explicit unknowns or stop if critical | Allows necessary follow-up without open-ended research |
| Calculations | Python owns all repeatable arithmetic | Prevents model math drift |
| Confidence | Categorical, with deterministic evidence coverage separate from analyst thesis confidence | Avoids false precision such as “78% confidence” |
| Incremental updates | Explicit scope plus per-section patch state | Prevents unrelated thesis rewrites and partial-data full recommendations |
| Durable writing | A new, from-scratch promotion component is the only KB writer | Preserves review and avoids concurrency problems without depending on a legacy skill or agent |
| Storage | Extend `workspace/runs/<run_id>/`; do not create `research-v2/` | The run workspace already solves request isolation and auditability |
| Skills | Only purpose-built `*-resources` skills; worksheet, validation, rendering, and promotion remain deterministic modules | Enforces the from-scratch boundary and prevents non-resource workflow skills from becoming hidden runtime dependencies |
| Migration | Run v1 and the new analyst side by side until acceptance gates pass | Makes quality and regression visible |
| Asset classes | Equity and ETF schemas are separate tracks | Their evidence and evaluation criteria differ materially |

### 3.1 Rating vocabulary

The Investment Analyst should not use portfolio-action terms as its primary conclusion. Recommended security-level vocabulary:

```text
fundamental_rating: attractive | neutral | unattractive | insufficient_evidence
valuation_stance: discounted | reasonable | demanding | indeterminate
thesis_direction: strengthening | unchanged | weakening | broken | initial
thesis_confidence: high | medium | low
```

The current rubric result can remain available during the benchmark as a separate field:

```text
legacy_policy_view:
  action: hold
  confidence: medium
  rubric_version: ...
```

It must not be silently relabeled as the new analyst's conclusion.

### 3.2 Existing-component reuse boundary

The reuse boundary is intentionally narrow:

| Existing component | Target treatment |
|---|---|
| `investment-analyst-resources` | Retain as the security resource skill |
| `market-analyst-resources` | Retain as the market resource skill |
| Any other existing skill | Do not invoke or extend in the rebuilt runtime |
| Any existing agent | Do not invoke or extend in the rebuilt runtime |
| Run workspace and shared Python infrastructure | Retain as infrastructure; these are not agent or skill dependencies |

New agents are created from blank definitions. New data-collection skills must be purpose-built `*-resources` skills. Non-resource deterministic work belongs in Python modules or CLI stages rather than Claude skills.

This changes several names from the source design:

- `build-investment-worksheet` becomes a new deterministic module/stage, not a skill;
- `validate-investment-thesis` becomes a new deterministic module/stage, not a skill;
- KB promotion becomes a new deterministic, human-gated component, not an extension of `kb-intake` or `kb-update-thesis`;
- the legacy stock workflow is an external benchmark/control only, never a callable branch inside the rebuilt workflow.

### 3.3 Existing workspace TRACE methodology

The rebuilt workflow must integrate the TRACE behavior already implemented in the workspace. TRACE is data-collection observability; it is not an analytical signal and does not replace evidence-quality or thesis-confidence judgments.

The workspace source of truth is `src/skill_trace.py`, documented in `docs/architecture/usage_tracking.md`. Its current producers are `investment-analyst-resources` and `market-analyst-resources`.

| TRACE element | Existing workspace behavior to retain |
|---|---|
| Field outcomes | `ok`, `missing`, or `not_applicable` |
| Completeness denominator | `ok + missing`; `not_applicable` is reported but excluded |
| Human-readable output | `logs/SkillTrace.txt`, one scannable line per invocation |
| Structured output | `logs/SkillTrace.jsonl`, including field names and per-domain detail |
| Run audit output | `trace_recorded` in the run's `audit_log.jsonl` when attached to a run |
| Workspace outcome | `created`, `attached`, or `skipped`, with a skip reason when applicable |
| Failure behavior | TRACE writing never raises and must not sink an otherwise successful data pull |

`investment-analyst-resources` also carries its trace in the digest and bundle. `market-analyst-resources` maps `fetched` and `ok` to TRACE `ok`; `stale`, `overdue`, `no_data`, `input_missing`, and `stale_leg` to `missing`; and `not_configured` to `not_applicable`.

The workspace does not currently define a universal TRACE percentage that blocks an analyst run. That policy is intentionally left blank:

```text
TRACE blocking threshold: ______________________________
TRACE warning threshold:  ______________________________
Who may override a TRACE-based block: __________________
```

### 3.4 What is retained, changed, or deferred from the two source plans

| Source-plan feature | Decision | Combined implementation |
|---|---|---|
| Institutional-style 15-section report | Retain, with restructuring | Keep the analytical coverage, but use stable section IDs and a typed artifact |
| Context Builder before the LLM | Retain and strengthen | Implement it as the deterministic worksheet builder over registered run evidence |
| Agent calls arbitrary tools when it decides it needs data | Change | Run controlled baseline acquisition first; permit one structured evidence-gap loop |
| Python calculates, LLM interprets | Retain | Make the worksheet and validator authoritative for all repeatable calculations |
| Strong Buy/Buy/Hold/Trim/Sell/Avoid from the analyst | Change | Analyst emits security attractiveness; Portfolio Manager later emits portfolio action |
| Portfolio Fit section in the analyst | Narrow | Show current context as information, but prohibit sizing/action conclusions |
| Options as supporting context | Retain | Label current capability snapshot-only until historical IV/OI exists |
| Technical analysis as context | Retain, defer expansion | Use existing returns now; add relative/technical measures only after the core thesis works |
| Insider and institutional signals | Retain with stronger limitations | Current snapshots are contextual; historical/filer-level interpretation requires new collectors |
| Many narrow analytical skills | Change | Use only purpose-built `*-resources` skills; keep other deterministic work in Python modules and use one coherent analyst |
| Separate research-v2 evidence tree | Reject as superseded | Use the implemented run workspace and evidence registry |
| DB-first analyst resource bundle | Retain | This is the quantitative/security data foundation |
| Separate market resource store | Retain | Reuse its registered indicators and later add a Market Researcher |
| First-run annual context | Retain and improve | Register it as run evidence instead of ephemeral uncited prose context |
| Knowledge Base as durable context | Retain | Promotion is reviewed, scope-safe, and performed only by the KB writer |
| Challenger analyst | Retain conditionally | Use a second bounded invocation when policy triggers, not for every routine run |
| Existing decision rubric | Benchmark reference only | Compare against separately produced legacy output; do not load its non-resource workflow into the rebuilt runtime |
| Portfolio Manager and Options Specialist | Defer | Build only after validated security theses exist |

---

## 4. Target components and ownership

| Component | Type | Responsibility | May not do |
|---|---|---|---|
| Run workspace | Shared infrastructure | Own request, evidence, calculations, outputs, validation state, and audit events | Make investment judgments |
| `investment-analyst-resources` | Existing deterministic skill | DB-first security, position, price, financial, earnings, dividend, classification, live market, and derived data | Write a thesis or KB page |
| `company-research-resources` | New, from-scratch deterministic/controlled resource skill | Gather filings, investor-relations material, transcripts, regulatory documents, and selected material news | Decide what evidence means |
| `market-analyst-resources` | Existing deterministic skill | Gather and persist macro/market indicators and deltas | Characterize the regime |
| Market Researcher | New, later judgment agent | Turn market resources into a versioned market landscape | Re-research each company |
| Prior-thesis loader | New deterministic script or worksheet function | Select current validated thesis and relevant historical claims | Read arbitrary old exports |
| Investment worksheet builder | New deterministic Python module/stage | Normalize evidence, calculate metrics/scenarios, enforce scope, and emit compact context | Form the final thesis |
| Investment Analyst | New, from-scratch strongest-model agent | Interpret worksheet evidence and produce `investment-thesis.v1` | Fetch freely, calculate authoritative metrics, size positions, or write KB |
| Challenger pass | Conditional invocation of a new, from-scratch analyst definition | Seek disconfirming evidence, hidden assumptions, and scenario weaknesses | Replace the primary thesis directly |
| Investment thesis validator | New deterministic Python module/stage | Validate schema, evidence, scope, arithmetic, and guardrails | Self-certify unsupported judgment |
| KB promotion | New deterministic, human-gated component | Promote approved changed sections and append history | Edit out-of-scope sections |
| Portfolio Manager | New, deferred agent | Convert security thesis plus portfolio policy/context into proposed action and size | Modify analyst evidence or execute trades |
| Human reviewer | Required control | Approve, reject, or request changes | Be bypassed by a flag |

### 4.1 Why there is no data-prep agent in the target path

The mechanical work is better represented as deterministic Python modules and resource-skill scripts. The existing v1 data-prep agent is not reused or extended.

For the new path:

- resource collection is performed only by `*-resources` skills;
- worksheet construction is a deterministic Python module/stage;
- manifest generation is a deterministic Python module/stage;
- validation is a deterministic Python module/stage;
- the only required LLM judgment stage is the Investment Analyst;
- a second LLM call occurs only when the challenger policy triggers.

If a small orchestration agent is later needed, it must be created from scratch and must not make judgments.

### 4.2 Section-to-data coverage and required changes

This matrix identifies what can be supplied today, what the new analyst can safely conclude, and what must be built before the full report promise is credible.

| Analyst section | Available now | New work required | Current limitation the report must disclose |
|---|---|---|---|
| Executive conclusion | Resource digest, derived metrics, prior KB decision | Typed thesis conclusion and confidence policy | Business evidence remains shallow before company research |
| Thesis / variant perception | Prior thesis, valuation and analyst snapshots, news | Claim model, consensus comparison, primary-source excerpts | A current analyst snapshot is not full consensus history |
| Company profile | DB identity/details plus live overview | Filing/IR extraction for segments, geography, customers, and revenue model | Aggregator summaries can be incomplete or stale |
| Business quality | Some margins, returns, description, news | Filings, transcripts, competitors, management/capital allocation evidence | Cannot defend moat/management conclusions from yfinance alone |
| Financial trajectory | DB quarterly snapshots, ledger, dividends, derived ratios | Annual-history fallback, normalization, share-count/SBC and segment extraction | Current derived fields are intentionally narrow and some differ from v1 annual metrics |
| Earnings and expectations | Earnings events and current analyst group | Persist dated consensus and guidance snapshots | Revision trends cannot be reconstructed reliably from only today's snapshot |
| Valuation | Current price, market cap/ratios, FCF yield, financial history | Multi-method valuation engine, assumptions, reverse DCF/sensitivities | Provider ratios can mix periods and require normalization |
| Price behavior | Full DB history, current quote, 30/90/365 returns, 52-week range | Benchmark/sector comparison, volatility, beta, drawdown, moving averages | Current quote is not written into DB history by design |
| Options positioning | Current chain, OI/volume ratios, ATM IV, skew, max-OI strikes | Historical IV/OI, Greeks, expected move and anomaly baselines | Snapshot cannot establish percentile, unusual flow, or persistence |
| Insider activity | Current provider insider tables and net shares | Filing parser, transaction codes, ownership percentage, plan/exercise/tax context | Sale intent cannot be inferred safely from current tables |
| Institutional activity | Current holder tables | Historical 13F/equivalent snapshots and entity matching | One snapshot does not establish accumulation/distribution |
| Market/macro | Rates, selected credit, volatility, sectors/factors, FX/commodities | Fill high-value TBD domains and build Market Researcher | Growth, inflation, labor, breadth, and positioning coverage is incomplete |
| Catalysts | Earnings calendar, news, company documents when added | Structured catalyst model and observable confirmation rules | News alone can confuse speculation with scheduled/material events |
| Risks | Financial gaps, news, prior thesis, filings when added | Risk taxonomy, leading indicators, disconfirming-evidence requirement | Generic risk lists add little value without mechanism and monitoring |
| Scenarios | Current price, financials, valuation inputs | Deterministic scenario engine and probability/arithmetic validation | LLM-authored scenario math is not authoritative |
| Portfolio context | Live value/weight/cost/gain, role/account type; analytics functions elsewhere | Separate portfolio resource for look-through, overlap, concentration, risk and policy | Classification may be stale; analyst context is informational only |
| What changes the conclusion | Prior thesis and current metrics | Machine-readable trigger schema and later monitor | Free-form prose cannot drive reliable monitoring |

---

## 5. End-to-end operating workflow

### 5.1 Stage 0 — Request intake and mode selection

Every run begins with a structured request. The request must answer:

- What security is being analyzed?
- What question is being answered?
- Is this a first analysis or an update?
- What event triggered it?
- Which sections are required?
- What decision horizon applies?
- Is the user asking only about the security or also asking for a portfolio action?

Supported initial modes:

| Mode | Trigger | Required behavior |
|---|---|---|
| `initial_research` | No validated prior thesis | Full equity analysis plus long-term history |
| `scheduled_review` | Periodic review | Refresh all decision-relevant sections, compare with prior thesis |
| `earnings_update` | Earnings within configured window or explicit request | Focus on results, expectations, financial trajectory, valuation, catalysts, risks, scenarios |
| `material_event` | Acquisition, regulation, management change, product event, etc. | Research event, map impact to affected claims and sections |
| `price_move_review` | Material price movement | Refresh valuation, price context, news/event cause, scenarios; preserve business facts unless evidence changed |
| `thesis_monitor` | Trigger evaluation | Evaluate stored invalidation and monitoring conditions only |
| `portfolio_decision` | User asks what to do with the position | Run or reuse security thesis, then hand off to Portfolio Manager; analyst still does not size |

### 5.2 Stage 1 — Create the run and analysis scope

Extend the workspace request contract with an optional structured scope while remaining backward compatible with existing `Request` files.

Proposed `analysis-scope.v1` shape:

```yaml
schema: analysis-scope.v1
run_id: 2026-08-09T...
subject: PLTR
asset_track: equity
mode: earnings_update
trigger:
  type: earnings
  occurred_at: 2026-08-08T20:00:00Z
decision_horizon: 12_to_36_months
evaluate_sections:
  - financial_trajectory
  - earnings_and_expectations
  - valuation
  - catalysts
  - risks
  - scenarios
  - conclusion
preserve_sections:
  - company_profile
  - business_quality
  - industry_structure
required_evidence_domains:
  - financials
  - earnings
  - valuation
  - price
  - company_filings
optional_evidence_domains:
  - options
  - institutional
critical_gap_policy: stop
```

The scope mapper must be deterministic. It should translate the run mode into required sections and evidence domains using a versioned policy file, not agent intuition.

Recommended new policy:

```text
Knowledge-Base/taxonomy/investment-analysis-policy.yml
```

This file should hold:

- mode-to-section mappings;
- section-to-required-domain mappings;
- freshness thresholds that are analytical policy rather than source mechanics;
- critical versus optional evidence rules;
- challenger triggers;
- allowed rating vocabulary;
- scenario probability and horizon requirements;
- first-run minimum history requirements.

Changes must require a policy version bump and deterministic validation, following the existing decision-rubric pattern.

### 5.3 Stage 2 — Security resource collection

Run `investment-analyst-resources` with the run ID. Preserve its existing gate/refresh/read concurrency design.

Retain its existing TRACE behavior. The trace in the digest/bundle and the shared text/JSONL/audit outputs must distinguish actual `missing` fields from `not_applicable` fields. An unavailable options chain, analyst coverage, or insider filing set must not become a phantom completeness failure when the workspace classifies that group as not applicable.

For one ticker:

```text
gate -> refresh due domains -> close writer -> read DB + live top-up -> register bundle
```

For multiple tickers:

```text
one gate pass for all tickers
        -> one sequential refresh for every due domain/ticker
        -> parallel read calls using the same run ID or per-security child runs
```

Required changes to the skill:

1. Add a machine-readable indication of whether each gap is **critical for the requested analysis scope**, not only generally missing.
2. Add a supported first-run/unowned fallback for annual and quarterly financial evidence using the verified provider symbol or a future filing collector.
3. Keep the provider symbol used for every live and fallback request in the bundle.
4. Do not hard-stop security analysis because portfolio classification is stale; mark portfolio context unavailable. Hard-stop only a later portfolio-action stage that requires it.
5. Make same-day artifact names collision-safe within a run by including stage and a short content hash or timestamp.
6. Expose data lineage for each derived metric: inputs, source block, observation dates, and calculation version.
7. Add explicit `current` versus `historical` estimate provenance; do not imply estimate history where only the current snapshot exists.

### 5.4 Stage 3 — Company research collection

Add `company-research-resources` as a controlled evidence collector. It should accept the run ID, verified company identifiers, requested document types, date/period constraints, and analysis scope.

This is a new resource skill built from scratch. As a data-collecting `*-resources` skill, it must use the existing `src/skill_trace.py` writer and the existing `ok` / `missing` / `not_applicable` model. The exact company-research TRACE domains and applicability rules are not defined in the workspace and are therefore left blank for implementation design:

```text
Company-research TRACE domains: _________________________
Company-research applicability rules: ___________________
```

Initial source precedence:

1. Regulatory filings and official government/regulator records.
2. Audited annual and interim reports.
3. Company earnings releases and investor presentations.
4. Earnings-call transcripts or official webcast transcripts.
5. Proxy/circular and insider filings.
6. Competitor filings and primary industry data.
7. Reputable reporting for material developments not yet in filings.
8. Aggregators only as discovery aids or clearly labeled secondary evidence.
9. Social content only when the question is explicitly about sentiment; never as a source for financial facts.

Each collected item must register:

```yaml
evidence_id: ev_...
entity_id: verified issuer identity
security_id: verified listing identity
document_type: annual_report | quarterly_report | earnings_release | transcript | proxy | form4 | news
publisher: ...
source_url: ...
published_at: ...
retrieved_at: ...
as_of_period: ...
filing_accession_or_document_id: ...
artifact_path: evidence/...
content_hash: sha256:...
extraction_version: ...
status: available | partial | missing | invalid
warnings: []
```

The collector must also record:

- entity or ticker ambiguity;
- reporting-currency changes;
- restatements or amended filings;
- inaccessible/paywalled documents;
- extraction failures;
- whether a transcript is official, licensed, or secondary;
- whether only an excerpt, table, or entire document was retained.

#### First source implementation

Do not initially build every adapter. Start with:

1. latest annual filing;
2. latest interim/quarterly filing;
3. latest earnings release;
4. latest accessible earnings transcript;
5. latest investor presentation when material;
6. a bounded set of material news published after the last filing.

The collector should produce both:

- full registered artifacts for audit and programmatic extraction;
- a small extraction index containing relevant sections, page references, and document metadata.

### 5.5 Stage 4 — Market context

Use `market-analyst-resources`, but do not pretend its current registry is complete. Its rates, volatility, selected credit proxies, sectors, factors, policy, FX, and commodities are useful now. Growth, inflation, labor, breadth, and positioning still contain unconfigured areas and must remain explicit gaps until adapters are added.

Consume its existing TRACE output without changing its status meanings: `fetched`/`ok` are obtained; stale, overdue, no-data, missing-input, and stale-leg states are missing; `not_configured` is not applicable and stays outside the completeness denominator. The subject is the run date because this resource is portfolio-wide.

The Investment Analyst should consume a **market report excerpt**, not the raw market bundle. Until the Market Researcher exists, the worksheet may include only deterministic indicator deltas and label them as un-interpreted market context.

Later, the Market Researcher should produce:

- regime summary;
- what changed since the last report;
- relevant rates, currency, commodity, factor, sector, and volatility observations;
- upcoming macro/event risks;
- security-relevant excerpts selected by industry, currency, geography, and business model;
- evidence IDs supporting every regime claim.

Market context should inform sensitivity and scenarios. It should not mechanically determine the security rating.

### 5.6 Stage 5 — Prior thesis and historical context

Load only the currently approved thesis plus a small change history. Do not scan arbitrary dated exports.

The loader should return:

- current approved thesis version and approval date;
- prior fundamental rating and valuation stance;
- active key claims;
- active risks, catalysts, and invalidation conditions;
- prior scenario assumptions and fair-value range;
- monitoring triggers;
- evidence IDs or source links retained in the KB;
- sections that were previously unknown;
- last material change summary.

On an initial analysis, the worksheet must require a longer financial and business history. The current one-time annual context should stop being ephemeral once the new evidence model exists: it should be registered in the run as evidence, used transparently, and retained with the run. It should still not be copied wholesale into the KB.

### 5.7 Stage 6 — Build the deterministic analyst worksheet

Build the investment worksheet as a new deterministic Python module/stage. It is the central integration point and the most important new non-agent component after the schema/validator. It is not a Claude skill.

Inputs:

- `request.yaml` and `analysis-scope.v1`;
- run context manifest;
- investment resource bundle;
- the investment resource bundle's existing TRACE record;
- company research extraction index and registered evidence;
- company-research TRACE record when that new resource skill exists;
- market report or deterministic market digest;
- market-resource TRACE record when market context is requested;
- prior validated thesis summary;
- versioned analysis policy.

Outputs:

```text
workspace/runs/<run_id>/calculations/<ticker>-<timestamp>-worksheet.json
workspace/runs/<run_id>/calculations/<ticker>-<timestamp>-analyst-context.md
```

The JSON is the authoritative machine-readable calculation artifact. The compact Markdown or text context is the only version normally loaded into the analyst.

The worksheet must:

1. Normalize periods, units, currency, signs, and per-share versus aggregate values.
2. Calculate every repeatable metric.
3. Preserve source and period metadata beside values.
4. Identify stale, conflicting, and missing inputs.
5. Preserve the TRACE distinction between `missing` and `not_applicable`; do not recompute the existing resource traces with different arithmetic.
6. Map evidence to report sections.
7. Enforce the requested analysis scope.
8. Carry prior-thesis claims for comparison.
9. Produce scenario templates with explicit assumptions and formulas.
10. Separate equity and ETF tracks.
11. Stay below a tested model-context size ceiling.

Recommended worksheet blocks:

```text
identity
request_and_scope
evidence_health
company_profile
business_evidence
financial_history_and_metrics
expectations_and_results
valuation_methods
price_and_market_context
options_context
ownership_and_capital_allocation
market_context
portfolio_context_informational
prior_thesis
scenario_inputs
section_requirements
known_conflicts
unknowns
```

### 5.8 Stage 7 — Investment Analyst judgment

The Investment Analyst receives:

- the compact analyst context;
- the artifact schema;
- the report section requirements;
- a list of registered evidence IDs it may cite;
- prior thesis excerpts when applicable;
- explicit unknowns, conflicts, and scope restrictions.

It should not receive the full option chain, full price history, full transaction ledger, or entire filing set in its prompt. If it identifies a critical gap, it may emit one structured request:

```yaml
status: needs_evidence
requested_evidence:
  question: What portion of revenue is attributable to the top customer?
  required_document_type: annual_report
  reason: Required to assess customer concentration risk
  blocking_sections: [business_quality, risks]
```

The controller may fulfill the request once, rebuild the worksheet, and rerun the analyst. A second unresolved critical gap produces `partial` or `insufficient_evidence`; it does not start open-ended browsing.

### 5.9 Stage 8 — Conditional challenger

The challenger is a separate invocation, not initially a separate agent definition. It receives the draft thesis and cited evidence summary and must answer:

- What evidence contradicts the thesis?
- Which conclusions rely on weak proxies or stale data?
- Which assumptions are presented too confidently?
- What does the bear case omit?
- Is the valuation range sensitive to one unsupported input?
- Is the supposed variant perception actually consensus?
- Did the analyst ignore dilution, capital intensity, cyclicality, or balance-sheet constraints?
- Did an out-of-scope section change?

Trigger the challenger when any of these are true:

- initial analysis;
- attractive or unattractive fundamental rating with medium/high confidence;
- large rating change from the prior thesis;
- material conflict between primary sources;
- high valuation sensitivity;
- critical event or earnings surprise;
- low evidence completeness paired with a decisive conclusion;
- user explicitly requests a deep or adversarial review.

Skip it for a routine monitor in which no trigger fired and no thesis section changed.

The challenger outputs objections and proposed corrections. The primary analyst then performs one bounded revision. Preserve both versions in the run audit trail.

### 5.10 Stage 9 — Deterministic validation

The validator is the release gate. It must independently validate:

- JSON schema and enum values;
- run ID and ticker identity;
- evidence IDs exist in `sources.jsonl`;
- evidence artifact hashes still match;
- every factual claim has at least one allowed evidence reference;
- claims do not cite evidence marked missing or invalid;
- citation source dates and reporting periods are present when required;
- scenario probabilities sum to 100%;
- expected values and valuation formulas recompute;
- price/fair-value upside and downside recompute;
- fact, assumption, and opinion classifications are present;
- all evaluated sections have a state;
- preserved sections have no replacement content;
- all changed sections were in scope;
- critical gaps obey the scope policy;
- confidence does not exceed evidence-policy caps;
- ETF and equity schemas are not mixed;
- no target weight, trade execution, broker, fill, or order fields appear;
- human review remains required.

Validation should produce errors for integrity failures and warnings for analytical limitations. The artifact may be marked:

```text
valid
valid_with_warnings
invalid
```

Only valid or valid-with-warnings artifacts may proceed to human review.

### 5.11 Stage 10 — Human review and KB promotion

The analyst writes only to `agent_outputs/`. A renderer may write a human-readable copy under the run's `final/`, but this remains a proposal.

After approval, run a new deterministic promotion component built from scratch. It must not invoke or extend `kb-intake`, `kb-update-thesis`, or any other existing non-resource skill. The new component must:

1. verify the artifact is valid and approved;
2. verify it belongs to the referenced run;
3. apply only `changed` sections;
4. preserve `unchanged` and `not_evaluated` sections byte-for-byte where practical;
5. append a thesis-history entry;
6. record source/evidence references in the KB format;
7. update monitoring conditions;
8. retain the run ID and artifact hash for audit;
9. rebuild generated indexes through their owning scripts;
10. reject concurrent or stale-base updates when the KB thesis changed after the run began.

This last point requires an optimistic concurrency check: the run should record the hash or version of the prior thesis it analyzed. Promotion must fail if the current KB page no longer matches that base version.

---

## 6. Detailed Investment Analyst report contract

The human-readable report may retain the broad layout from the Stock Analysis Agent design, but the machine artifact must be more precise. Every section has a purpose, required inputs, and update behavior.

### 6.1 Executive conclusion

Must contain:

- fundamental rating;
- valuation stance;
- thesis direction relative to prior analysis;
- thesis confidence and evidence coverage separately;
- one-paragraph investment case;
- one-paragraph reason the conclusion may be wrong;
- most important catalyst, risk, and unknown;
- analysis horizon and as-of timestamp.

Must not contain:

- target portfolio weight;
- amount of capital to deploy;
- trade timing instruction;
- unsupported numeric confidence percentage.

### 6.2 Investment thesis and variant perception

Must answer:

- What has to be true for the investment to work?
- Which two to four claims carry most of the thesis?
- What appears mispriced or misunderstood?
- Why might the market hold a different view?
- What evidence would distinguish the analyst's view from consensus?

Each key claim should be structured:

```yaml
claim_id: thesis_1
statement: ...
claim_type: fact | inference | assumption | opinion
importance: critical | supporting
evidence_ids: [...]
status_vs_prior: new | strengthened | unchanged | weakened | invalidated
confidence: high | medium | low
```

### 6.3 Company profile and business model

Required content:

- products/services and customer types;
- revenue model and pricing model;
- reportable segments and geographic exposure;
- customer/supplier concentration where disclosed;
- recurring versus transactional revenue;
- cyclicality and major economic sensitivities;
- reporting currency and important operating currencies.

Primary evidence should come from filings and official company material. Aggregator descriptions should be fallback only.

### 6.4 Business quality and competitive position

Evaluate:

- competitive advantages and their evidence;
- switching costs, network effects, scale, brand, IP, regulation, distribution, and data advantages where applicable;
- market structure and bargaining power;
- durability of margins and returns on capital;
- customer retention or usage indicators when disclosed;
- management execution and capital allocation;
- technological or product substitution risk;
- total addressable market only when sourced and decision-relevant.

Avoid unsupported “moat scores.” If a score is retained for comparison, every level must have written anchors in policy and should be secondary to the narrative evidence.

### 6.5 Financial trajectory

Deterministic calculations should cover where available:

- annual and quarterly revenue growth;
- organic growth when explicitly reported;
- gross, operating, and free-cash-flow margins;
- earnings and cash conversion;
- return on invested capital or a clearly defined proxy;
- debt, net cash, liquidity, leverage, and interest coverage;
- working-capital behavior;
- capital expenditures and capital intensity;
- diluted share-count changes and stock-based compensation;
- dividend coverage and buybacks when relevant;
- segment growth and profitability where extractable.

The narrative must distinguish:

- structural trend;
- cyclicality or seasonality;
- acquisition effects;
- accounting/non-cash effects;
- one-time items;
- data limitations.

For banks, insurers, REITs, commodity producers, and other specialized industries, the worksheet must eventually route to industry-specific metrics instead of forcing generic FCF and EV/EBITDA conventions.

### 6.6 Earnings and expectations

Must compare:

```text
actual result
vs expectation immediately before release
vs previous expectation snapshot
vs company guidance
vs prior company guidance
```

Required outputs:

- revenue and EPS surprise;
- guidance change;
- estimate revision direction;
- which operating driver caused the variance;
- whether the market reaction appears related to results, guidance, valuation, or an external event;
- whether the event changes a critical thesis claim.

Important limitation: current yfinance analyst data is not a reliable historical expectations store. Until estimate snapshots are persisted over time, the system must label this section `current_snapshot_only` and avoid claims about revision trends beyond the evidence actually retained.

### 6.7 Valuation

The worksheet should support multiple methods selected by business type:

- earnings multiple;
- free-cash-flow yield or FCF multiple;
- EV/revenue for appropriate early-stage cases;
- EV/EBITDA where meaningful;
- dividend or distributable-cash-flow methods;
- simplified DCF or reverse DCF;
- sum-of-the-parts when segment evidence supports it;
- NAV-oriented methods for relevant asset businesses.

Every method must record:

- base metric and period;
- normalization adjustments;
- growth, margin, reinvestment, dilution, and discount assumptions;
- peer or historical reference basis;
- terminal assumption where applicable;
- currency and share-count basis;
- resulting equity value and per-share value;
- sensitivity range.

The analyst interprets the range and assumptions. Python calculates the range. A single point target should not be presented without a surrounding range and sensitivity explanation.

### 6.8 Price and market behavior

Provide context, not a mechanical trading signal:

- current quote and source time;
- 30/90/365-day returns;
- drawdown from 52-week and all-time or relevant-cycle highs;
- relative return versus benchmark and sector when available;
- volatility and beta when sufficient history exists;
- volume and moving-average context in a later technical phase;
- whether price movement materially changed valuation asymmetry.

Technical indicators must never independently determine the fundamental rating.

### 6.9 Options and market positioning

V1-capable observations:

- put/call open-interest and volume ratios;
- near/far ATM IV;
- skew;
- maximum open-interest strikes;
- event proximity and expected uncertainty, with limitations.

Do not claim IV percentile, unusual flow, robust expected move, or dealer positioning until historical snapshots and the necessary calculations are implemented. This section should carry a data-capability label such as `snapshot_only` or `history_available`.

### 6.10 Insider, institutional, and capital-allocation activity

Separate three questions:

1. What did insiders do?
2. What did the company do with shares and capital?
3. What did institutions disclose?

The first release may report current tables as context, but should not infer intent from a sale without transaction-code and filing context. A later filing adapter should capture Form 4/insider codes, exercises, tax withholding, 10b5-1 references, ownership changes, and transaction proportions.

Institutional trend claims require multiple dated 13F or equivalent snapshots. A single current holders table is only a snapshot.

### 6.11 Market and macro sensitivity

Include only security-relevant items:

- rates and duration sensitivity;
- credit sensitivity;
- currency exposure;
- commodity inputs/outputs;
- economic growth and labor sensitivity;
- sector/factor regime;
- material upcoming macro events.

Every claimed sensitivity should connect to a business or valuation mechanism. Avoid generic macro paragraphs that do not change a thesis or scenario.

### 6.12 Catalysts

Each catalyst should include:

- description;
- estimated time window;
- affected thesis claim;
- expected mechanism;
- evidence supporting its existence;
- whether it is already broadly expected;
- what observable outcome confirms it.

Classify catalysts as company-specific, industry, regulatory, financial, or market-driven.

### 6.13 Risks and disconfirming evidence

Each risk should include:

- probability category;
- impact category;
- time horizon;
- leading indicators;
- affected valuation or thesis assumption;
- mitigation or offset, if any;
- evidence IDs;
- whether the evidence is already occurring or merely possible.

Disconfirming evidence must be its own explicit list so negative facts cannot be buried inside a positive narrative.

### 6.14 Bull, base, and bear scenarios

Every scenario must specify:

- probability;
- horizon;
- revenue/growth assumption;
- margin/cash-flow assumption;
- dilution/share-count assumption;
- balance-sheet assumption where relevant;
- valuation method and terminal multiple/discount input;
- fair value per share;
- expected return from current price;
- catalysts or conditions that lead to the scenario.

Validator rules:

- probabilities sum to 100%;
- fair values and returns recompute;
- bear is not mechanically required to be below current price, but any unusual ordering must be explained;
- scenario assumptions cannot contradict the financial table without an explicit bridge;
- all scenario inputs are facts or labeled assumptions.

### 6.15 Portfolio context — informational only

The analyst may state:

- whether the security is currently held;
- current weight and market value;
- cost basis and unrealized gain/loss;
- assigned role and account type, with freshness status;
- obvious overlap or concentration facts supplied by a portfolio resource.

It may not conclude “buy $500,” “trim to 3%,” or “hold because the position is already large.” Those are Portfolio Manager conclusions. The analyst can flag that a portfolio decision requires current classification/exposure context.

### 6.16 Conclusion and what changes it

Must provide machine-readable conditions:

```yaml
upgrade_conditions:
  - trigger_id: growth_reacceleration
    metric: revenue_growth_yoy
    operator: gte
    threshold: 0.25
    confirmation_periods: 2
    affected_rating: attractive
downgrade_conditions: [...]
invalidation_conditions: [...]
monitoring_items: [...]
```

Not every condition can be purely numeric. Qualitative conditions must still specify an observable event, source type, and affected claim.

---

## 7. Evidence, provenance, and conflict policy

### 7.1 Evidence record extensions

The current `EvidenceRecord` already includes evidence ID, URL, artifact path, timestamps, status, validation status, hash, collection method, notes, and warnings. Extend it carefully rather than replacing it.

Recommended optional fields or a typed metadata block:

- issuer/entity identifier;
- listing/provider symbol;
- document ID/accession;
- publication date;
- reporting period;
- source tier;
- extraction/parser version;
- original language;
- unit/currency metadata;
- supersedes evidence ID;
- conflict group ID;
- licensing/retention note.

Backward compatibility matters: existing evidence registrations must remain valid.

### 7.2 Claim-level citations

An evidence ID alone identifies a file, not the location of support. Claims should cite:

```yaml
evidence_id: ev_123
locator:
  page: 42
  section: Revenue by segment
  table: 7
excerpt_hash: sha256:...
```

The excerpt hash or retained excerpt protects against a claim citing a large document without showing where the support exists.

### 7.3 Source conflict rules

The worksheet builder should never silently choose among conflicting values. It should:

1. normalize units and periods;
2. determine whether values are actually comparable;
3. prefer a later amended or audited primary source when the conflict is resolvable;
4. preserve both values and a conflict record when it is not;
5. prevent the analyst from using the disputed value as a hard fact without acknowledging the conflict.

Example conflict record:

```yaml
conflict_id: cf_001
field: diluted_shares_q2
values:
  - value: ...
    evidence_id: ...
  - value: ...
    evidence_id: ...
resolution: unresolved | source_precedence | period_mismatch | restatement
selected_value: null
notes: ...
```

### 7.4 Freshness is domain- and question-specific

Separate:

- **source freshness:** whether the resource collector should refetch;
- **analytical sufficiency:** whether the evidence is fresh enough for this question;
- **thesis freshness:** whether the stored thesis needs review.

For example, a 20-day-old annual report is fresh, but a 20-day-old quote is not. A stale portfolio classification should not block a security-only thesis, but it must block a final portfolio action based on role or allocation.

---

## 8. Artifact and schema design

### 8.1 `investment-thesis.v1`

Recommended top-level shape:

```yaml
schema: investment-thesis.v1
run_id: ...
security:
  ticker: PLTR
  provider_symbol: PLTR
  asset_track: equity
as_of: ...
analysis_mode: earnings_update
policy_version: ...
worksheet_hash: sha256:...
prior_thesis:
  version: ...
  content_hash: ...
section_states:
  business_quality: unchanged
  financial_trajectory: changed
  valuation: changed
  industry_structure: not_evaluated
conclusion: {...}
key_claims: [...]
sections: {...}
scenarios: {...}
conditions: {...}
known_conflicts: [...]
unknowns: [...]
evidence_ids_used: [...]
challenger:
  required: true
  completed: true
  material_objections: [...]
validation:
  status: pending
human_review_required: true
```

### 8.2 Confidence model

Use three separate concepts:

| Measure | Owner | Meaning |
|---|---|---|
| Evidence completeness | Deterministic resource/worksheet code | Percentage of applicable required fields obtained |
| Evidence quality | Deterministic policy plus source tiers | Strength, freshness, conflicts, and primary-source coverage |
| Thesis confidence | Analyst, capped by validator | Confidence that the interpretation follows from the evidence |

The validator should cap thesis confidence at `low` when critical evidence is missing and at `medium` when a critical claim depends only on a low-tier or conflicting source.

### 8.3 Agent output envelope

The existing generic `AgentOutput` can remain the workspace envelope, but the thesis itself should be a referenced typed artifact rather than forcing the entire report into generic `findings` dictionaries.

Add an envelope field in a backward-compatible way, for example:

```yaml
artifact_type: investment_thesis
artifact_path: agent_outputs/PLTR-...-thesis.json
artifact_hash: sha256:...
```

If modifying the shared model risks other users, create a specialized `InvestmentThesisOutput` model in `src/workspace/analysis_models.py` and teach `workspace.validation` to validate both envelopes.

---

## 9. Equity and ETF tracks

The first implementation should target equities. ETF support should be designed now but implemented as a separate worksheet and thesis schema after the equity vertical slice works.

### 9.1 Equity track

Focuses on:

- business and competitive quality;
- financial statements and cash generation;
- management and capital allocation;
- expectations and earnings;
- valuation and scenarios;
- company-specific catalysts and risks.

### 9.2 ETF track

Focuses on:

- index methodology and rebalance rules;
- fund structure and replication method;
- expense ratio and tracking difference;
- AUM, liquidity, spreads, and closure risk;
- holdings and concentration;
- sector/geographic/currency exposure;
- overlap with current holdings;
- distribution yield and tax characteristics;
- securities lending and counterparty considerations where applicable;
- fit relative to available alternative funds.

An ETF has no company management, operating margin, earnings guidance, or insider activity in the equity sense. Those sections must be `not_applicable`, not missing.

---

## 10. Portfolio Manager boundary and later integration

The Portfolio Manager should consume only validated analyst theses plus current portfolio analytics and owner policy.

Required Portfolio Manager inputs:

- validated security thesis and fair-value/scenario ranges;
- current live position values;
- sector, thematic, factor, geographic, and currency exposure;
- ETF look-through and overlap;
- cash and account constraints;
- allocation policy and risk limits;
- alternative opportunities;
- taxes/account restrictions when available;
- existing proposed actions and review state.

Portfolio Manager outputs:

```text
avoid | initiate | add | hold | trim | exit | watch
target range, not false point precision
priority relative to other actions
policy checks
funding source or displaced holding, if applicable
human approval required
```

This is where a request such as “I have $500; should I buy PLTR?” is completed:

```text
Investment Analyst: PLTR is attractive/neutral/unattractive at current price
Portfolio Manager: given holdings, limits, alternatives, and $500, propose action
Human: approve or reject
```

The analyst alone should never answer the full capital-allocation question.

---

## 11. Exact repository changes

### 11.1 New files — first vertical slice

```text
.claude/agents/investment-analyst.md

docs/agents/investment-analyst/
  architecture.md
  plan.md

src/workspace/analysis_models.py
src/workspace/investment_worksheet.py
src/workspace/financial_metrics.py
src/workspace/valuation.py
src/workspace/scenarios.py
src/workspace/thesis_validation.py
src/workspace/thesis_rendering.py
src/workspace/thesis_promotion.py

tests/fixtures/investment_analyst/
tests/test_investment_worksheet.py
tests/test_investment_thesis_validation.py
tests/test_investment_analyst_contract.py
tests/test_thesis_promotion.py
```

These files are new implementations. No existing non-resource skill is wrapped, renamed, or called from them.

### 11.2 New files — company research phase

```text
.claude/skills/company-research-resources/
  SKILL.md
  references/company-resource-contract.md
  scripts/company_research_resources.py
  scripts/source_registry.py
  scripts/document_identity.py
  scripts/extract_sections.py

tests/test_company_research_resources.py
```

Adapters should live under the skill unless they become shared application infrastructure.

### 11.3 New or updated policy/templates

```text
Knowledge-Base/taxonomy/investment-analysis-policy.yml
Knowledge-Base/templates/investment-thesis-v2-template.md
```

Do not replace the current stock-thesis template until the benchmark is approved.

### 11.4 Existing files to update

| File | Change |
|---|---|
| `src/workspace/models.py` or new `analysis_models.py` | Add scope and typed analyst artifact models without breaking v1 |
| `src/workspace/validation.py` | Validate thesis artifacts, claim citations, hashes, section scope, and forbidden portfolio/trade fields |
| `src/workspace/manifest.py` | Include calculation artifacts, prior thesis version/hash, scope, and target analyst stage |
| `src/workspace/audit.py` | Add worksheet-built, analyst-drafted, challenged, validated, reviewed, and promoted events |
| `tests/test_run_workspace.py` | Cover new artifacts and cross-file validation |
| `investment-analyst-resources` scripts/contract | Add scope-aware gap output, lineage, collision-safe filenames, and first-run/unowned behavior |
| `market-analyst-resources` contract | Add consumption contract for security-relevant excerpts; retain explicit TBD domains |
| New `src/workspace/thesis_promotion.py` | Apply approved section patches, verify the base thesis hash, and append run/artifact references without calling a legacy agent or skill |
| `docs/architecture/run_workspace.md` | Document analyst artifact lifecycle and review/promotion |
| `docs/architecture/decision_support_flow.md` | Document v1/v2 side-by-side operation and final migration decision |
| `docs/reference/cli.md` | Update only when user-facing `src/app.py` commands are added or changed |

### 11.5 Legacy files retained only as benchmark references

Keep:

- `.claude/agents/stock-data-prep.md`;
- `.claude/agents/stock-analyst.md`;
- `fetch-stock-research-data`;
- `evaluate-stock-decision`;
- the decision rubric and existing recommendation artifacts.

They may form a separately executed benchmark/control path and remain available as historical fallback material. They are not runtime dependencies, cannot be invoked by the rebuilt agents, and must not be extended as part of this plan.

---

## 12. Phased implementation plan

The detailed implementation work is separated into the following phase files:

1. [Phase 0 — Contract and responsibility freeze](01-phase-0-contract-and-responsibility-freeze.md)
2. [Phase 1 — Thesis models and validator](02-phase-1-thesis-models-and-validator.md)
3. [Phase 2 — Worksheet builder over existing resources](03-phase-2-worksheet-builder.md)
4. [Phase 3 — Minimal Investment Analyst agent](04-phase-3-minimal-investment-analyst.md)
5. [Phase 4 — Side-by-side benchmark before adding breadth](05-phase-4-side-by-side-benchmark.md)
6. [Phase 5 — Company primary-source research](06-phase-5-company-primary-source-research.md)
7. [Phase 6 — Incremental update and safe KB promotion](07-phase-6-incremental-update-and-kb-promotion.md)
8. [Phase 7 — Challenger pass](08-phase-7-challenger-pass.md)
9. [Phase 8 — Expectations history and event intelligence](09-phase-8-expectations-history-and-event-intelligence.md)
10. [Phase 9 — Market Researcher and market linkage](10-phase-9-market-research-and-linkage.md)
11. [Phase 10 — Portfolio Manager](11-phase-10-portfolio-manager.md)
12. [Phase 11 — Advanced specialist data](12-phase-11-advanced-specialist-data.md)

---

## 13. Testing and evaluation strategy

### 13.1 Unit tests

Test:

- schema parsing and forbidden extra fields;
- date/period normalization;
- currency/unit handling;
- every financial and valuation formula;
- scenario math;
- source precedence and conflict detection;
- critical-gap policy;
- scope mapping and patch enforcement;
- confidence caps;
- claim/citation validation;
- artifact hashing and base-version checks.

### 13.2 Integration tests

Use deterministic fixtures to exercise:

- fresh complete equity run;
- stale-domain refresh decision;
- first-run ticker;
- unowned ticker;
- provider-symbol mismatch;
- earnings-window override;
- missing classification;
- missing options and analysts;
- conflicting filing values;
- incremental earnings update;
- stale-base KB promotion;
- side-by-side v1/v2 report creation.

No test should require live network access.

### 13.3 Golden-file tests

Maintain compact approved fixtures for:

- worksheet structure;
- analyst context rendering;
- valid thesis JSON;
- human-readable thesis rendering;
- incremental diff;
- validation error report.

Golden files should focus on stable contracts, not entire LLM prose.

### 13.4 Agent evaluations

Create a reviewed evaluation set with questions such as:

- Did the analyst distinguish current facts from assumptions?
- Did it identify the main driver rather than list every metric?
- Did it acknowledge contradictory evidence?
- Was the variant perception genuinely different from consensus?
- Did it overstate what options/insider data proves?
- Did it preserve out-of-scope sections?
- Did the conclusion change when a critical assumption changed?
- Did identical evidence produce materially stable conclusions?

### 13.5 Operational metrics

Track per run:

- TRACE completeness percentage, graded-field count, missing fields/domains, not-applicable fields/domains, failed-domain count, and workspace outcome from the existing TRACE records;
- critical gaps as a separate policy decision; TRACE itself does not define criticality;
- evidence source mix and primary-source coverage;
- number of conflicts;
- context size;
- agent tokens and duration;
- challenger invocation rate and useful-objection rate;
- validation failures by category;
- human changes requested;
- promotion success/failure;
- analyst rating changes and causes.

---

## 14. Migration and rollback

### 14.1 Side-by-side period

During migration:

- a separate legacy process may continue producing rubric recommendations for comparison;
- the new analyst produces security theses in run workspaces;
- the two outputs are shown separately;
- the rebuilt workflow never invokes the legacy agents or non-resource skills;
- KB promotion of v2 output is initially manual or targets a separate v2 section/template;
- no existing thesis history is overwritten automatically.

### 14.2 Replacement decision

Possible outcomes after benchmarking:

1. **Full replacement:** v2 analyst becomes the primary security-research path; v1 rubric retained as a policy cross-check.
2. **Dual system:** a separately operated legacy system remains portfolio-wide screening; the rebuilt system is used for deep research and material events.
3. **Targeted adoption:** use selected rebuilt outputs outside the legacy runtime without making legacy agents or non-resource skills dependencies of the new workflow.

The most likely best use is dual system initially:

- the separately operated legacy path for fast standardized screening;
- v2 for initial purchases, high-conviction holdings, earnings reviews, and thesis-breaking events.

### 14.3 Rollback

Because the new path writes only to run workspaces until approved promotion, rollback is straightforward:

- disable the new analyst handoff;
- continue using the separate legacy path if needed;
- preserve runs for diagnosis;
- do not delete or rewrite KB content;
- revert only the promotion adapter if it caused an issue.

---

## 15. Key risks and mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Analyst and Portfolio Manager responsibilities blur | Conflicting or unsafe actions | Enforce schema and forbidden fields |
| Legacy skills or agents leak into the rebuilt runtime | The rebuild inherits old assumptions and coupling | Permit only purpose-built `*-resources` skills and enforce the dependency boundary in tests |
| Raw evidence overwhelms context | Worse reasoning and token use | Compact worksheet plus one bounded evidence request |
| Current snapshot presented as history | False trend claims | Capability labels and persisted snapshots |
| Stale classification blocks all research | Unnecessary failures | Block portfolio action, not security analysis |
| Incremental run rewrites entire thesis | Lost context and partial-data conclusions | Deterministic scope and section patches |
| Same-day files collide | Evidence loss | Run isolation plus timestamp/hash filenames |
| Concurrent KB update overwrites newer thesis | Lost human work | Prior-thesis hash and optimistic concurrency |
| Source disagreement hidden | False certainty | Conflict records and source precedence |
| Arbitrary confidence percentages | False precision | Categorical thesis confidence plus deterministic evidence measures |
| Options/insider data overinterpreted | Misleading signals | Snapshot labels and strict narrative limitations |
| Collectors expand without decision value | Maintenance burden | Add sources only against observed evaluation gaps |
| New system never proves better | Permanent complexity | Time-boxed side-by-side acceptance gates |

---

## 16. Recommended first development milestone

The first milestone should be deliberately narrow:

> Produce a validated, evidence-cited, security-only equity thesis for one well-covered ticker from an existing `investment-analyst-resources` bundle, inside a run workspace, without external company-document collectors and without any KB write.

### Milestone work package

1. Finalize the schema and scope policy.
2. Add `analysis_models.py` and validator tests.
3. Build the resource-bundle-to-worksheet adapter.
4. Generate a compact analyst context.
5. Add the Investment Analyst agent.
6. Produce and validate one full PLTR thesis.
7. Repeat with identical evidence to measure stability.
8. Run the v1 analyst for the same as-of date.
9. Review the two reports using the benchmark rubric.
10. Document actual evidence gaps before starting the company-research collector.

### Information intentionally unavailable in this milestone

- filing-level business-quality evidence;
- historical analyst expectations unless already persisted;
- full insider intent context;
- robust institutional changes;
- IV percentile or unusual options flow;
- final portfolio action and sizing.

The thesis must state those limitations. The milestone succeeds by demonstrating the architecture and discipline, not by pretending the initial data layer is complete.

---

## 17. Priority summary

### Must build first

1. Responsibility boundary and schemas.
2. Scope policy and patch semantics.
3. Thesis validator.
4. Worksheet builder over `investment-analyst-resources`.
5. Minimal Investment Analyst.
6. Side-by-side benchmark.

### Build next if the vertical slice succeeds

1. Primary-source company research.
2. Safe incremental updates and KB promotion.
3. Conditional challenger.
4. Expectations history.
5. Market Researcher and market linkage.

### Build only after the analyst is reliable

1. Portfolio Manager.
2. ETF analyst track.
3. Advanced options history and specialist.
4. Insider and institutional historical parsers.
5. Expanded technical analysis.
6. Automated monitoring and alerts.

### Most important information to retain throughout implementation

- The analyst is a security analyst, not a portfolio allocator.
- The artifact contract and validator are more important than a large agent prompt.
- Full evidence stays in the run; only compact, selected context enters the model.
- Missing and conflicting information are first-class output, not inconveniences to hide.
- Incremental scope must be enforced by code through every stage.
- The only existing skills reused are `investment-analyst-resources` and `market-analyst-resources`; all agents and all non-resource workflow components are rebuilt from scratch. Shared run-workspace and Python infrastructure remains reusable.
- TRACE uses the workspace's existing three-way classification and must not count `not_applicable` as missing.
- Current options, analyst, insider, and institutional data have real historical/context limitations.
- Separately produced legacy output remains valuable as a screening benchmark and fallback reference, but is not part of the rebuilt runtime.
- No new path should write to the Knowledge Base until validation and human review succeed.
- The system should be judged by better decisions and clearer uncertainty, not by how many tools, sections, or agents it contains.
