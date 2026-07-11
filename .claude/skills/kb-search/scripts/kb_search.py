"""Front-matter-aware deterministic search over the research wiki.

Combines all supplied filters with AND. Front-matter field matches rank
above body-text matches. Read-only: never writes to the wiki. See
references/search-contract.md for the exact ranking and output rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402


def _matches_filters(meta: dict, ticker: str | None, ptype: str | None, tag: str | None, status: str | None) -> bool:
    if ticker and ticker.upper() not in (meta.get("tickers") or []):
        return False
    if ptype and meta.get("type") != ptype:
        return False
    if tag and tag.lower() not in [t.lower() for t in (meta.get("tags") or [])]:
        return False
    if status and meta.get("status") != status:
        return False
    return True


def _front_matter_text_hit(meta: dict, query: str) -> bool:
    haystacks = [
        str(meta.get("title", "")),
        str(meta.get("summary", "")),
        " ".join(meta.get("tickers") or []),
        " ".join(meta.get("tags") or []),
    ]
    return any(query in text.lower() for text in haystacks)


def _front_matter_line_count(page_text: str) -> int:
    lines = page_text.splitlines()
    for idx in range(1, len(lines)):
        if lines[idx].strip() == kb_pages.FRONT_MATTER_DELIMITER:
            return idx + 1  # opening '---' through closing '---', 1-indexed line count
    return 0


def _body_matches(body: str, query: str, front_matter_lines: int) -> list[tuple[int, str]]:
    hits = []
    for offset, line in enumerate(body.splitlines(), start=1):
        if query in line.lower():
            hits.append((offset + front_matter_lines, line.strip()))
    return hits


def search(kb_root: Path, *, query: str | None, ticker: str | None, ptype: str | None,
           tag: str | None, status: str | None, limit: int) -> list[dict]:
    query_lc = query.lower() if query else None
    results: list[dict] = []
    for page in kb_pages.collect_wiki_pages(kb_root, include_indexes=False):
        page_text = page.read_text(encoding="utf-8")
        try:
            meta, body = kb_pages.parse_page(page_text, source=str(page))
        except kb_pages.KBPageError:
            continue
        if not _matches_filters(meta, ticker, ptype, tag, status):
            continue

        front_hit = _front_matter_text_hit(meta, query_lc) if query_lc else False
        front_matter_line_count = _front_matter_line_count(page_text)
        body_hits = _body_matches(body, query_lc, front_matter_line_count) if query_lc else []

        if query_lc and not front_hit and not body_hits:
            continue

        rel_path = page.relative_to(kb_root).as_posix()
        results.append(
            {
                "path": rel_path,
                "title": meta.get("title"),
                "type": meta.get("type"),
                "tickers": meta.get("tickers") or [],
                "status": meta.get("status"),
                "updated": str(meta.get("updated")),
                "summary": meta.get("summary"),
                "front_matter_hit": front_hit,
                "matched_lines": [{"line": ln, "text": text} for ln, text in body_hits[:5]],
            }
        )

    results.sort(key=lambda r: (not r["front_matter_hit"], -len(r["matched_lines"]), r["path"]))
    return results[:limit]


def render_text(results: list[dict]) -> str:
    if not results:
        return "No matches."
    lines = []
    for r in results:
        tickers = ", ".join(r["tickers"]) or "-"
        lines.append(f"{r['path']}  [{r['type']}] tickers={tickers} status={r['status']} updated={r['updated']}")
        lines.append(f"  {r['title']}")
        lines.append(f"  {r['summary']}")
        for hit in r["matched_lines"]:
            lines.append(f"  L{hit['line']}: {hit['text']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the research wiki by front matter and body text.")
    parser.add_argument("--query", default=None)
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--type", dest="ptype", default=None, choices=kb_pages.PAGE_TYPES)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = search(
        kb_pages.KB_ROOT,
        query=args.query,
        ticker=args.ticker,
        ptype=args.ptype,
        tag=args.tag,
        status=args.status,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps({"count": len(results), "results": results}, indent=2))
    else:
        print(render_text(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
