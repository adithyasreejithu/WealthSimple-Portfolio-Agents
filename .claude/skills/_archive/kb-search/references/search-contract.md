# Search contract

## Filters (`kb_search.py`)

All supplied filters combine with AND:

- `--query TEXT` — case-insensitive substring match against front-matter
  `title`/`summary`/`tickers`/`tags` (a "front-matter hit") and against body
  text line-by-line (a "body hit"). Omit to browse by the other filters
  alone.
- `--ticker TICKER` — exact match (case-insensitive) against front-matter
  `tickers`.
- `--type TYPE` — exact match against front-matter `type`; must be one of
  the types in `Knowledge-Base/templates/front-matter-spec.md`.
- `--tag TAG` — exact match (case-insensitive) against front-matter `tags`.
- `--status STATUS` — exact match against front-matter `status`.
- `--limit N` — cap on returned results (default 20).
- `--json` — machine-readable output instead of the text summary.

Index pages (`index.md`) are excluded from search results — they are
navigation aids, not content.

## Ranking

1. Front-matter hits rank above body-only hits.
2. Within each group, more body-line matches rank higher.
3. Ties break by path for determinism.

## Output

Each result: `path` (repo-relative to `Knowledge-Base/`), `title`, `type`,
`tickers`, `status`, `updated`, `summary`, and up to 5 matched body lines
with 1-indexed line numbers into the page file. A query with zero matches is
not an error — the script exits 0 and reports "No matches."

## `validate_kb.py`

Checks, over every wiki page (indexes included):

1. **Front matter** — required fields present, `type` known, `status`
   allowed for that `type`, `tickers` uppercase, `tags` kebab-case,
   `created`/`updated` are `YYYY-MM-DD`, `summary` non-empty, `sources`
   entries have `title` and `ref`.
2. **Related links** — every path in a page's `related` list resolves to an
   existing file relative to that page.
3. With `--fix-indexes`: regenerates every `index.md` table (including the
   `theses/<status>/` views and the main `index.md` recently-updated table)
   from current front matter before running checks 1–2.

Exit code is 0 only when zero issues are found; otherwise every issue is
printed to stderr and the script exits 1.
