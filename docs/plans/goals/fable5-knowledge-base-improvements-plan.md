# Knowledge-Base Evaluation & Improvement Plan

## Context

An evaluation of `Knowledge-Base/` — its structure, how it holds data, and how agents interact with it — surfaced that the architecture is sound but under-enforced and largely unpopulated. This plan captures the findings and the approved improvement tracks: integrity/health tooling, sync-script correctness fixes, a content-seeding workflow, and small search upgrades.

**Evaluation verdict: the architecture is sound; the gaps are enforcement, freshness signals, and content.**

What works well (keep as-is):
- **Two-zone split** — `ref/*.yaml` (approved classifier reference, v1.1, versioned in `CHANGELOG.md`) vs. the research wiki (everything else, logged in `logs/update-log.md`). Ownership boundaries are clean and consistently documented.
- **Single implementation substrate** — `src/kb_pages.py` owns front-matter parse/validate/index-render/log-append; all skill scripts import it. No duplication.
- **Front-matter spec** (`templates/front-matter-spec.md`) is complete and every one of the 27 wiki pages complies today.
- **Agent separation** — `kb-discovery` (haiku, read-only, kb-search only) vs. `kb-intake` (sonnet, sole wiki writer, search-first mandate). Guardrails are well-written.
- The decision/template consolidation plan (`docs/plans/consolidate-decisions-in-thesis.md`) is already applied in the working tree (old templates and v1 YAMLs deleted, uncommitted).

Gaps found:
1. **The wiki is empty.** `stocks/`, `theses/*`, `sources/`, `earnings/`, `dividends/`, `market-research/` contain only index files. 26 holdings, 0 thesis pages.
2. **No coverage/staleness signal.** Nothing reports which held tickers lack thesis pages, whether `portfolio/holdings.md` is stale vs. the classification JSON, or which active theses haven't been touched in months.
3. **Immutability is convention-only.** "Never edit Original Thesis / log rows" is enforced by agent prompts, not by any check. `thesis_page.py validate` is structural only.
4. **Sync-script correctness nits.** `portfolio-overview.md` allocation table omits a Needs Review row (~2% unaccounted); negative positions (WN −2.66%, ZEQT −2.0 qty) render without any flag.
5. **Search is a linear substring scan** — acceptable at current scale, but has no related-link traversal and re-parses every page per query.

## Track 1 — Integrity & health tooling (highest priority)

New module `.claude/skills/kb-search/scripts/kb_health.py` (kb-search skill owns read-only health checks; reuse `kb_pages.collect_wiki_pages`, `parse_page_file`), with one CLI entry point running these checks:

1. **Coverage report** — load `exports/portfolio-classification/portfolio-classification.json`, compare held tickers against existing `Knowledge-Base/stocks/<TICKER>.md` pages. Output: held-but-no-thesis (sorted by weight desc), thesis-but-not-held (candidates for status change).
2. **Portfolio staleness** — reuse the existing `--check` logic in `.claude/skills/kb-sync-portfolio/scripts/sync_portfolio_pages.py` (import or subprocess) to report whether generated pages match the JSON; also warn if the JSON's own timestamp is older than N days (default 7).
3. **Stale thesis detection** — any `type: stock-page` with `status: active` whose `updated` is older than N days (default 90) is flagged.
4. **Original-Thesis immutability guard** — on thesis create, `thesis_page.py create` stores `original_thesis_sha256` in front matter (hash of the normalized "## Original Thesis" section body). `kb_health.py` (and `validate_kb.py`) recompute and compare; mismatch = error. Add the field as optional in `front-matter-spec.md` and `kb_pages.validate_meta`. Note: hash is set/updated once when the agent first fills in the Original Thesis prose — provide `thesis_page.py seal` subcommand to (re)compute the hash, allowed only when the field is absent or the page status is still the initial `research`/`watchlist` creation flow; document the rule in `references/thesis-contract.md`.

Wire-up:
- Extend `validate_kb.py` main() to optionally run health checks (`--health` flag) so one command covers lint + health.
- Update `kb-search/SKILL.md` + `references/search-contract.md` to allow the new script; update `kb-discovery.md` agent guardrails to permit running it.
- Tests: new `tests/test_kb_health.py` mirroring the style of `tests/test_kb_search.py` (temp KB fixture, deterministic).

