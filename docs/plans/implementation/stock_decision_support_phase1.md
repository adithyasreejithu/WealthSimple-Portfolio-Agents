# Stock Decision-Support System — Phase 1: Research KB, Discovery Agent, Intake Agent

## Summary

Implement phase 1 of `docs/plans/goals/stock_decision_support_system.md`: a Karpathy-style
research wiki inside the existing `Knowledge-Base/` directory, a read-only **kb-discovery**
agent that finds existing pages/theses/sources, and a **kb-intake** agent that brings
information into the wiki (document ingestion via Microsoft markitdown, thesis
create/update with history preservation, and portfolio data sync from the classification
pipeline).

Key decisions:

- `Knowledge-Base/` already exists and holds the approved classifier YAMLs
  (`ref/*_v1_1.yaml`, wired via `src/config.py`). Windows filesystems are
  case-insensitive, so a new `knowledge-base/` would be the same directory. The research
  wiki therefore **extends** `Knowledge-Base/` with new subfolders; `ref/` and
  `src/config.py` paths are never touched.
- Taxonomy is **linked, not copied**: `taxonomy/index.md` documents the `ref/*_v1_1.yaml`
  files as the source of truth. Only `taxonomy/decision-framework.yml` is new content
  (decision/confidence/time-horizon/verdict enums, which have no existing equivalent).
- Generated portfolio pages sync from `exports/portfolio-classification/portfolio-classification.json`
  (already joins holdings, groups, weights, metadata) — no direct DuckDB access.
- Canonical stock pages live at `stocks/TICKER.md` with a `status` front-matter field;
  `theses/<status>/index.md` files are regenerated views so pages never move and links
  never break.
- Changelog split: `Knowledge-Base/CHANGELOG.md` stays classifier-YAML-only;
  `logs/update-log.md` is the research wiki's own change log.
- No new `src/app.py` CLI commands — skill scripts are the entry points (same pattern as
  `reconcile-holdings-report`), so `docs/reference/cli.md` is unchanged.

## Implementation Steps

1. **Foundation.**
   - Save this plan document.
   - Create `src/kb_pages.py`: front-matter parse/serialize/validate against the spec,
     index-table rendering and row upsert, log-append formatting, slug/path helpers, KB
     root resolver from `src/config.py`'s `KNOWLEDGE_BASE_FOLDER`. Pure stdlib + PyYAML.
   - Add `tests/test_kb_pages.py`.
   - Scaffold the wiki tree: `index.md` (main index + recently-updated table),
     `portfolio/` (index, allocation-policy.md, risk-log.md), `stocks/index.md`,
     `theses/` status-view indexes (active/watchlist/closed/rejected), `earnings/`
     (+`earnings-notes/`), `dividends/`, `market-research/` (+sector/macro/sentiment/
     options note folders), `sources/` (+`2026/`), `taxonomy/` (index +
     `decision-framework.yml`), `templates/` (front-matter-spec.md,
     stock-thesis-template.md, research-note-template.md, portfolio-review-template.md,
     sell-decision-template.md), `logs/` (decision-log.md, research-log.md,
     update-log.md, index.md).
   - Every page carries YAML front matter per `templates/front-matter-spec.md`
     (title, type, tickers, tags, status, created, updated, summary, related, sources).
   - Update `Knowledge-Base/README.md` to document the two zones (ref/ = classifier
     YAMLs; everything else = research wiki) and the changelog split.

2. **kb-sync-portfolio skill.** `.claude/skills/kb-sync-portfolio/` with
   `scripts/sync_portfolio_pages.py` (`--classification`, `--check` dry-run) regenerating
   `portfolio/holdings.md` and `portfolio/portfolio-overview.md` (group allocation vs
   `ref/policy_v1_1.yaml` targets, currency split, review-needed list) with
   `status: generated` front matter and `generated_at` provenance; upserts
   `portfolio/index.md`; appends `logs/update-log.md`. Plus
   `references/page-contract.md` and `tests/test_kb_sync_portfolio.py`. Run against the
   real classification JSON.

3. **kb-search skill + kb-discovery agent.** `.claude/skills/kb-search/` with
   `scripts/kb_search.py` (`--query/--ticker/--type/--tag/--status/--limit/--json`,
   AND-combined filters, front-matter hits ranked above body hits, exit 0 on no matches)
   and `scripts/validate_kb.py` (front-matter lint, index/link checks, `--fix-indexes`),
   `references/search-contract.md`, `tests/test_kb_search.py`. Agent
   `.claude/agents/kb-discovery.md` (haiku, tools Bash+Read, skill kb-search) returning a
   Context Report; docs under `docs/agents/kb-discovery/`.

4. **Intake.** Add `markitdown[pdf,docx,pptx,xlsx]` to `requirements.txt`.
   `.claude/skills/kb-intake-document/` with `scripts/intake_document.py` (extension and
   destination allowlists, converts via markitdown into `sources/<year>/<date>-<slug>.md`,
   optional companion research-note scaffold, index upserts, research-log + update-log
   appends, refuses writes outside `Knowledge-Base/`).
   `.claude/skills/kb-update-thesis/` with `scripts/thesis_page.py`
   (create/validate/set-status subcommands, pre-fills from the classification JSON) and
   `scripts/append_log.py` (append-only log rows with verdict vocabulary); SKILL.md
   carries the thesis-update workflow (search first; never rewrite Original Thesis or
   Decision History). Agent `.claude/agents/kb-intake.md` (sonnet — thesis comparison
   needs judgment; tools Bash/Read/Edit/Write; skills kb-search, kb-intake-document,
   kb-update-thesis, kb-sync-portfolio) returning a Wiki Update Summary; docs under
   `docs/agents/kb-intake/`. Tests: `tests/test_kb_intake_document.py`,
   `tests/test_kb_thesis_scripts.py` (markitdown mocked).

5. **Docs + verification.** New `docs/architecture/knowledge_base.md`; short CLAUDE.md
   note (research-wiki conventions, `ref/` never edited by KB agents); end-to-end checks
   below.

## Success Criteria

- `python -m unittest discover -s tests` passes.
- `validate_kb.py` reports zero errors over the scaffolded wiki.
- `sync_portfolio_pages.py` populates holdings/overview from the real classification
  JSON; a second run with `--check` reports no diff.
- kb-discovery answers "what do we know about AAPL" citing `portfolio/holdings.md`.
- kb-intake ingests a sample document into `sources/`, with indexes and logs updated.
- kb-intake creates a thesis page for a holding; a subsequent update pass leaves
  Original Thesis untouched, changes Updated Thesis, and appends Decision History and
  logs.
- Existing classifier behavior is unchanged (`ref/` untouched, existing tests pass).

## Assumptions

- The classification JSON is regenerated by the existing `classify-portfolio` workflow;
  the wiki sync consumes whatever is on disk and records `generated_at` provenance.
- markitdown output quality is acceptable as a research-note starting point; the intake
  agent may clean up formatting but must not alter substantive content.
- Thesis judgment (comparing new information to old assumptions) is agent work guided by
  `references/thesis-contract.md`; scripts enforce only the deterministic edges
  (structure, status transitions, append-only logs).
