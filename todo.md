# Phase 1: Stock Decision-Support System — File Tracker

This file lists all new and modified files from the Phase 1 implementation (research knowledge base, kb-discovery agent, kb-intake agent). Review these before Phase 2 work begins.

---

## Core Shared Module

- `src/kb_pages.py` — Front-matter YAML parsing/serialization/validation, index-table rendering and row upsert, append-only log formatting, slug/path helpers, KB root resolver. Used by all KB skills and agents.
- `tests/test_kb_pages.py` — 18 unit tests for kb_pages.py functions (validation, index rendering, log append, page collection).

---

## Skills

### kb-search

- `.claude/skills/kb-search/SKILL.md` — Deterministic skill definition; allows `--query`, `--ticker`, `--type`, `--tag`, `--status`, `--limit`, `--json` flags for front-matter-aware search and validation.
- `.claude/skills/kb-search/scripts/kb_search.py` — Search script with AND-combined filters, front-matter hit ranking, exit 0 on no matches.
- `.claude/skills/kb-search/scripts/validate_kb.py` — Wiki validator: checks front-matter schema, validates index tables, checks related-link resolution, `--fix-indexes` regenerates index.md files.
- `.claude/skills/kb-search/references/search-contract.md` — Specification of search behavior and output format.
- `tests/test_kb_search.py` — 14 unit tests for kb_search.py and validate_kb.py (search filters, ranking, linting, static index preservation).

### kb-sync-portfolio

- `.claude/skills/kb-sync-portfolio/SKILL.md` — Deterministic skill definition; allows `--classification` (path to JSON), `--check` (dry-run) flags.
- `.claude/skills/kb-sync-portfolio/scripts/sync_portfolio_pages.py` — Regenerates `portfolio/holdings.md` and `portfolio/portfolio-overview.md` from classification JSON with `status: generated` provenance; upserts indexes, appends update-log.
- `.claude/skills/kb-sync-portfolio/references/page-contract.md` — Specification of generated page structure and data sources.
- `tests/test_kb_sync_portfolio.py` — 7 unit tests for sync_portfolio_pages.py (holdings table, overview analysis, index updates, deterministic generated_at).

### kb-intake-document

- `.claude/skills/kb-intake-document/SKILL.md` — Deterministic skill definition; allows `--input`, `--title`, `--tickers`, `--doc-type`, `--dest-folder`, `--source-date` flags.
- `.claude/skills/kb-intake-document/scripts/intake_document.py` — Markitdown conversion (lazy import) to `sources/<year>/<date>-<slug>.md`; optional companion research-note scaffold; index upserts; research-log and update-log appends; path validation.
- `.claude/skills/kb-intake-document/references/intake-contract.md` — Specification of ingestion behavior, allowlists, and companion note scaffolding.
- `tests/test_kb_intake_document.py` — 7 unit tests for intake_document.py (path allowlists, source filing, companion scaffold, index/log updates, markitdown mocked).

### kb-update-thesis

- `.claude/skills/kb-update-thesis/SKILL.md` — Deterministic skill definition; documents the thesis-update workflow (search first, never rewrite Original Thesis, append-only logs).
- `.claude/skills/kb-update-thesis/scripts/thesis_page.py` — Subcommands: `create` (template instantiation, classification JSON pre-fill), `validate` (structural check, all 19 sections present), `set-status` (front-matter + thesis-view regeneration).
- `.claude/skills/kb-update-thesis/scripts/append_log.py` — Append-only log row writer: `--log decision|research|update` with required verdict/sources/page and optional note.
- `.claude/skills/kb-update-thesis/references/thesis-contract.md` — Immutability rules (Original Thesis locked, Decision History append-only) and verdict vocabulary.
- `tests/test_kb_thesis_scripts.py` — 15 unit tests for thesis_page.py and append_log.py (create, validate, set-status, all log types, classification pre-fill).

---

## Agents

