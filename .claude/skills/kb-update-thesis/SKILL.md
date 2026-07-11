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
4. Run `thesis_page.py validate --path stocks/<TICKER>.md` before finishing.
5. Log the outcome: `append_log.py --log decision ...` (if a decision was
   made or reaffirmed) and `append_log.py --log update ...` (always, for
   any page mutation).

See [thesis-contract.md](references/thesis-contract.md) for the immutability
rules and verdict vocabulary in full.

Do not run arbitrary SQL or Python, do not create pages outside
`Knowledge-Base/stocks/`, and do not touch `Knowledge-Base/ref/*.yaml` or
`CHANGELOG.md`.
