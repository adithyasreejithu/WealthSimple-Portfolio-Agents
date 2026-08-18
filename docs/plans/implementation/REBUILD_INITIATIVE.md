# Investment Analyst Rebuild Initiative

**Status:** Phases 0–4 complete; Phase 5 (side-by-side benchmark) ready to begin
**Master plan:** [`../investment-analyst-rebuild-roadmap.md`](../investment-analyst-rebuild-roadmap.md)  
**Phases:** Phases 0–5 are a strict critical path (no skipping, no reordering); 6–12 fan out after Phase 5

---

## Vision

The rebuild replaces the legacy `decision-rubric.yml` path (static scores, 1–10 anchoring, no evidence audit trail) with a new end-to-end `investment-thesis.v1` workflow that:

1. **Locks down the contract before any LLM writes code** (Phase 0: schemas, policy, vocabulary)
2. **Validates deterministically before agents run** (Phase 1: pydantic models, cross-file checks)
3. **Builds in mechanical, testable stages** (Phase 2–3: data layer, worksheet builder)
4. **Embeds LLM judgment in a second-opinion arch** (Phase 4–8: analyst, challenger, debate)
5. **Traces and audits every source and decision** (Phases 1–11: evidence registry, changelog, decision-log)

The system is **research-only** (no trade execution, no direct portfolio modification) and **human-gated** (every thesis requires human approval before Knowledge-Base publication).

---

## Phases 0–5: Critical Path (Required, Sequential)

```
Phase 0 (✅ done)
  Contract freeze: schemas, policy, vocabulary
     ↓
Phase 1 (✅ done)
  Models & validator: pydantic + cross-file checks
     ↓
Phase 2 (✅ done)
  Cheap data gaps: technicals + ratios (no new data source)
     ↓
Phase 3 (✅ done)
  Worksheet builder: compact analyst context
     ↓
Phase 4 (✅ done)
  Investment Analyst: LLM judgment against investment-thesis.v1
     ↓
Phase 5 (→ ready to start)
  Benchmark: side-by-side comparison with legacy path
```

**Each phase stops and waits for approval before proceeding to the next.** No auto-continuation. Phases are designed to ship independently: Phase 1's models/validator is production-ready even though Phase 2 hasn't started yet.

---

## Phase Recap

### Phase 0: Contract and Responsibility Freeze ✅

**Deliverables:** 8 files (2 schemas, 1 policy, 5 example YAML)  
**Key decisions:** TRACE thresholds (50 blocking, 80 warning), Portfolio Manager vocabulary (reuse decision-framework.yml)  
**Output:** Locked schemas, policy file, no code yet

**See:** [`implementation/phase-0/deliverables-checklist.md`](implementation/phase-0/deliverables-checklist.md)

---

### Phase 1: Thesis Models & Validator ✅

**Deliverables:** 3 Python files (643 + 185 + 655 lines), package updates, 1 bug fix  
**Gate condition:** Fabricated evidence ID + `target_weight` field both fail validation ✅  
**Output:** Production-ready pydantic models + cross-file validator, 40 tests, 937/937 full suite passing

**What it enforces:**
- No Portfolio Manager fields anywhere (forbidden-field detection scans entire tree)
- Evidence citations resolve to registered, intact records
- Section coverage matches the run's analysis-scope.v1
- Scenario probabilities sum to 1.0 ± policy tolerance
- Confidence is capped by TRACE completeness + gate status

**See:** [`implementation/phase-1/deliverables-checklist.md`](implementation/phase-1/deliverables-checklist.md)

---

### Phase 2: Cheap Data-Gap Closure ✅

**Deliverables:** `src/security_technicals.py` (moving averages, beta, drawdown,
volatility, relative strength vs. XEQT.TO), a normalized ratio module, and a
fix for the 30d/90d/365d return mislabeling bug.

**See:** [`../phase-2/deliverables-checklist.md`](../phase-2/deliverables-checklist.md), [`../phase-2/HANDOFF.md`](../phase-2/HANDOFF.md)

---

### Phase 3: Worksheet Builder ✅

**Deliverables:** `src/workspace/investment_worksheet.py` (scope mapper +
`build_worksheet`/`build_worksheet_for_run`), `financial_metrics.py`,
`valuation.py`, `scenarios.py`. First integration with
`investment-analyst-resources`; deterministic worksheet + compact
analyst-context, TRACE preserved verbatim.

**See:** [`../phase-3/deliverables-checklist.md`](../phase-3/deliverables-checklist.md), [`../phase-3/HANDOFF.md`](../phase-3/HANDOFF.md)