- `.claude/agents/kb-discovery.md` — Read-only agent (model: haiku, tools: Bash+Read). Searches the KB via kb-search skill; returns Context Report of matched pages, current thesis status, related sources.
- `.claude/agents/kb-intake.md` — Write-only agent (model: sonnet, tools: Bash+Read+Edit+Write). The sole KB writer; runs kb-search first; invokes skills for deterministic edges; uses Edit/Read for prose judgment; outputs Wiki Update Summary.

---

## Agents Documentation

### kb-discovery

- `docs/agents/kb-discovery/architecture.md` — Purpose, runtime settings (haiku, Bash+Read), skill dependency (kb-search), workflow, guardrails, code locations.
- `docs/agents/kb-discovery/plan.md` — Summary, agent and skill structure, fixed workflow, why search comes first, tests and acceptance criteria, assumptions.

### kb-intake

- `docs/agents/kb-intake/architecture.md` — Purpose (sole KB writer), runtime settings (sonnet, Bash+Read+Edit+Write), skill dependencies (kb-search, kb-intake-document, kb-update-thesis, kb-sync-portfolio), workflow, guardrails, code locations.
- `docs/agents/kb-intake/plan.md` — Summary, agent and skill structure, why judgment lives outside scripts, why search comes first, tests and acceptance criteria, assumptions.

---

## Knowledge-Base Scaffolding

### Main index and README

- `Knowledge-Base/index.md` — Main wiki index page with recently-updated table; front-matter metadata.
- `Knowledge-Base/README.md` — UPDATED to document two zones (ref/ = classifier YAMLs, unchanged; rest = research wiki).

### Portfolio section

- `Knowledge-Base/portfolio/index.md` — Portfolio section index page.
- `Knowledge-Base/portfolio/holdings.md` — **GENERATED** by kb-sync-portfolio; ticker, company, group, weight, value, quantity, yield, confidence, link to stocks/TICKER.md.
- `Knowledge-Base/portfolio/portfolio-overview.md` — **GENERATED** by kb-sync-portfolio; group allocation vs policy targets, currency split, needs-review list.
- `Knowledge-Base/portfolio/allocation-policy.md` — Hand-curated narrative describing portfolio allocation strategy; links to `ref/policy_v1_1.yaml`.
- `Knowledge-Base/portfolio/risk-log.md` — Append-only risk register (portfolio-level risks, not ticker-specific).

### Stocks section

- `Knowledge-Base/stocks/index.md` — Canonical stock pages index; table of all TICKER.md files.

### Theses section (status views)

- `Knowledge-Base/theses/index.md` — Navigation page explaining status-view model; hand-curated (static, not auto-rebuilt).
- `Knowledge-Base/theses/active/index.md` — **GENERATED** view of active holdings linked to stocks/; regenerated by kb_pages.rebuild_thesis_views().
- `Knowledge-Base/theses/watchlist/index.md` — **GENERATED** view of watchlist stocks.
- `Knowledge-Base/theses/closed/index.md` — **GENERATED** view of closed/sold positions.
- `Knowledge-Base/theses/rejected/index.md` — **GENERATED** view of rejected thesis stocks.

### Earnings section

- `Knowledge-Base/earnings/index.md` — Earnings research section index.
- `Knowledge-Base/earnings/earnings-notes/` — Folder for dated earnings analysis notes (scaffolded by kb-intake-document, filled by kb-intake agent).

### Dividends section

- `Knowledge-Base/dividends/index.md` — Dividend research section index.

### Market research section

- `Knowledge-Base/market-research/index.md` — Market research section index.
- `Knowledge-Base/market-research/sector-notes/` — Folder for sector-level research (scaffolded by kb-intake-document).
- `Knowledge-Base/market-research/macro-notes/` — Folder for macro economic research (scaffolded by kb-intake-document).
- `Knowledge-Base/market-research/sentiment-notes/` — Folder for market sentiment and technicals (scaffolded by kb-intake-document).
- `Knowledge-Base/market-research/options-notes/` — Folder for options activity and volatility analysis (scaffolded by kb-intake-document).

