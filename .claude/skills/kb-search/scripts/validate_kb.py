"""Lint the research wiki: front-matter validity, index freshness, and
`related` link resolution. See references/search-contract.md for the rules
enforced here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402


def validate_front_matter(kb_root: Path) -> list[str]:
    errors: list[str] = []
    for page in kb_pages.collect_wiki_pages(kb_root, include_indexes=True):
        try:
            meta, _ = kb_pages.parse_page_file(page)
        except kb_pages.KBPageError as exc:
            errors.append(str(exc))
            continue
        rel = page.relative_to(kb_root).as_posix()
        errors.extend(kb_pages.validate_meta(meta, source=rel))
    return errors


def validate_related_links(kb_root: Path) -> list[str]:
    errors: list[str] = []
    for page in kb_pages.collect_wiki_pages(kb_root, include_indexes=True):
        try:
            meta, _ = kb_pages.parse_page_file(page)
        except kb_pages.KBPageError:
            continue
        for rel_link in meta.get("related") or []:
            target = (page.parent / rel_link).resolve()
            if not target.exists():
                errors.append(f"{page.relative_to(kb_root).as_posix()}: related link not found: {rel_link}")
    return errors


def find_index_files(kb_root: Path) -> list[Path]:
    return sorted(kb_root.rglob("index.md"))


# index.md files that are hand-curated navigation only (links to subsections,
# not a table of pages within their own folder) and so carry no
# kb-index markers -- never auto-rebuilt.
STATIC_INDEXES = ("theses/index.md", "logs/index.md", "taxonomy/index.md")


def fix_indexes(kb_root: Path) -> list[str]:
    errors: list[str] = []
    for index_path in find_index_files(kb_root):
        rel = index_path.relative_to(kb_root).as_posix()
        if index_path == kb_root / "index.md" or rel in STATIC_INDEXES:
            continue
        if index_path.parent.name in ("active", "watchlist", "closed", "rejected") and \
                index_path.parent.parent.name == "theses":
            continue  # rebuilt by rebuild_thesis_views below
        errors.extend(kb_pages.rebuild_index(index_path))
    errors.extend(kb_pages.rebuild_thesis_views(kb_root))
    errors.extend(kb_pages.rebuild_main_index(kb_root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the research wiki's front matter, indexes, and links.")
    parser.add_argument("--fix-indexes", action="store_true", help="Regenerate all index.md tables from front matter.")
    args = parser.parse_args()

    kb_root = kb_pages.KB_ROOT
    errors: list[str] = []

    if args.fix_indexes:
        errors.extend(fix_indexes(kb_root))

    errors.extend(validate_front_matter(kb_root))
    errors.extend(validate_related_links(kb_root))

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        print(f"\n{len(errors)} issue(s) found.", file=sys.stderr)
        return 1

    print("Knowledge base is valid: front matter, indexes, and links all check out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
