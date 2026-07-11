"""Deterministic edges of the thesis lifecycle: create a new stock page from
the template, validate a page's structure, and change a stock page's status
(updating the status-view indexes). The actual thesis comparison/writing
(what changed, whether the thesis is stronger or weaker) is agent judgment
performed with Read/Edit against the page these commands produce -- see
references/thesis-contract.md.
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

DEFAULT_CLASSIFICATION = EXPORT_FOLDER / "portfolio-classification" / "portfolio-classification.json"

REQUIRED_SECTIONS = (
    "## Status",
    "## Company Overview",
    "## Original Thesis",
    "## Updated Thesis",
    "## Financial Analysis",
    "## Valuation Analysis",
    "## Technical Analysis",
    "## Options Activity",
    "## Market Sentiment",
    "## Insider Activity",
    "## Earnings and Catalysts",
    "## Dividend Analysis",
    "## Portfolio Fit",
    "## Bull Case",
    "## Bear Case",
    "## Key Risks",
    "## Open Questions",
    "## Decision History",
    "## Monitoring Checklist",
    "## Sources",
)


class ThesisError(ValueError):
    pass


def _status_block(portfolio_status: str, role: str, today: str) -> str:
    return (
        "\n## Status\n\n"
        f"- Portfolio Status: {portfolio_status}\n"
        f"- Portfolio Role: {role}\n"
        "- Decision: Watchlist\n"
        "- Confidence: Low\n"
        "- Time Horizon: Medium-term\n"
        f"- Last Updated: {today}\n"
    )


def render_new_page(ticker: str, company_name: str, portfolio_status: str, role: str, today: str) -> str:
    meta = {
        "title": f"{company_name} ({ticker}) — Stock Page" if company_name else f"{ticker} — Stock Page",
        "type": "stock-page",
        "tickers": [ticker],
        "tags": [],
        "status": portfolio_status,
        "created": today,
        "updated": today,
        "summary": f"Research page for {ticker}, created {today}.",
        "related": [],
        "sources": [],
    }
    body = f"\n# {ticker} — {company_name or 'Company Name'}\n"
    body += _status_block(portfolio_status, role, today)
    for section in REQUIRED_SECTIONS[1:]:
        if section == "## Decision History":
            body += f"\n{section}\n\n| Date | Action | Verdict | Note |\n|---|---|---|---|\n"
        elif section == "## Monitoring Checklist":
            body += f"\n{section}\n\n- [ ] \n"
        else:
            body += f"\n{section}\n\n"
    return kb_pages.serialize_page(meta, body)


def lookup_holding(ticker: str, classification_path: Path) -> dict | None:
    if not classification_path.exists():
        return None
    data = json.loads(classification_path.read_text(encoding="utf-8"))
    for holding in data.get("holdings", []):
        if holding["ticker"] == ticker:
            return holding
    return None


def cmd_create(args) -> int:
    ticker = args.ticker.upper()
    kb_root = kb_pages.KB_ROOT
    page_path = kb_root / "stocks" / f"{ticker}.md"
    if page_path.exists():
        raise ThesisError(f"stock page already exists: {page_path.relative_to(kb_root)} (use set-status/update instead)")

    holding = lookup_holding(ticker, args.classification)
    company_name = args.company_name or (holding["company_name"] if holding else "")
    role = holding["primary_group"] if holding else "Unassigned"

    today = kb_pages.today()
    page_path.write_text(
        render_new_page(ticker, company_name, args.status, role, today), encoding="utf-8", newline="\n"
    )

    kb_pages.rebuild_index(kb_root / "stocks" / "index.md")
    kb_pages.rebuild_thesis_views(kb_root)
    kb_pages.rebuild_main_index(kb_root)
    kb_pages.append_update_log(kb_root, "created", f"stocks/{ticker}.md", f"New stock page, status={args.status}.", on=today)

    print(f"Created: {page_path}")
    return 0


def cmd_validate(args) -> int:
    path = args.path.resolve()
    try:
        meta, body = kb_pages.parse_page_file(path)
    except kb_pages.KBPageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = kb_pages.validate_meta(meta, source=str(path))
    if meta.get("type") != "stock-page":
        errors.append(f"{path}: expected type 'stock-page', got '{meta.get('type')}'")
    for section in REQUIRED_SECTIONS:
        if section not in body:
            errors.append(f"{path}: missing required section '{section}'")

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(f"Valid: {path}")
    return 0


def cmd_set_status(args) -> int:
    ticker = args.ticker.upper()
    kb_root = kb_pages.KB_ROOT
    page_path = kb_root / "stocks" / f"{ticker}.md"
    if not page_path.exists():
        raise ThesisError(f"no stock page for {ticker}: {page_path.relative_to(kb_root)}")

    meta, body = kb_pages.parse_page_file(page_path)
    old_status = meta.get("status")
    today = kb_pages.today()
    meta = kb_pages.touch_updated(meta, on=today)
    meta["status"] = args.status

    if "- Portfolio Status:" in body:
        lines = body.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().startswith("- Portfolio Status:"):
                lines[idx] = f"- Portfolio Status: {args.status}"
            elif line.strip().startswith("- Last Updated:"):
                lines[idx] = f"- Last Updated: {today}"
        body = "\n".join(lines) + ("\n" if not body.endswith("\n") else "")

    page_path.write_text(kb_pages.serialize_page(meta, body), encoding="utf-8", newline="\n")

    kb_pages.rebuild_thesis_views(kb_root)
    kb_pages.rebuild_main_index(kb_root)
    kb_pages.append_update_log(
        kb_root, "status-changed", f"stocks/{ticker}.md", f"{old_status} -> {args.status}", on=today
    )
    print(f"Updated: {page_path} ({old_status} -> {args.status})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage stock thesis page lifecycle.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("--ticker", required=True)
    p_create.add_argument("--status", required=True, choices=kb_pages.STOCK_STATUSES)
    p_create.add_argument("--company-name", dest="company_name", default=None)
    p_create.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    p_create.set_defaults(func=cmd_create)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--path", type=Path, required=True)
    p_validate.set_defaults(func=cmd_validate)

    p_set_status = sub.add_parser("set-status")
    p_set_status.add_argument("--ticker", required=True)
    p_set_status.add_argument("--status", required=True, choices=kb_pages.STOCK_STATUSES)
    p_set_status.set_defaults(func=cmd_set_status)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ThesisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
