# Stock Analysis Agent design — gap review against the actual repository

**Status:** review, not a plan. Companion document: [investment-analyst-rebuild-roadmap.md](investment-analyst-rebuild-roadmap.md) turns this review's findings into an ordered build sequence.
**Input reviewed:** the user's `stock_analysis_agent_design.md` (a from-first-principles Stock Analysis Agent design, not checked into this repo).
**Also considered:** [`docs/plans/combined-investment-analyst-plan/00-overview.md`](combined-investment-analyst-plan/00-overview.md) — an existing 1,527-line synthesis that names the same design doc as a primary input and reconciles it with the repo. This review is independent of that synthesis: it states where the source design is factually wrong about, or unaware of, the repo, which the synthesis does not do.

## Status marker convention

| Marker | Meaning |
|---|---|
| `[BUILT]` | Exists and usable today. Cited to a real file, function, table, or JSON path. |
| `[PARTIAL]` | Exists but materially incomplete. The limitation is stated explicitly, never implied. |
| `[NOT BUILT]` | Required by the design; no implementation and no data source exists. Kept in the architecture as a named future component rather than deleted from scope. |

Every `[BUILT]`/`[PARTIAL]` entry carries a `Source:` line. Every `[NOT BUILT]` entry carries a `Blocked on:` line naming what has to exist first.

## Decisions already made (not re-litigated here)

- The Investment Analyst emits `fundamental_rating` (attractive/neutral/unattractive/insufficient_evidence), `valuation_stance` (discounted/reasonable/demanding/indeterminate), and `thesis_direction` (initial/strengthening/unchanged/weakening/broken) — **not** Buy/Hold/Trim/Sell. Portfolio actions (`initiate|add|hold|trim|exit|avoid`, target range, capital allocation) belong to a later, separate Portfolio Manager.
- Capabilities the design assumes but the repo doesn't have stay **in** the architecture, explicitly marked `[NOT BUILT]`, rather than being quietly dropped.
- Every existing agent and every non-`*-resources` skill is excluded from the rebuilt runtime; only `investment-analyst-resources` and `market-analyst-resources` are retained.

---

## 1. Verdict

The design doc's **research framework** — the 22-section report structure, the emphasis on expectations vs. results, business-quality reasoning over financial statements, and explicit bull/base/bear scenarios with invalidation triggers — is the strongest part of the document and should survive into the rebuild largely intact.

Its **execution model** is mostly superseded. The repo already has working solutions for data freshness, evidence provenance, run isolation, and citation validation — problems the design doc anticipates in the abstract ("provenance and freshness," §14) but solves differently, and less specifically, than what already runs in this codebase. Adopting the design's execution ideas as written would mean building a second, incompatible version of infrastructure that already exists.

---

## 2. Missing project context

This is what the design doc doesn't know exists. It is the largest section because most of the doc's proposed *machinery* — as opposed to its analytical framework — already has a repo equivalent.

**`investment-analyst-resources` already is the design's "Context Builder"** for security data (design §13). Its bundle schema carries:

```text
db:    position, ledger, ledger_summary, prices, financials, earnings,
       dividends, stock_details, etf_details, classification, portfolio_context
live:  overview, valuation, analyst, options, news, insider, institutional, funds
derived: 19 named metrics (fcf_yield, debt_to_equity, current_ratio, revenue_growth_yoy,
         return_30d/90d/365d, put_call_oi_ratio, put_call_volume_ratio, atm_iv_near/far,
         iv_skew, max_oi_call/put_strike, upgrades_90d, downgrades_90d,
         net_revisions_365d, net_insider_shares)
quote, trace, gaps, errors
```

Source: verified against a real bundle, `exports/investment-analyst-resources/ZETA-2026-08-07-resources.json`, and the script that builds it, `.claude/skills/investment-analyst-resources/scripts/{investment_analyst_resources.py,db_resources.py,derived_metrics.py,freshness_gate.py}`.

**The run workspace supersedes the design's `request_workspace/<TICKER>/` tree** (design §12). `workspace/runs/<run_id>/` already provides `request.yaml`, `context_manifest.yaml`, `run_metadata.json`, `audit_log.jsonl`, and `evidence/sources.jsonl` — the last carrying sha256 hashes, an `evidence_type` field, and `available`/`partial` status per entry. Source: `src/workspace/{run,manifest,models,evidence,audit,validation}.py`. Building the design's proposed tree alongside this would split the audit trail across two incompatible evidence stores.

