"""Convert an external document into a research-wiki source page via
markitdown, file it, and (optionally) scaffold a companion research note.

See references/intake-contract.md for the extension/destination allowlists
and the exact filing rules this script implements.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402

ALLOWED_EXTENSIONS = {".pdf", ".html", ".htm", ".docx", ".pptx", ".xlsx", ".md", ".txt"}

NOTE_TYPES = (
    "research-note",
    "earnings-note",
    "dividend-note",
    "sector-note",
    "macro-note",
    "sentiment-note",
    "options-note",
)

# --dest-folder value -> (folder path under Knowledge-Base/, note type to
# scaffold there, or None for "file the source only, no companion note").
DEST_FOLDERS: dict[str, tuple[str | None, str | None]] = {
    "earnings": ("earnings/earnings-notes", "earnings-note"),
    "sector": ("market-research/sector-notes", "sector-note"),
    "macro": ("market-research/macro-notes", "macro-note"),
    "sentiment": ("market-research/sentiment-notes", "sentiment-note"),
    "options": ("market-research/options-notes", "options-note"),
    "dividends": ("dividends", "dividend-note"),
    "sources-only": (None, None),
}


class IntakeError(ValueError):
    pass


def convert_to_markdown(input_path: Path) -> str:
    """Convert `input_path` to markdown text via markitdown. Imported lazily
    so this module can be loaded (and this function monkeypatched in tests)
    without the markitdown package installed."""
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(input_path))
    return result.text_content


def validate_input(input_path: Path) -> Path:
    resolved = input_path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise IntakeError(f"input must be inside the repository: {input_path}") from exc
    if resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise IntakeError(
            f"unsupported extension '{resolved.suffix}'; allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if not resolved.is_file():
        raise IntakeError(f"input file not found: {resolved}")
    return resolved


def source_page_path(kb_root: Path, source_date: str, title: str) -> Path:
    year = source_date.split("-")[0]
    slug = kb_pages.slugify(title)
    return kb_root / "sources" / year / f"{source_date}-{slug}.md"


def render_source_page(title: str, tickers: list[str], body: str, source_date: str, today: str) -> str:
    meta = {
        "title": title,
        "type": "source-document",
        "tickers": tickers,
        "tags": ["ingested"],
        "status": "final",
        "created": today,
        "updated": today,
        "summary": f"Markitdown conversion of an ingested document ({source_date}).",
    }
    return kb_pages.serialize_page(meta, f"\n# {title}\n\n{body.strip()}\n")


def nearest_index(folder: Path, kb_root: Path) -> Path:
    """The index.md that owns pages filed under `folder`: itself if it has
    one, else the nearest ancestor's, matching kb_pages.collect_index_pages."""
    current = folder
    while True:
        candidate = current / "index.md"
        if candidate.exists():
            return candidate
        if current == kb_root:
            raise IntakeError(f"no index.md found for {folder} up to {kb_root}")
        current = current.parent


def render_note_scaffold(title: str, note_type: str, tickers: list[str], source_link: str, today: str) -> str:
    meta = {
        "title": title,
        "type": note_type,
        "tickers": tickers,
        "tags": [],
        "status": "draft",
        "created": today,
        "updated": today,
        "summary": "TODO: one-sentence takeaway.",
        "sources": [{"title": title, "ref": source_link, "date": today}],
    }
    body = (
        f"\n# {title}\n\n"
        "## Facts\n\nWhat is verifiably true, with sources.\n\n"
        "## Analysis\n\nInterpretation -- clearly separated from facts.\n\n"
        "## Implications\n\nWhat this means for affected theses or the portfolio.\n\n"
        "## Follow-ups\n\n- [ ] Review the converted source and fill this note in.\n"
    )
    return kb_pages.serialize_page(meta, body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest an external document into the research wiki via markitdown.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--tickers", default="", help="Comma-separated tickers, e.g. AAPL,NVDA")
    parser.add_argument("--doc-type", dest="doc_type", default=None,
                         help="Note type to scaffold; required unless --dest-folder is sources-only")
    parser.add_argument("--dest-folder", dest="dest_folder", required=True, choices=sorted(DEST_FOLDERS))
    parser.add_argument("--source-date", dest="source_date", default=None, help="YYYY-MM-DD; defaults to today")
    args = parser.parse_args()

    try:
        input_path = validate_input(args.input)
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    kb_root = kb_pages.KB_ROOT
    today = kb_pages.today()
    source_date = args.source_date or today
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    dest_rel, default_note_type = DEST_FOLDERS[args.dest_folder]
    note_type = args.doc_type or default_note_type
    if dest_rel is not None and note_type not in NOTE_TYPES:
        print(f"error: unsupported --doc-type '{note_type}'", file=sys.stderr)
        return 1

    body = convert_to_markdown(input_path)

    source_path = source_page_path(kb_root, source_date, args.title)
    if source_path.exists():
        print(f"error: source page already exists: {source_path.relative_to(kb_root)}", file=sys.stderr)
        return 1
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        render_source_page(args.title, tickers, body, source_date, today), encoding="utf-8", newline="\n"
    )

    written = [source_path]
    if dest_rel is not None:
        dest_dir = kb_root / dest_rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        slug = kb_pages.slugify(args.title)
        note_path = dest_dir / f"{source_date}-{slug}.md"
        if note_path.exists():
            print(f"error: note already exists: {note_path.relative_to(kb_root)}", file=sys.stderr)
            return 1
        source_link = "../../sources/" if "market-research" in dest_rel else "../sources/"
        source_link += f"{source_date.split('-')[0]}/{source_path.name}"
        note_path.write_text(
            render_note_scaffold(args.title, note_type, tickers, source_link, today), encoding="utf-8", newline="\n"
        )
        written.append(note_path)

    kb_pages.rebuild_index(kb_root / "sources" / "index.md")
    if dest_rel is not None:
        kb_pages.rebuild_index(nearest_index(kb_root / dest_rel, kb_root))
    kb_pages.rebuild_main_index(kb_root)

    kb_pages.append_log_row(
        kb_root / "logs" / "research-log.md",
        [today, ", ".join(tickers) or "-", "ingested", input_path.name, f"Filed as {source_path.relative_to(kb_root).as_posix()}"],
    )
    for path in written:
        kb_pages.append_update_log(kb_root, "created", path.relative_to(kb_root).as_posix(), f"Ingested from {input_path.name}", on=today)

    for path in written:
        print(f"Created: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