---

### Phase 4: Investment Analyst Agent ✅

**Deliverables:** `.claude/agents/investment-analyst.md` — the sole LLM
judgment stage — plus three new `run` CLI stages
(`build-worksheet`/`check-thesis`/`save-thesis`) in `src/workspace/cli.py`
wrapping new `thesis_validation.py` functions. Produces a validated
`investment-thesis.v1` artifact against the unmodified Phase 1 validator; a
scope correction was applied at the start of this phase (the legacy
`decision-rubric.yml`/buy-sell-hold framing does not apply here — see
`../phase-4/HANDOFF.md`).

**See:** [`../phase-4/deliverables-checklist.md`](../phase-4/deliverables-checklist.md), [`../phase-4/HANDOFF.md`](../phase-4/HANDOFF.md)

---

### Phase 5: Benchmark (Ready to Start)

Side-by-side comparison of the new `investment-analyst` path against the
legacy `stock-analyst`/`decision-rubric.yml` path on the same tickers;
verdict on full replacement, dual system, or targeted adoption (roadmap
row 5).

---

## Phases 6–12: Fan-Out (Optional, Reorderable)

After Phase 5's benchmark verdict, Phases 6–12 can be built in any order (or dropped) based on what the benchmark showed actually mattered:

- **Phase 6:** Company primary-source research (filings, IR, segments, guidance)
- **Phase 7:** Incremental update & KB promotion (re-run without rewriting untouched sections)
- **Phase 8:** Challenger pass (bounded second opinion, not routine doubling of cost)
- **Phase 9:** Expectations history & event intelligence (analyst estimate revision tracking)
- **Phase 10:** Market Researcher (macro/market linkage to analyst output)
- **Phase 11:** Portfolio Manager (turn security theses into portfolio action)
- **Phase 12:** Advanced specialist data (IV/OI history, 13F changes, insider transaction codes)

---

## File Organization

```
docs/
├── architecture/
│   ├── investment_thesis_schema.md          (Phase 0: contract for artifact shape)
│   ├── analysis_scope_schema.md             (Phase 0: contract for scope)
│   ├── investment_analyst_examples/         (Phase 0: 5 example YAML fixtures)
│   └── ...existing reference docs...
├── plans/
│   ├── investment-analyst-rebuild-roadmap.md    (Master plan, unchanged)
│   ├── stock-analysis-agent-design-review.md    (Gap analysis reference)
│   ├── combined-investment-analyst-plan/        (12-phase spec)
│   └── implementation/
│       ├── REBUILD_INITIATIVE.md                (← This file)
│       ├── phase-0/                             (Phase 0 tracking)
│       │   ├── README.md
│       │   ├── deliverables-checklist.md
│       │   └── HANDOFF.md
│       ├── phase-1/                             (Phase 1 tracking)
│       │   ├── README.md
│       │   ├── deliverables-checklist.md
│       │   └── HANDOFF.md
│       ├── phase-2/                             (Phase 2 tracking, when it starts)
│       └── ...phases 3–12 follow same pattern...

config/
└── policies/
    └── investment-analysis-policy.yml        (Phase 0: operational configuration)

src/
├── workspace/
│   ├── analysis_models.py                    (Phase 1: pydantic models)
│   ├── thesis_validation.py                  (Phase 1: cross-file validator)
│   ├── financial_metrics.py                  (Phase 2: technicals + ratios)
│   ├── valuation.py                          (Phase 3: valuation methods)
│   ├── scenarios.py                          (Phase 3: scenario building)
│   ├── investment_worksheet.py               (Phase 3: worksheet builder)
│   └── ...rest of run workspace...
├── analytics.py                              (Portfolio exposure functions for PM)
└── ...rest of pipeline...

tests/
├── test_investment_thesis_validation.py      (Phase 1: 40 tests)
├── test_financial_metrics.py                 (Phase 2: when it lands)
└── ...rest of test suite...
```

---

## Key Architectural Decisions

### 1. Schemas Locked Before Code

Phase 0 locked the exact shape of `investment-thesis.v1` and `analysis-scope.v1` before Phase 1 wrote a line of Python. This forces the contract to be real (not "we'll refine it later") and lets Phase 2/3 know exactly what they're building into.

### 2. Validator Exists Before the Agent

Phase 1 validates deterministically before Phase 4's LLM agent exists. No agent can produce a thesis that violates the schema or policy rules, so the agent's instructions can assume structural validity — it only needs to worry about correctness of analysis.

### 3. Mechanical Data Layers Before LLM