**Freshness gating exists; the design has no concept of it at all.** Per-domain cadences — prices to the last trading day, earnings 7 days, dividends 30 days, financials 30 days, classification 7 days (report-only) — plus a **2-day earnings-proximity override** that force-stales prices/earnings/financials and sets `earnings_window: true`. Source: `.claude/skills/investment-analyst-resources/scripts/freshness_gate.py`. This is the repo's existing answer to a real prior incident (stale data being analyzed on earnings day) that the design doc never anticipates.

**TRACE observability** — a three-way `ok`/`missing`/`not_applicable` field classification, with `not_applicable` excluded from the completeness denominator, written to `logs/SkillTrace.{txt,jsonl}`. PLTR's last resource bundle reports `completeness_pct: 76.5`. Source: `src/skill_trace.py`. The design's closest analogue is "data provenance and freshness" (§14), which describes provenance metadata but no completeness measurement.

**Two DuckDB databases and a real concurrency constraint** — `Data/PRD_WealthSimple.duckdb` (schema v13) for portfolio/security data, and a deliberately separate `Data/market.duckdb` for macro indicators. DuckDB allows one read-write process *or* many read-only ones, which is exactly why the resource skills are split into `gate`/`refresh`/`read` modes rather than a single mode. The design assumes free-form, unconstrained database access throughout (§4, §11) with no discussion of what happens when two components want to write at once.

**The Knowledge Base is real, structured, and already opinionated** about exactly the things the design proposes loosely:

