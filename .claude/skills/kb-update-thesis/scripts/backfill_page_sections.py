"""One-time backfill for thesis pages whose Company Overview / Original Thesis
were left blank by an early recommendation ingest (the ingest workflow only seeds
those sections on page creation, and the recommendation artifact schema had no
`company_overview` field before rubric v1.2).

For each `stocks/TICKER.md` with an empty Company Overview, this fills:

  - Company Overview  <- `overview.longBusinessSummary` from the matching
    `exports/stock-recommendations/TICKER-<date>-research.json`, and
  - Original Thesis    <- the page's own current Updated Thesis text, but ONLY
    when Original Thesis is still empty (immutability: never overwrite an
    existing Original Thesis). Pages whose Updated Thesis is too thin to stand in
    are reported for manual attention instead.

Deterministic, no LLM. Idempotent: a page whose Company Overview already has
content is skipped. Every page touched gets one update-log row.

Usage:
    python backfill_page_sections.py [--research-dir exports/stock-recommendations]
                                     [--date 2026-07-14] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402
from config import EXPORT_FOLDER  # noqa: E402

DEFAULT_RESEARCH_DIR = EXPORT_FOLDER / "stock-recommendations"
# An Original Thesis shorter than this (after stripping) is treated as too thin
# to seed from Updated Thesis; the page is flagged for manual writing instead.
MIN_THESIS_CHARS = 80


def _section_text(body: str, header: str) -> str:
    """Return the text between `## {header}` and the next `## ` (or EOF), stripped."""
    lines = body.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == f"## {header}":
            start = idx + 1
            break
    if start is None:
        return ""
    collected = []
    for line in lines[start:]:
        if line.strip().startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _find_research(research_dir: Path, ticker: str, date: str | None) -> Path | None:
    if date:
        candidate = research_dir / f"{ticker}-{date}-research.json"
        if candidate.exists():
            return candidate
    matches = sorted(research_dir.glob(f"{ticker}-*-research.json"))
    return matches[-1] if matches else None


def _long_business_summary(research_path: Path) -> str:
    try:
        data = json.loads(research_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return ""
    overview = ((data.get("data") or {}).get("overview") or {})
    return str(overview.get("longBusinessSummary") or "").strip()


def backfill(
    kb_root: Path | None = None,
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    date: str | None = None,
    dry_run: bool = False,
) -> dict:
    kb_root = kb_root or kb_pages.KB_ROOT
    stocks_dir = kb_root / "stocks"
    today = kb_pages.today()
    updated: list[str] = []
    skipped: list[str] = []
    flagged: list[str] = []

    for page_path in sorted(stocks_dir.glob("*.md")):
        if page_path.name == "index.md":
            continue
        ticker = page_path.stem.upper()
        meta, body = kb_pages.parse_page_file(page_path)
        if meta.get("type") != "stock-page":
            continue

        if _section_text(body, "Company Overview"):
            skipped.append(ticker)
            continue

        changed = False

        research_path = _find_research(research_dir, ticker, date)
        summary = _long_business_summary(research_path) if research_path else ""
        if summary:
            body = kb_pages.replace_section(body, "Company Overview", summary)
            changed = True

        # Original Thesis: seed from Updated Thesis only if still empty (never
        # overwrite an existing Original Thesis) and only if substantive.
        if not _section_text(body, "Original Thesis"):
            updated_thesis = _section_text(body, "Updated Thesis")
            if len(updated_thesis) >= MIN_THESIS_CHARS:
                seed = (
                    f"{updated_thesis}\n\n"
                    f"_(Backfilled {today} from the then-current Updated Thesis; "
                    "preserved verbatim as the original reasoning.)_"
                )
                body = kb_pages.replace_section(body, "Original Thesis", seed)
                changed = True
            else:
                flagged.append(ticker)

        if not changed:
            flagged.append(ticker) if ticker not in flagged else None
            continue

        if dry_run:
            updated.append(ticker)
            continue

        meta = kb_pages.touch_updated(meta, on=today)
        page_path.write_text(kb_pages.serialize_page(meta, body), encoding="utf-8", newline="\n")
        kb_pages.append_update_log(
            kb_root, "backfilled", f"stocks/{ticker}.md",
            "Seeded Company Overview / Original Thesis from research + prior Updated Thesis.",
            on=today,
        )
        updated.append(ticker)

    return {"updated": updated, "skipped": skipped, "flagged": flagged}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill blank Company Overview / Original Thesis sections.")
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument("--date", default=None, help="Preferred research-JSON date suffix (e.g. 2026-07-14).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = backfill(research_dir=args.research_dir, date=args.date, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}Backfilled: {', '.join(result['updated']) or '(none)'}")
    print(f"Already had overview (skipped): {', '.join(result['skipped']) or '(none)'}")
    if result["flagged"]:
        print(f"Needs manual Company Overview / Original Thesis: {', '.join(result['flagged'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
