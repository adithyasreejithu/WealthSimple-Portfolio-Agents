---
name: kb-intake-document
description: Convert an external document (PDF, HTML, DOCX, PPTX, XLSX, or plain markdown/text) into a research-wiki source page via markitdown, file it under Knowledge-Base/sources/, and optionally scaffold a companion research note. Use when the user asks to add, ingest, or file a document into the knowledge base.
---

# KB Intake Document

Run only `python .claude/skills/kb-intake-document/scripts/intake_document.py`.

Permit only:

- `--input <path>` — must resolve inside the repository and have an
  allowlisted extension (`.pdf .html .htm .docx .pptx .xlsx .md .txt`).
- `--title <text>`
- `--tickers <comma-separated>` (optional)
- `--doc-type <type>` — one of `research-note`, `earnings-note`,
  `dividend-note`, `sector-note`, `macro-note`, `sentiment-note`,
  `options-note`; required unless `--dest-folder sources-only`.
- `--dest-folder <folder>` — one of `earnings`, `sector`, `macro`,
  `sentiment`, `options`, `dividends`, `sources-only`. See
  [intake-contract.md](references/intake-contract.md) for what each maps to.
- `--source-date <YYYY-MM-DD>` (optional; defaults to today)

Do not run arbitrary SQL or Python, do not accept a `--dest-folder` outside
the allowlist above, and do not write anywhere outside
`Knowledge-Base/sources/` and the one allowlisted destination folder. The
script refuses inputs outside the repository and refuses to overwrite an
existing source page or note. It converts via markitdown faithfully — never
summarize, edit, or omit content from the converted source page; editorial
work belongs in the companion note, not the source page.
