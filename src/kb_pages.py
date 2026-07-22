"""Shared helpers for the Knowledge-Base research wiki pages.

Every wiki page carries YAML front matter described by
`Knowledge-Base/templates/front-matter-spec.md`. This module is the single
implementation of that spec: parsing/serializing front matter, validating it,
rebuilding index tables from page metadata, and appending rows to the
append-only log files. Skill scripts under `.claude/skills/kb-*` import these
helpers; the module itself is pure stdlib + PyYAML with no network or
database access.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import yaml

from config import KNOWLEDGE_BASE_FOLDER

KB_ROOT = KNOWLEDGE_BASE_FOLDER

FRONT_MATTER_DELIMITER = "---"

PAGE_TYPES = (
    "stock-page",
    "research-note",
    "earnings-note",
    "dividend-note",
    "sector-note",
    "macro-note",
    "sentiment-note",
    "competitor-note",
    "options-note",
    "source-document",
    "portfolio-page",
    "decision-record",
    "taxonomy",
    "template",
    "log",
    "index",
)

STOCK_STATUSES = ("active", "watchlist", "closed", "rejected", "research")
NOTE_STATUSES = ("draft", "final", "archived")

# Allowed `status` values per page type. Generated pages (rebuilt by skill
# scripts rather than curated by hand) use the sentinel status "generated".
STATUS_VALUES: dict[str, tuple[str, ...]] = {
    "stock-page": STOCK_STATUSES,
    "research-note": NOTE_STATUSES,
    "earnings-note": NOTE_STATUSES,
    "dividend-note": NOTE_STATUSES,
    "sector-note": NOTE_STATUSES,
    "macro-note": NOTE_STATUSES,
    "sentiment-note": NOTE_STATUSES,
    "competitor-note": NOTE_STATUSES,
    "options-note": NOTE_STATUSES,
    "source-document": NOTE_STATUSES,
    "portfolio-page": ("generated",) + NOTE_STATUSES,
    "decision-record": NOTE_STATUSES,
    "taxonomy": ("draft", "final"),
    "template": ("draft", "final"),
    "log": ("final",),
    "index": ("generated", "final"),
}

REQUIRED_FIELDS = ("title", "type", "tickers", "tags", "status", "created", "updated", "summary")
OPTIONAL_FIELDS = ("related", "sources", "section_updated", "generated_at")
FIELD_ORDER = REQUIRED_FIELDS + OPTIONAL_FIELDS

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TAG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

INDEX_BEGIN = "<!-- kb-index:begin -->"
INDEX_END = "<!-- kb-index:end -->"
INDEX_HEADER = (
    "| Page | Type | Tickers | Status | Updated | Summary |\n"
    "|---|---|---|---|---|---|"
)


class KBPageError(ValueError):
    """Raised when a wiki page cannot be parsed as front matter + body."""


def today() -> str:
    return date.today().isoformat()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def parse_page(text: str, *, source: str = "<string>") -> tuple[dict, str]:
    """Split a page into (front-matter dict, body markdown)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        raise KBPageError(f"{source}: page does not start with '---' front matter")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FRONT_MATTER_DELIMITER:
            raw = "".join(lines[1:idx])
            body = "".join(lines[idx + 1:])
            try:
                meta = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise KBPageError(f"{source}: invalid front-matter YAML: {exc}") from exc
            if not isinstance(meta, dict):
                raise KBPageError(f"{source}: front matter is not a mapping")
            return meta, body
    raise KBPageError(f"{source}: unterminated front matter (missing closing '---')")


def parse_page_file(path: Path) -> tuple[dict, str]:
    return parse_page(path.read_text(encoding="utf-8"), source=str(path))


def serialize_page(meta: dict, body: str) -> str:
    """Render front matter (stable field order) + body back into page text."""
    ordered = {key: meta[key] for key in FIELD_ORDER if key in meta}
    ordered.update({key: value for key, value in meta.items() if key not in ordered})
    front = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=1000).strip()
    if not body.startswith("\n"):
        body = "\n" + body
    return f"{FRONT_MATTER_DELIMITER}\n{front}\n{FRONT_MATTER_DELIMITER}{body}"