## Track 2 — Sync-script fixes (small, do alongside Track 1)

In `.claude/skills/kb-sync-portfolio/scripts/sync_portfolio_pages.py`:
1. Add a **Needs Review row** to the Group Allocation table in `portfolio-overview.md` (current weight shown, target/min/max rendered as `—`), so the table sums to ~100%.
2. **Flag negative positions**: any holding with negative quantity or market value gets a marker in `holdings.md` (e.g., `⚠ negative position` note column or a dedicated list under Needs Review) instead of rendering silently.
3. Update `references/page-contract.md` and regenerate the pages; extend `tests/test_kb_sync_portfolio.py` with fixtures covering both cases.

## Track 3 — Content seeding workflow (no new code; process + one helper)

Populate the empty wiki using the existing `kb-intake` agent / `kb-update-thesis` skill:
1. Run the Track-1 coverage report to get the priority list (holdings by weight desc).
2. Seed in batches via `kb-intake`: for each ticker, `thesis_page.py create` (already pre-fills company/Portfolio Role from the classification JSON), then the agent fills Original Thesis/valuation/risk prose, then `seal`. Start with the top ~5 weights (per current holdings: XEQT-class core ETFs and largest single names first), then work down; ETFs get lighter theses (role-in-portfolio focused) than single names.
3. After each batch: `validate_kb.py --fix-indexes`, confirm `theses/<status>/` views populate, and `holdings.md` company links to `stocks/TICKER.md` begin resolving (the sync script already links when the page exists — regenerate via kb-sync-portfolio).
4. This is agent-driven work sessions, not repo code; the plan's deliverable is documenting the batch procedure in `docs/reference/knowledge_base_workflow.md` (short "Seeding the wiki" section).

## Track 4 — Search upgrades (minimal, scale-appropriate)

Keep substring search; add only what has payoff now in `.claude/skills/kb-search/scripts/kb_search.py`:
1. `--related <page>` — list pages whose `related`/`sources` front matter references the given page, and vice-versa (one-hop backlink traversal using already-parsed front matter).
2. Include `related` paths in `--json` output so `kb-discovery` can follow links without extra searches.
3. Explicitly **defer** caching/semantic search — re-parsing ~30 pages per query is microseconds; revisit if the wiki exceeds a few hundred pages.
4. Update `references/search-contract.md` + `tests/test_kb_search.py`.

## Files to modify (representative)

- `.claude/skills/kb-search/scripts/kb_health.py` (new), `validate_kb.py`, `kb_search.py`, `SKILL.md`, `references/search-contract.md`
- `.claude/skills/kb-update-thesis/scripts/thesis_page.py` (+`seal`), `references/thesis-contract.md`
- `.claude/skills/kb-sync-portfolio/scripts/sync_portfolio_pages.py`, `references/page-contract.md`
- `src/kb_pages.py` (`validate_meta`: optional `original_thesis_sha256`), `Knowledge-Base/templates/front-matter-spec.md`
- `.claude/agents/kb-discovery.md` (allow kb_health), `docs/reference/knowledge_base_workflow.md`, `docs/architecture/knowledge_base.md`
- Tests: `tests/test_kb_health.py` (new), `tests/test_kb_sync_portfolio.py`, `tests/test_kb_search.py`, `tests/test_kb_thesis_scripts.py`

No CLI additions to `src/app.py` (KB workflows are skill scripts by design), so `docs/reference/cli.md` is unaffected.

## Suggested order

1. Track 2 (small, immediate correctness) → 2. Track 1 (health tooling; coverage report unblocks seeding) → 3. Track 4 (small search additions) → 4. Track 3 (seeding sessions using the new tooling).

## Verification

- `python -m unittest discover -s tests` — full suite green.
- `python .claude/skills/kb-search/scripts/validate_kb.py --health` on the real KB: reports 26 uncovered tickers, portfolio pages fresh/stale correctly.
- Regenerate portfolio pages via kb-sync-portfolio: overview table includes Needs Review row and sums ~100%; WN/ZEQT flagged.
- Create one real thesis end-to-end (create → fill → seal → validate); then hand-edit the Original Thesis section in a scratch copy and confirm the guard fails.
- `kb_search.py --related stocks/<TICKER>.md` returns the expected backlinks.