Phases 2–3 build deterministic, testable data layers (technicals, ratios, worksheet) before Phase 4's judgment stage. This means the analyst agent receives compact, verified context (not raw bundles) and can focus on reasoning over already-vetted data.

### 4. Operational Config in `config/`, Not `Knowledge-Base/`

The Knowledge-Base is reserved for research content (theses, analyses, sources). Operational configuration (policy, rules, thresholds) lives in `config/policies/`. This keeps concerns separated and makes policy edits testable independently.

### 5. Everything is Read-Only + Human-Gated

The analyst produces a research-only thesis (no portfolio action field). The Portfolio Manager (Phase 11) turns thesis + policy into action. Both require human approval before anything touches the Knowledge-Base or database. No agent can execute trades.

---

## Decision Records (Phase 0 Open Decisions, Now Resolved)

### Decision #1: TRACE Thresholds

| Parameter | Value | Rationale |
|---|---|---|
| Blocking threshold | 50.0% | Catches genuinely broken pulls; accommodates ETF structural sparseness |
| Warning threshold | 80.0% | Flags imperfect but usable runs (PLTR 76.5% was usable); caps confidence at medium |
| Scope | Required domains only | A run requiring [financials, earnings, valuation] is judged on those, not on optional options/insider |
| Override authority | Human operator only | No agent may override; logged to audit trail |

**Located in:** `config/policies/investment-analysis-policy.yml` §6

### Decision #2: Portfolio Manager Vocabulary

| Choice | Rationale |
|---|---|
| Reuse decision-framework.yml unchanged | The candidate vocabulary (initiate|add|hold|trim|exit|avoid) is a synonym set over the same 7 concepts with no semantic gain. Both PM and analyst write to the same stock-page Decision field; two textually different enums for the same downstream field is a bug-generator, not a boundary. |
| No new taxonomy file | decision-framework.yml already covers PM actions; no gap. |
| Analyst never emits portfolio actions | Analyst emits fundamental attractiveness (fundamental_rating, valuation_stance, thesis_direction, thesis_confidence). PM emits actions. Clear boundary. |

**Located in:** `config/policies/investment-analysis-policy.yml` §2

---

## For Next Sessions

### Before Phase 5 Starts

1. **Read `../phase-4/HANDOFF.md` in full**, especially the scope correction
   record (item 1) — the legacy `decision-rubric.yml`/buy-sell-hold framing
   does not apply to the `investment-analyst` agent and must not be
   reintroduced.
2. **Run spot checks from `../phase-4/HANDOFF.md`** to confirm the system
   still works: `uv run python -m unittest tests.test_investment_analyst_cli -v`
   and the full suite.
3. **Outstanding, carried forward from earlier phases, still unresolved:**
   whether `tests/tmpqinoi8gr/portfolio.duckdb` (an 11MB binary accidentally
   committed in `24e6272`, Phase 0) should be removed via a new commit or a
   history rewrite — needs explicit user approval either way, not something
   any phase should do unilaterally.

### During Phase 5 & Beyond

- Keep Phase N implementation tracking (README, deliverables-checklist, HANDOFF) in `docs/plans/implementation/phase-N/`
- Every phase is a safe stopping point; nothing later assumes phase N+1 exists
- All policy constants live in one file (`config/policies/investment-analysis-policy.yml`); if a phase needs new policy, add it there, not scattered across code
- Follow the error-collection pattern (no early exit on first error); callers need a complete list to fix everything at once

---

## Links

- **Master roadmap:** [`../investment-analyst-rebuild-roadmap.md`](../investment-analyst-rebuild-roadmap.md)
- **Phase 0 deliverables:** [`implementation/phase-0/deliverables-checklist.md`](implementation/phase-0/deliverables-checklist.md)
- **Phase 1 deliverables:** [`implementation/phase-1/deliverables-checklist.md`](implementation/phase-1/deliverables-checklist.md)
- **Phase 1 handoff (bugs, decisions, what Phase 2 needs):** [`implementation/phase-1/HANDOFF.md`](implementation/phase-1/HANDOFF.md)
- **Policy file (authoritative source for all tuning):** [`../../config/policies/investment-analysis-policy.yml`](../../config/policies/investment-analysis-policy.yml)
- **Artifact schemas (contracts Phase 1 enforces):** [`../architecture/investment_thesis_schema.md`](../architecture/investment_thesis_schema.md), [`../architecture/analysis_scope_schema.md`](../architecture/analysis_scope_schema.md)

---

**Last updated:** 2026-08-11
**Next action:** Approve Phase 4 deliverables, then Phase 5 (side-by-side benchmark) can begin.
