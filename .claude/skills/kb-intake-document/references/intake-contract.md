# Intake contract

## Extension allowlist

`.pdf`, `.html`, `.htm`, `.docx`, `.pptx`, `.xlsx`, `.md`, `.txt`. Anything
else is rejected before conversion is attempted.

## Filing rules

1. The document is converted to markdown via `markitdown` and written
   verbatim (no summarization) to
   `Knowledge-Base/sources/<year>/<source-date>-<slug>.md` with
   `type: source-document`, `status: final` front matter. `<year>` and
   `<source-date>` come from `--source-date` (default: today); `<slug>` is
   `--title` slugified.
2. If `--dest-folder` is not `sources-only`, a companion research-note
   scaffold (from `templates/research-note-template.md`'s structure) is
   created at `Knowledge-Base/<dest-folder-path>/<source-date>-<slug>.md`
   with `status: draft` and a `sources` entry pointing back at the source
   page. The note's `type` comes from `--doc-type` (or the destination's
   default type if `--doc-type` is omitted).
3. `--dest-folder` mapping:

   | `--dest-folder` | Filed under | Default note type |
   |---|---|---|
   | `earnings` | `earnings/earnings-notes/` | `earnings-note` |
   | `sector` | `market-research/sector-notes/` | `sector-note` |
   | `macro` | `market-research/macro-notes/` | `macro-note` |
   | `sentiment` | `market-research/sentiment-notes/` | `sentiment-note` |
   | `options` | `market-research/options-notes/` | `options-note` |
   | `dividends` | `dividends/` | `dividend-note` |
   | `sources-only` | (no note) | — |

4. The nearest ancestor `index.md` for the destination folder is
   regenerated, along with `sources/index.md` and the main
   `Knowledge-Base/index.md` recently-updated table.
5. One row is appended to `logs/research-log.md` (what was ingested, from
   where) and one row per file created to `logs/update-log.md`.

## What this script never does

- Read or write outside `Knowledge-Base/sources/` and the one allowlisted
  destination folder.
- Accept an `--input` path outside the repository.
- Overwrite an existing source page or note (same date + slug) — the run
  fails instead, so re-ingesting the same document on the same day requires
  a different `--source-date` or `--title`.
- Edit `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md`.
- Summarize or alter the converted document's content in the source page —
  that page is the faithful record; analysis belongs in the companion note.