- Fixed page location and section order: `Knowledge-Base/stocks/<TICKER>.md`, with Company Overview → Original Thesis (write-once, never edited after creation) → Updated Thesis → Financial Analysis → Valuation Analysis → Technical Analysis (placeholder) → Options Activity → Market Sentiment → Insider Activity → Earnings and Catalysts → Dividend Analysis → Portfolio Fit → Bull Case → Bear Case → Analyst View → Key Risks → Open Questions → Decision History (append-only table) → Monitoring Checklist → Sources.
- `section_updated` frontmatter drives a weekly/monthly staleness gate (`.claude/skills/kb-staleness-gate/`), so re-analysis only touches sections that are actually due.
- Three sections (Financial Analysis's DB-backed portion, Earnings and Catalysts, Dividend Analysis) are regenerated straight from the database inside `<!-- kb-db-section:begin/end -->` markers and are never staleness-gated.

Source: `Knowledge-Base/templates/{front-matter-spec.md,stock-thesis-template.md}`, `src/kb_pages.py`. The design's "save theses into the knowledge base" (§13, development-order item 14) undersells how much of this is already a solved, guarded problem — the open question is promotion safety on *incremental* updates, not whether a KB writer exists.

**Portfolio policy is already machine-readable**, closely matching the design's §11 exposure example: `Knowledge-Base/ref/policy_v1_1.yaml` defines groups (Core/Income/Quality/Growth/Alternatives/Cash), per-group allocation targets with min/max bands, and `constraints.single_name_max_percent: 10` — mirrored in Python as `SINGLE_NAME_MAX_WEIGHT` in `src/config.py`.

**Portfolio exposure functions exist but are not wired into any analyst-facing output.** `get_sector_allocation` (`src/analytics.py:1168`), `get_look_through_sector_exposure` (`:1233`), `get_currency_exposure` (`:1501`), `get_benchmark_returns` (`:1906`). The design's `get_portfolio_exposure()` (§11) is not a gap — it's mostly built and simply unplumbed to any consumer.

**Citation resolution is the repo's best existing idea and has no counterpart in the design doc.** `.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py` rejects any evidence field that doesn't resolve to a non-null, non-empty value in the exact source file it cites. This is a stronger provenance guarantee than the design's proposed per-scalar envelope (§14) and should be carried into the new validator unchanged rather than reinvented.

---

## 3. Section-by-section mapping

| Design §, title | Repo status | Note |
|---|---|---|
| §2, output structure (15-section report + "what would change the recommendation") | `[NOT BUILT]`, worth building | No existing artifact produces a readable memo; `decision-rubric.yml` v1.3 produces a score, not a narrative. The design's final "what changes the recommendation" section has no repo analogue and is a genuine improvement (see §5 below). Blocked on: Phase 4 agent + a typed trigger schema (Phase 9). |
| §3, position in overall architecture | `[PARTIAL]` — real architecture differs | The design's Orchestrator → Stock Analysis Agent → {Research, Analytics, Portfolio} Tools tree is close in spirit to the actual `run workspace → *-resources skills → worksheet builder → analyst` chain, but the design's tools are generic where the repo's are concrete and already built. See Document B Diagram 1. |
| §4, fundamental/financial data + ratios | `[PARTIAL]` | Quarterly revenue/margins/FCF/EPS `[BUILT]` (`db.financials`); most of the named ratio list (P/E, PEG, EV/EBITDA, EV/Revenue, Debt/EBITDA, ROIC, ROE) `[NOT BUILT]` as a normalized module — see Document A §6 row-by-row. |
| §4, "Python calculates, LLM interprets" | `[BUILT]` as a principle, already enforced | `derived_metrics.py` and `scoring_worksheet.py` already do exactly this; the rebuild's worksheet builder (Phase 3) continues it. No change needed to this principle. |
| §5, business quality (moat, management, TAM, competitive threats) | `[NOT BUILT]` | No filing/IR/transcript source exists. yfinance's `overview`/`news` groups give only shallow description. Blocked on: `company-research-resources` (Phase 6). |
| §6, expectations vs. results | `[PARTIAL]`, better than it looks | Earnings actual-vs-estimate with surprise % `[BUILT]` — `db.earnings`, 25 rows on PLTR. Estimate *revision history* (prior estimates, upgrade/downgrade timeline beyond a current snapshot), and company guidance, are `[NOT BUILT]`. This is the single biggest gap in the design's most distinctive analytical idea. Blocked on: persisting dated consensus snapshots (Phase 9). |
| §7, price and technical context | `[PARTIAL]` | 52-week high/low and period return `[BUILT]`. Moving averages, beta, drawdown, volatility, relative strength vs. sector/index `[NOT BUILT]` — but full OHLCV already exists in `historical_records`, making this the cheapest gap in the entire design to close (Phase 2). |
| §8, options and market positioning | `[BUILT]` as snapshot, `[NOT BUILT]` as history | Put/call OI & volume ratios, ATM IV, skew, max-OI strikes all `[BUILT]` (`derived.*`). IV percentile, historical IV/OI, unusual-flow detection `[NOT BUILT]` — no persistence of option snapshots over time exists. Blocked on: Phase 12. |
| §8, separation of options-as-information vs. options-strategy-execution | `[BUILT]` as a principle | Already the repo's stance; no options-execution agent exists or is planned before a validated analyst. |
| §9, insider/institutional activity with context (10b5-1, tax, exercise) | `[PARTIAL]`, mostly `[NOT BUILT]` | Current insider table and `net_insider_shares` `[BUILT]`. Every contextual question the design asks in §9 ("was it scheduled," "was it tax-related") is unanswerable today — no transaction-code or filing-level data exists. Institutional holdings are a current snapshot only, no 13F change history. Blocked on: Phase 12. |
| §10, market/macro context via a separate agent's output file | `[PARTIAL]` | Real equivalent is `market-analyst-resources` → `Data/market.duckdb` + `exports/market-analyst-resources/<date>-market.json`, not a `market_context.yaml`. Coverage is ~5% populated; `growth`, `inflation`, `labour`, `breadth`, `positioning` domains are explicit `<TBD>` stubs with no source chosen. The Market Researcher agent that would characterize this data is itself `[NOT BUILT]` (Phase 10). |
| §11, portfolio context, exposure, "attractiveness ≠ portfolio action" | `[PARTIAL]`, principle already adopted | The position/weight/cost/role fields are `[BUILT]` (`db.portfolio_context`). Group/sector/currency exposure functions exist (§2 above) but are unwired. The attractiveness-vs-action separation is already the rebuild's Phase 0 decision, independently arrived at. |
| §12, structured context package (`request_workspace/<TICKER>/*.json`) | `[BUILT]` differently | Superseded by the run workspace, see §2 above. Don't build a second tree. |
| §13, Context Builder | `[BUILT]` differently, `[NOT BUILT]` as a single component | No single "Context Builder" component exists; the equivalent work is split across `investment-analyst-resources` (security data) + `market-analyst-resources` (macro data) + the planned worksheet builder (Phase 3), which is closer to the design's intent than a monolithic builder would be. |
| §14, data provenance/freshness envelope | `[BUILT]` differently, stronger | See §2 above — domain-level freshness gates + evidence-ID citation + artifact hashing already exceed the design's proposed per-scalar `{value, source, observed_at}` envelope. |
| §15, agent definition (small YAML, intelligence from architecture not prompt size) | `[BUILT]` as a principle | Matches this repo's existing agent style (`.claude/agents/stock-analyst.md` is deliberately compact); the rebuild's Phase 4 agent should follow the same discipline. |
| §16, ten narrow analysis skills under one agent | Rejected by prior decision | Conflicts with the "only `*-resources` skills" rebuild constraint (`docs/plans/combined-investment-analyst-plan/00-overview.md` §3.2). Section requirements live in the worksheet (Phase 3); calculations live in Python modules (Phase 2), not skills. |
| §17, tools underneath skills (`tools/{market,fundamentals,analysts,portfolio,kb}/`) | Don't build — duplicates existing modules | Would duplicate `src/market_data.py`, `src/analytics.py`, `src/position_engine.py`, `src/kb_pages.py`. |
| §18, end-to-end example (PLTR, $500 capital) | `[NOT BUILT]` end-to-end, pieces exist | This is effectively the rebuild's Phase 4–5 acceptance scenario, minus the Portfolio Manager's capital-sizing step which stays deferred. |
| §19–20, recommended V1 architecture and development order | Superseded by Document B | The design's 22-step development order and this review's phase table (Document B) cover the same ground; Document B is the one to execute from — it's tied to real files and a real data-availability ledger, this list is not. |
| §21, architectural boundaries (Agent/Skill/Tool/Context Builder/KB/Portfolio DB) | `[BUILT]`, mostly matches | The boundary the design draws is sound and matches the rebuild's component-ownership table in the combined plan §4; the repo's actual component names differ but the separation of concerns is the same idea. |
| §22, Stock Analysis Agent stays narrow; other components answer other questions | `[BUILT]` as the rebuild's own decision | This is precisely Phase 0/11's Analyst-vs-Portfolio-Manager split, arrived at independently in the combined plan before this review started. |

---

## 4. Where the design must change

| # | Design says | Change | Why |
|---|---|---|---|
| 1 | Strong Buy / Buy / Hold / Trim / Sell / Avoid from the analyst | Analyst emits `fundamental_rating` / `valuation_stance` / `thesis_direction`; a later Portfolio Manager emits portfolio actions | Confirmed decision. Also: `Strong Buy` isn't in the repo's `Knowledge-Base/taxonomy/decision-framework.yml` enum at all, and `decision-rubric.yml` v1.3 already produces the Buy/Hold/Trim/Sell vocabulary deterministically — two components producing the same verdict is a real hazard, not a style preference |
| 2 | `Confidence: 78%` / `74%` | Categorical `high\|medium\|low`, plus a separate deterministic evidence-coverage percentage | Percentages presented as confidence are unvalidatable false precision; the repo already derives confidence from unknown-dimension/unknown-gate counts and evidence-group coverage |
| 3 | 1–10 numeric scores (`Business Quality: 9/10`, `Growth: 9/10`) | The analyst emits no numeric scores | Collides with the rubric's anchored 1–5 dimension scale; an unanchored 10-point scale has no fixed meaning and will drift between runs and between tickers |
| 4 | `request_workspace/<TICKER>/*.json` | Use `workspace/runs/<run_id>/evidence/` + `evidence/sources.jsonl` | A second evidence tree splits the audit trail across two incompatible stores |
| 5 | Per-scalar provenance envelope `{value, currency, source, observed_at}` | Domain-level `as_of`/`observed_at` timestamps + evidence-ID/JSON-path citation + artifact sha256 | Per-scalar envelopes multiply bundle size for provenance the citation-resolution model already provides at lower cost |
| 6 | `tools/{market,fundamentals,analysts,portfolio,kb}/` | Do not create this tree | Duplicates `src/market_data.py`, `src/analytics.py`, `src/position_engine.py`, `src/kb_pages.py` |
| 7 | Ten analysis skills (financial-analysis, valuation-analysis, earnings-analysis, …) under one agent | One agent; section requirements live in the worksheet; calculations live in Python modules | Violates the rebuild's own "only purpose-built `*-resources` skills" constraint |
| 8 | `market_context.yaml` produced by a Market Research Agent | Real equivalent is `market-analyst-resources` → `Data/market.duckdb` + `exports/market-analyst-resources/<date>-market.json`, `[PARTIAL]` | ~5% populated; five domains are `<TBD>`; the Market Researcher agent that would characterize the regime is itself `[NOT BUILT]` |
| 9 | *(absent from the design entirely)* freshness gating, incremental-update scope, DB read/write concurrency | Adopt the repo's existing solutions for all three unchanged | The design has no model for any of them; the repo has working, tested ones |

---

## 5. Where the design is right and the repo has nothing

All genuinely `[NOT BUILT]`, and all worth keeping in the rebuilt architecture:

- Explicit bull/base/bear scenarios with stated probability and driving assumptions.
- Machine-readable "what would change the recommendation" triggers — the design's single best idea, and the current rubric produces nothing like it.
- Variant perception: what is the market mispricing, and why, stated as a distinct claim from "the stock went up/down."
- Business-quality and moat reasoning grounded in evidence rather than a numeric score standing in for judgment.
- A readable investment memorandum as the primary artifact, with the machine-readable schema underneath it rather than the schema being the whole product.

---

## 6. Data availability ledger

| Input | Status | Note |
|---|---|---|
| Quarterly revenue/net income/EPS/margins/FCF/D-E/current ratio | `[BUILT]` | `db.financials`, 6 quarters on PLTR |
| Earnings actual vs. estimate + surprise % | `[BUILT]` | `db.earnings`, 25 rows on PLTR |
| Analyst estimate revision history, prior estimates | `[NOT BUILT]` | Blocked on: persisting dated consensus snapshots (Phase 9). The single biggest gap in the design's §6 |
| Company guidance / prior guidance | `[NOT BUILT]` | Blocked on: filings/IR collector (Phase 6) |
| Revenue segments, geography, customer concentration | `[NOT BUILT]` | Blocked on: `company-research-resources` (Phase 6) |
| P/E, forward P/E, P/S, PEG | `[PARTIAL]` | Provider-supplied in `live.valuation`, unnormalized, may mix reporting periods |
| EV/EBITDA, EV/Revenue, Debt/EBITDA, ROIC, ROE, share dilution, capex, working capital | `[NOT BUILT]` | Blocked on: a financial-metrics module (Phase 2). Inputs largely already present |
| FCF yield, D/E, current ratio, revenue growth YoY | `[BUILT]` | `derived.*` |
| 52-week high/low, period return | `[BUILT]` | `db.prices` aggregate |
| 30/90/365-day returns | `[PARTIAL]` | Concrete defect: a real bundle (ZETA) shows `return_30d == return_90d == return_365d == 0.2299`. DB prices begin at `first_owned_date`, so for recently opened positions `return_365d` is not actually a 365-day return, and today it is not labelled as anything less. Fixed in Phase 2 |
| Moving averages, beta, drawdown, volatility, relative strength vs. sector/index | `[NOT BUILT]` | Blocked on: a per-security technicals module (Phase 2). Full OHLCV already exists in `historical_records` — the cheapest gap in the entire ledger to close |
| Options chain, put/call OI & volume, ATM IV, skew, max-OI strikes | `[BUILT]` snapshot | `derived.*` |
| IV percentile, historical IV/OI, unusual flow, expected move | `[NOT BUILT]` | Blocked on: persisting option snapshots over time (Phase 12) |
| Insider table + net insider shares | `[PARTIAL]` | `derived.net_insider_shares`; no transaction codes, 10b5-1 flags, ownership %, or exercise/tax context — every §9 question in the design is unanswerable today |
| Institutional holders | `[PARTIAL]` | Current snapshot only; no 13F change history |
| Position, weight, cost basis, unrealized gain, role, account type | `[BUILT]` | `db.portfolio_context` |
| Portfolio-level group/sector/currency exposure | `[PARTIAL]` | Functions exist in `analytics.py`; not exposed to any analyst-facing output |
| Macro/market context | `[PARTIAL]` | See §2 and §4 row 8 above |
| Prior thesis + decision history | `[BUILT]` | `kb_pages.last_decision_history_row` / `parse_status_block` |

---

## 7. Open decisions this review does not resolve

- **TRACE thresholds are literally blank** in the combined plan's Phase 0 (`TRACE blocking threshold: ______`, `warning threshold: ______`, `override authority: ______`). Someone has to fill these in before Phase 0 can close.
- **The Portfolio Manager's vocabulary has no home.** The chosen enum (`initiate|add|hold|trim|exit|avoid`) does not match the repo's existing `Knowledge-Base/taxonomy/decision-framework.yml` (`Buy|Sell|Hold|Trim|Add|Watchlist|Avoid`). Either extend that file with a v2 block or give the Portfolio Manager its own taxonomy file — Document B's Phase 11 gate depends on this being decided.
- **Whether the existing combined plan gets amended** to fold in this review's findings, or the two documents stand side by side with the roadmap (Document B) as the operative one going forward.

---

## 8. Recommended sequence

See [investment-analyst-rebuild-roadmap.md](investment-analyst-rebuild-roadmap.md) for the full ordered build plan. One-line summary: close the two cheap data gaps identified above (per-security technicals from existing OHLCV; the normalized ratio set) *before* building the analyst and running any benchmark, so the benchmark measures reasoning quality rather than data starvation.