### Sources section

- `Knowledge-Base/sources/index.md` — Registry of all ingested external documents; front-matter index.
- `Knowledge-Base/sources/2026/` — Folder for **GENERATED** markitdown conversions of ingested PDFs/reports, filed by date.

### Taxonomy section

- `Knowledge-Base/taxonomy/index.md` — Taxonomy navigation page; documents and links `ref/*_v1_1.yaml` as source of truth; hand-curated (static).
- `Knowledge-Base/taxonomy/decision-framework.yml` — NEW: decision (Buy/Sell/Hold/Trim/Add/Watchlist/Avoid), confidence, time-horizon, and verdict enums (concepts not covered in ref/).

### Templates section

- `Knowledge-Base/templates/index.md` — Template registry page.
- `Knowledge-Base/templates/front-matter-spec.md` — Central specification for all KB page metadata (YAML field names, types, allowed values, examples).
- `Knowledge-Base/templates/stock-thesis-template.md` — Template for new stocks/TICKER.md pages (all 19 required sections, Status block, Original Thesis, Decision History, Monitoring Checklist).
- `Knowledge-Base/templates/research-note-template.md` — Template for earnings/market-research notes; scaffold structure.
- `Knowledge-Base/templates/portfolio-review-template.md` — Template for portfolio-level quarterly/annual review notes.
- `Knowledge-Base/templates/sell-decision-template.md` — Template for documenting sell decisions and thesis closure.

### Logs section

- `Knowledge-Base/logs/index.md` — Log section navigation page; hand-curated (static).
- `Knowledge-Base/logs/decision-log.md` — **APPEND-ONLY** record of all buy/sell/trim/add/reject decisions (date, tickers, action, verdict, note).
- `Knowledge-Base/logs/research-log.md` — **APPEND-ONLY** record of research activities (date, tickers, action, sources, note).
- `Knowledge-Base/logs/update-log.md` — **APPEND-ONLY** changelog for the research wiki; appended by every skill that mutates a page.

---

## Architecture and Reference Documentation

- `docs/architecture/knowledge_base.md` — Comprehensive architecture doc describing KB layout, front-matter, page ownership, skills/agents map, changelog split, no new CLI commands.
- `docs/reference/knowledge_base_workflow.md` — Workflow reference guide mapping agent workflows to KB file storage and data flows; file index, agent workflows, data flow diagrams, common tasks matrix, immutability rules, skill script reference, validation guide.
- `docs/plans/implementation/stock_decision_support_phase1.md` — Phase 1 implementation plan with summary, implementation steps, success criteria, assumptions, critical reference files.

---

## Repository-level Updates

- `CLAUDE.md` — UPDATED with Research Knowledge Base section describing KB conventions (front-matter required on every page, generated vs. curated page ownership, KB agents never edit ref/), references to new architecture doc.
- `requirements.txt` — UPDATED to add `markitdown[pdf,docx,pptx,xlsx]` dependency for document ingestion.

---

## Summary by File Count

| Category | New Files | Modified Files |
|---|---|---|
| Core module + tests | 2 | — |
| Skills (4 × scripts + references + SKILL.md + tests) | 14 | — |
| Agents (2 × agent definition) | 2 | — |
| Agent documentation | 4 | — |
| KB scaffolding | 33 | 1 (README.md) |
| Architecture + workflow docs | 3 | — |
| Repository updates | — | 2 (CLAUDE.md, requirements.txt) |
| **Total** | **58** | **3** |

**Total files: 61** (58 new, 3 modified).

---

## Status: Ready for Review

All Phase 1 files have been implemented and tested:
- ✅ 287 unit tests passing
- ✅ `validate_kb.py` clean over scaffolded KB
- ✅ End-to-end smoke test confirmed (document ingestion, thesis creation, decision logging, status transitions, immutability)

**Next step:** Review all files in this list, then Phase 2 (remaining agents per goal doc) can begin.
