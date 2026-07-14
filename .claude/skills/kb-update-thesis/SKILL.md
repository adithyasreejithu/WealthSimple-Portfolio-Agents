---
name: kb-update-thesis
description: Create or update a canonical stock thesis page (Knowledge-Base/stocks/TICKER.md) following the goal doc's thesis-update logic -- preserving Original Thesis and Decision History, comparing new information against old assumptions, and logging the outcome. Use when the user asks to create a thesis for a stock, update an existing thesis, or change a stock's portfolio status (active/watchlist/closed/rejected).
---

# KB Update Thesis

Run `.claude/skills/kb-search/scripts/kb_search.py --ticker <TICKER>` first,
every time, to check whether a page already exists before deciding whether
to create or update.

Then run only:

```
python .claude/skills/kb-update-thesis/scripts/thesis_page.py create --ticker T --status research|watchlist|active|closed|rejected [--company-name "..."]
python .claude/skills/kb-update-thesis/scripts/thesis_page.py validate --path <stocks/TICKER.md>
python .claude/skills/kb-update-thesis/scripts/thesis_page.py set-status --ticker T --status research|watchlist|active|closed|rejected
python .claude/skills/kb-update-thesis/scripts/append_log.py --log decision|research|update ...
python .claude/skills/kb-update-thesis/scripts/ingest_recommendation.py --path <exports/stock-recommendations/TICKER-DATE.json>
```

## Workflow

1. **Search first.** If `stocks/<TICKER>.md` exists, this is an update, not
   a create.
2. **No existing page:** run `create`, then `Read` the new page and fill in
   the section content yourself (Company Overview, Original Thesis,
   analysis sections, Bull/Bear Case, Key Risks) using `Edit`. Run
   `append_log.py --log decision --action Watchlist --verdict new` (or the
   appropriate action) and `--log update`.
3. **Existing page:** `Read` it, identify the assumptions behind the
   current Original Thesis, compare against the new information you have.
   Using `Edit`, update **only**: Updated Thesis, the analysis sections
   (Financial/Valuation/Technical/Options/Sentiment/Insider/Earnings/
   Dividend/Portfolio Fit), Bull Case, Bear Case, Key Risks, Open Questions,
   Monitoring Checklist, and append (never rewrite) a Decision History row.
   **Never edit Original Thesis.** State explicitly in Updated Thesis
   whether the thesis is stronger, weaker, unchanged, or broken. If the
   portfolio status or Decision/Confidence/Time Horizon changed, run
   `set-status` for the status and edit the Status block's other fields
   directly.
4. **Recommendation ingestion:** when a `stock-analyst` recommendation artifact
   exists (`exports/stock-recommendations/<TICKER>-<date>.json`), the page must
   already exist (create it first if needed). Run
   `ingest_recommendation.py --path <artifact>` — it re-validates the artifact
   against the rubric, rewrites the Status block's Decision/Confidence/Time
   Horizon/Last Updated (never Portfolio Status), appends a Decision History row,
   and appends the decision-log + update-log rows. Then `Read` the page and
   `Edit` the prose from the artifact's `narratives`: Updated Thesis (state
   stronger/weaker/unchanged/broken), the analysis `section_updates`, the
   **Analyst View** section, Bull/Bear Case, Key Risks, Open Questions,
   Monitoring Checklist, and Sources. **Never edit Original Thesis.** A Sell
   recommendation does not change Portfolio Status — only a user-confirmed trade
   drives `set-status`.
5. Run `thesis_page.py validate --path stocks/<TICKER>.md` before finishing.
6. Log the outcome: `append_log.py --log decision ...` (if a decision was
   made or reaffirmed — the ingest path already does this) and
   `append_log.py --log update ...` (always, for any page mutation).

See [thesis-contract.md](references/thesis-contract.md) for the immutability
rules and verdict vocabulary in full.

## Multiple tickers

Run this skill for one ticker at a time, never concurrently for several
tickers in the same request. `thesis_page.py` and `append_log.py` read a
shared file (`stocks/index.md`, `theses/<status>/index.md`, the main
`index.md`, `logs/*.md`) in full and overwrite it in full, with no locking --
two tickers committed at once can each read before the other writes back,
silently dropping one ticker's row. This only affects the write step; the
upstream `fetch-stock-research-data`/`evaluate-stock-decision` research and
scoring for different tickers is safe to run in parallel since it never
touches these shared files.

Do not run arbitrary SQL or Python, do not create pages outside
`Knowledge-Base/stocks/`, and do not touch `Knowledge-Base/ref/*.yaml` or
`CHANGELOG.md`.