def validate_meta(meta: dict, *, source: str = "<page>") -> list[str]:
    """Return a list of front-matter spec violations (empty when valid)."""
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in meta:
            errors.append(f"{source}: missing required field '{field}'")
    page_type = meta.get("type")
    if page_type is not None and page_type not in PAGE_TYPES:
        errors.append(f"{source}: unknown type '{page_type}'")
    status = meta.get("status")
    if page_type in STATUS_VALUES and status is not None:
        allowed = STATUS_VALUES[page_type]
        if status not in allowed:
            errors.append(
                f"{source}: status '{status}' not allowed for type '{page_type}' "
                f"(allowed: {', '.join(allowed)})"
            )
    tickers = meta.get("tickers")
    if tickers is not None:
        if not isinstance(tickers, list):
            errors.append(f"{source}: 'tickers' must be a list")
        else:
            for ticker in tickers:
                if not isinstance(ticker, str) or ticker != ticker.upper():
                    errors.append(f"{source}: ticker '{ticker}' must be an uppercase symbol")
    tags = meta.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            errors.append(f"{source}: 'tags' must be a list")
        else:
            for tag in tags:
                if not isinstance(tag, str) or not TAG_PATTERN.match(tag):
                    errors.append(f"{source}: tag '{tag}' must be kebab-case")
    for field in ("created", "updated"):
        value = meta.get(field)
        if value is None:
            continue
        text = value.isoformat() if isinstance(value, date) else str(value)
        if not DATE_PATTERN.match(text):
            errors.append(f"{source}: '{field}' must be YYYY-MM-DD, got '{value}'")
    summary = meta.get("summary")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()):
        errors.append(f"{source}: 'summary' must be a non-empty sentence")
    related = meta.get("related")
    if related is not None and not isinstance(related, list):
        errors.append(f"{source}: 'related' must be a list of paths")
    sources = meta.get("sources")
    if sources is not None:
        if not isinstance(sources, list):
            errors.append(f"{source}: 'sources' must be a list")
        else:
            for entry in sources:
                if not isinstance(entry, dict) or "title" not in entry or "ref" not in entry:
                    errors.append(f"{source}: each source needs 'title' and 'ref'")
    section_updated = meta.get("section_updated")
    if section_updated is not None:
        if not isinstance(section_updated, dict):
            errors.append(f"{source}: 'section_updated' must be a mapping of section-key to date")
        else:
            for key, value in section_updated.items():
                text = value.isoformat() if isinstance(value, date) else str(value)
                if not DATE_PATTERN.match(text):
                    errors.append(
                        f"{source}: section_updated['{key}'] must be YYYY-MM-DD, got '{value}'"
                    )
    return errors


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _date_text(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else _cell(value)


def index_row(meta: dict, link_path: str) -> str:
    """Render one index-table row for a page (link is index-relative POSIX path)."""
    tickers = ", ".join(meta.get("tickers") or [])
    return (
        f"| [{_cell(meta.get('title'))}]({link_path}) "
        f"| {_cell(meta.get('type'))} "
        f"| {_cell(tickers)} "
        f"| {_cell(meta.get('status'))} "
        f"| {_date_text(meta.get('updated'))} "
        f"| {_cell(meta.get('summary'))} |"
    )


def collect_index_pages(folder: Path) -> list[Path]:
    """Pages a folder's index.md covers: all *.md below it, skipping index.md
    files and any subtree that has its own index.md."""
    pages: list[Path] = []
    for path in sorted(folder.rglob("*.md")):
        if path.name == "index.md":
            continue
        parent = path.parent
        owned_elsewhere = False
        while parent != folder:
            if (parent / "index.md").exists():
                owned_elsewhere = True
                break
            parent = parent.parent
        if not owned_elsewhere:
            pages.append(path)
    return pages


def render_index_section(rows: list[str]) -> str:
    if not rows:
        return f"{INDEX_BEGIN}\n{INDEX_HEADER}\n{INDEX_END}"
    return f"{INDEX_BEGIN}\n{INDEX_HEADER}\n" + "\n".join(rows) + f"\n{INDEX_END}"


def replace_index_section(page_text: str, section: str, *, source: str = "<index>") -> str:
    begin = page_text.find(INDEX_BEGIN)
    end = page_text.find(INDEX_END)
    if begin == -1 or end == -1 or end < begin:
        raise KBPageError(f"{source}: missing kb-index begin/end markers")
    return page_text[:begin] + section + page_text[end + len(INDEX_END):]


def replace_section(body: str, header: str, content: str) -> str:
    """Replace a markdown section (## header) with new content.

    Finds the line starting with '## {header}', replaces everything from that
    line up to (but not including) the next '## ' line or EOF with the header
    plus the new content. Raises KBPageError if the header is not found.
    """
    lines = body.splitlines(keepends=True)
    header_prefix = f"## {header}"
    header_idx = None
    for idx, line in enumerate(lines):
        if line.strip().startswith(header_prefix):
            header_idx = idx
            break
    if header_idx is None:
        raise KBPageError(f"section '{header}' not found")

    # Find the next section header or EOF.
    next_section_idx = len(lines)
    for idx in range(header_idx + 1, len(lines)):
        if lines[idx].strip().startswith("## "):
            next_section_idx = idx
            break

    # Rebuild: keep everything before the section, insert new content, keep everything after.
    new_content = f"\n{header_prefix}\n\n{content}\n"
    if body.endswith("\n"):
        new_content = new_content.rstrip() + "\n"

    result = "".join(lines[:header_idx]) + new_content + "".join(lines[next_section_idx:])
    return result


STATUS_BLOCK_KEYS = {
    "Portfolio Status": "portfolio_status",
    "Portfolio Role": "portfolio_role",
    "Decision": "decision",
    "Confidence": "confidence",
    "Time Horizon": "time_horizon",
    "Last Updated": "last_updated",
}


def parse_status_block(body: str) -> dict:
    """Extract the '## Status' section's '- Key: value' lines as a dict.

    Keys whose line is absent from the page are omitted, not set to None.
    """
    result: dict[str, str] = {}
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Status"):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("## "):
            break
        for label, key in STATUS_BLOCK_KEYS.items():
            prefix = f"- {label}:"
            if stripped.startswith(prefix):
                result[key] = stripped[len(prefix):].strip()
    return result


def decision_history_rows(body: str) -> list[dict]:
    """Every '## Decision History' table row as {"date", "action", "verdict", "note"}.

    Header/separator rows are skipped naturally -- their first cell never
    matches DATE_PATTERN.
    """
    rows: list[dict] = []
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Decision History"):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("## "):
            break
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 4 and DATE_PATTERN.match(cells[0]):
                rows.append({"date": cells[0], "action": cells[1], "verdict": cells[2], "note": cells[3]})
    return rows


def last_decision_history_row(body: str) -> dict | None:
    rows = decision_history_rows(body)
    return rows[-1] if rows else None


def _sort_key(item: tuple[dict, str]) -> tuple[str, str]:
    meta, link = item
    updated = _date_text(meta.get("updated")) or "0000-00-00"
    return ("-" + updated, link)


def rebuild_index(index_path: Path, pages: list[Path] | None = None) -> list[str]:
    """Regenerate the marker-delimited table in an index.md from page front
    matter. Returns validation errors from skipped (unparseable) pages."""
    folder = index_path.parent
    if pages is None:
        pages = collect_index_pages(folder)
    errors: list[str] = []
    entries: list[tuple[dict, str]] = []
    for page in pages:
        try:
            meta, _ = parse_page_file(page)
        except KBPageError as exc:
            errors.append(str(exc))
            continue
        link = Path(os.path.relpath(page, folder)).as_posix()
        entries.append((meta, link))
    entries.sort(key=lambda item: (item[0].get("updated") is None, _sort_key(item)))
    rows = [index_row(meta, link) for meta, link in entries]
    text = index_path.read_text(encoding="utf-8")
    index_path.write_text(
        replace_index_section(text, render_index_section(rows), source=str(index_path)),
        encoding="utf-8",
    )
    return errors


# The wiki shares Knowledge-Base/ with the pre-existing classifier zone:
# ref/*.yaml plus the root README.md / CHANGELOG.md carry no front matter and
# are never treated as wiki pages.
NON_WIKI_DIRS = ("ref",)
NON_WIKI_FILES = ("README.md", "CHANGELOG.md")


def collect_wiki_pages(kb_root: Path, *, include_indexes: bool = False) -> list[Path]:
    """All research-wiki markdown pages, excluding the classifier zone."""
    pages: list[Path] = []
    for path in sorted(kb_root.rglob("*.md")):
        rel = path.relative_to(kb_root)
        if rel.parts[0] in NON_WIKI_DIRS:
            continue
        if len(rel.parts) == 1 and rel.name in NON_WIKI_FILES:
            continue
        if path.name == "index.md" and not include_indexes:
            continue
        pages.append(path)
    return pages


def rebuild_main_index(kb_root: Path, limit: int = 15) -> list[str]:
    """Refresh the recently-updated table in the wiki's main index.md."""
    pages = collect_wiki_pages(kb_root)
    entries: list[tuple[dict, Path]] = []
    errors: list[str] = []
    for page in pages:
        try:
            meta, _ = parse_page_file(page)
        except KBPageError as exc:
            errors.append(str(exc))
            continue
        if meta.get("type") in ("template", "log"):
            continue
        entries.append((meta, page))
    entries.sort(key=lambda item: _date_text(item[0].get("updated")) or "0000-00-00", reverse=True)
    index_path = kb_root / "index.md"
    rows = [
        index_row(meta, Path(os.path.relpath(page, kb_root)).as_posix())
        for meta, page in entries[:limit]
    ]
    text = index_path.read_text(encoding="utf-8")
    index_path.write_text(
        replace_index_section(text, render_index_section(rows), source=str(index_path)),
        encoding="utf-8",
    )
    return errors


def rebuild_thesis_views(kb_root: Path) -> list[str]:
    """Regenerate theses/<status>/index.md view tables from stocks/*.md front
    matter. Pages with status 'research' stay in stocks/index.md only."""
    stocks = sorted((kb_root / "stocks").glob("*.md"))
    by_status: dict[str, list[tuple[dict, Path]]] = {
        status: [] for status in ("active", "watchlist", "closed", "rejected")
    }
    errors: list[str] = []
    for page in stocks:
        if page.name == "index.md":
            continue
        try:
            meta, _ = parse_page_file(page)
        except KBPageError as exc:
            errors.append(str(exc))
            continue
        status = meta.get("status")
        if status in by_status:
            by_status[status].append((meta, page))
    for status, entries in by_status.items():
        index_path = kb_root / "theses" / status / "index.md"
        entries.sort(key=lambda item: _date_text(item[0].get("updated")) or "", reverse=True)
        rows = [
            index_row(meta, Path(os.path.relpath(page, index_path.parent)).as_posix())
            for meta, page in entries
        ]
        text = index_path.read_text(encoding="utf-8")
        index_path.write_text(
            replace_index_section(text, render_index_section(rows), source=str(index_path)),
            encoding="utf-8",
        )
    return errors


# Gated stock-page sections and their `section_updated` keys (decision #16).
# Weekly tier goes stale in 7 days (market-driven); monthly tier in 30
# (fundamentals/judgment). DB-backed sections (Financial Analysis, Earnings and
# Catalysts, Dividend Analysis) and event-driven sections (Original Thesis,
# Decision History, Sources) are deliberately absent -- they are never gated.
# `technical_analysis` joins the weekly tier only once that phase ships.
WEEKLY_SECTIONS: tuple[str, ...] = ("status", "market_sentiment", "options_activity")
MONTHLY_SECTIONS: tuple[str, ...] = (
    "valuation_analysis",
    "company_overview",
    "bull_case",
    "bear_case",
    "key_risks",
    "open_questions",
    "monitoring_checklist",
    "portfolio_fit",
    "updated_thesis",
    "analyst_view",
    "insider_activity",
)
GATED_SECTIONS: tuple[str, ...] = WEEKLY_SECTIONS + MONTHLY_SECTIONS

# Map a gated section's markdown `## header` to its `section_updated` key, so
# the writer can stamp the right key after rewriting a section. Only gated
# sections appear here; rewriting a non-gated section (e.g. Original Thesis,
# Financial Analysis) records nothing.
SECTION_KEY_BY_HEADER: dict[str, str] = {
    "Status": "status",
    "Market Sentiment": "market_sentiment",
    "Options Activity": "options_activity",
    "Valuation Analysis": "valuation_analysis",
    "Company Overview": "company_overview",
    "Bull Case": "bull_case",
    "Bear Case": "bear_case",
    "Key Risks": "key_risks",
    "Open Questions": "open_questions",
    "Monitoring Checklist": "monitoring_checklist",
    "Portfolio Fit": "portfolio_fit",
    "Updated Thesis": "updated_thesis",
    "Analyst View": "analyst_view",
    "Insider Activity": "insider_activity",
}


def touch_updated(meta: dict, on: str | None = None) -> dict:
    meta = dict(meta)
    meta["updated"] = on or today()
    return meta


def stamp_sections_updated(meta: dict, headers, *, on: str | None = None) -> dict:
    """Record that the given gated sections were rewritten today (decision #16).

    `headers` is an iterable of markdown section header names (e.g.
    "Market Sentiment"); only those in `SECTION_KEY_BY_HEADER` are stamped, so
    passing a non-gated header (Original Thesis, a DB-backed section) is a
    harmless no-op. Returns a copy with an updated `section_updated` map,
    merged onto any keys already present.
    """
    on = on or today()
    meta = dict(meta)
    section_map = dict(meta.get("section_updated") or {})
    for header in headers:
        key = SECTION_KEY_BY_HEADER.get(header)
        if key is not None:
            section_map[key] = on
    if section_map:
        meta["section_updated"] = section_map
    return meta


def append_log_row(log_path: Path, cells: list[str]) -> None:
    """Append one row to an append-only markdown log table."""
    text = log_path.read_text(encoding="utf-8")
    row = "| " + " | ".join(_cell(cell) for cell in cells) + " |"
    if not text.endswith("\n"):
        text += "\n"
    log_path.write_text(text + row + "\n", encoding="utf-8")


def append_update_log(kb_root: Path, action: str, page: str, note: str, *, on: str | None = None) -> None:
    """Record a research-wiki change in logs/update-log.md."""
    append_log_row(kb_root / "logs" / "update-log.md", [on or today(), action, page, note])
