"""Staleness gate for the KB population workflow.

Computes which owned tickers have at least one *due* stock-page section this
run, per decision #16's per-section cadence tiers. This is the first step the
`kb-orchestrator` agent runs (via Bash) before fanning out any prep/analyst
work -- it decides who gets processed and which sections of their page are
stale enough to rewrite.

This script is strictly read-only against `Knowledge-Base/`: it parses each
`stocks/<TICKER>.md` page's front matter and emits one versioned-JSON document
to stdout (`kb-staleness-gate.v1`). It never writes anything, ever -- so
`--dry-run` and a normal run compute and print identically; the flag exists to
make "I am only inspecting the gate, touching no KB state" explicit at the call
site (decision #16's testing requirement).

Section cadence tiers come straight from decision #16. Note the count: the
plan's prose says "11 gated sections", but decision #16's own tier lists
enumerate 3 weekly + 11 monthly = 14 currently-gated sections (the "11" in the
plan conflated the total with the monthly-tier count). Decision #16's explicit
lists are the source of truth, so 14 sections are gated here. `technical_analysis`
joins the weekly tier only once the technical-analysis phase ships -- it is a
placeholder section today, so it is deliberately not gated yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

# kb_pages (the canonical front-matter parser) lives in src/, exposed here the
# same way the classify-portfolio / bootstrap-stock-research skill scripts do it.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

import kb_pages  # noqa: E402

GATE_SCHEMA = "kb-staleness-gate.v1"

# Decision #16's cadence tiers. The section-key lists are the single source of
# truth in kb_pages (shared with the writer's section_updated bookkeeping);
# this script only owns the day thresholds.
WEEKLY_SECTIONS = kb_pages.WEEKLY_SECTIONS
MONTHLY_SECTIONS = kb_pages.MONTHLY_SECTIONS
GATED_SECTIONS = kb_pages.GATED_SECTIONS

WEEKLY_DAYS = 7
MONTHLY_DAYS = 30

DEFAULT_CLASSIFICATION_JSON = (
    Path(__file__).resolve().parents[4]
    / "exports"
    / "portfolio-classification"
    / "portfolio-classification.json"
)


def _cadence_days(section: str) -> int:
    return WEEKLY_DAYS if section in WEEKLY_SECTIONS else MONTHLY_DAYS


def _as_date(value: object) -> date | None:
    """Coerce a front-matter date value (date, datetime, or ISO string) to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _kb_root(kb_root: Path | str | None) -> Path:
    return Path(kb_root) if kb_root is not None else kb_pages.KB_ROOT


def _due_sections_for_page(page_path: Path, run_date: date) -> list[str]:
    """Return the gated sections of an existing stock page that are past cadence.

    Each section's last-updated date is `section_updated[<key>]` when present,
    else the page's top-level `updated:` (decision #16's backward-compatible
    fallback). An unparseable/absent date is treated as due (stale-or-unknown).
    """
    meta, _ = kb_pages.parse_page_file(page_path)
    section_updated = meta.get("section_updated") or {}
    if not isinstance(section_updated, dict):
        section_updated = {}
    top_level = _as_date(meta.get("updated"))

    due: list[str] = []
    for section in GATED_SECTIONS:
        last = _as_date(section_updated.get(section))
        if last is None:
            last = top_level
        if last is None or (run_date - last).days >= _cadence_days(section):
            due.append(section)
    return due


def compute_due_tickers(
    owned_tickers: Iterable[str],
    force: Iterable[str] | None = None,
    dry_run: bool = False,
    *,
    kb_root: Path | str | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Compute which owned tickers have at least one due section this run.

    - Missing `stocks/<TICKER>.md`  -> due, `first_run: true`, all sections due.
    - Existing page                 -> due only if some gated section is past
                                        its cadence (see `_due_sections_for_page`).
    - `force` (a subset of tickers) -> the run is scoped to exactly those
                                        tickers; each is fully due (every gated
                                        section), `first_run` set only when the
                                        page genuinely does not exist. Scoping to
                                        the named set is what makes a targeted
                                        2-3 ticker verification run small, rather
                                        than also dragging in every other stale
                                        ticker.

    `dry_run` does not change the computation -- this script never writes, so it
    is accepted for API symmetry and to document intent at the call site.
    """
    root = _kb_root(kb_root)
    run_date = run_date or date.today()
    owned = [str(t).strip().upper() for t in owned_tickers if str(t).strip()]
    forced = {str(t).strip().upper() for t in (force or []) if str(t).strip()}

    tickers_to_check = sorted(forced) if forced else owned

    due_tickers: list[dict[str, Any]] = []
    for ticker in tickers_to_check:
        page_path = root / "stocks" / f"{ticker}.md"
        exists = page_path.exists()
        if ticker in forced:
            due_sections = list(GATED_SECTIONS)
        elif not exists:
            due_sections = list(GATED_SECTIONS)
        else:
            due_sections = _due_sections_for_page(page_path, run_date)
            if not due_sections:
                continue
        due_tickers.append(
            {
                "ticker": ticker,
                "first_run": not exists,
                "due_sections": due_sections,
            }
        )

    due_names = {entry["ticker"] for entry in due_tickers}
    skipped_count = len([t for t in owned if t not in due_names])

    return {
        "schema": GATE_SCHEMA,
        "run_date": run_date.isoformat(),
        "due_tickers": due_tickers,
        "skipped_count": skipped_count,
    }


def load_owned_tickers(classification_json: Path | str = DEFAULT_CLASSIFICATION_JSON) -> list[str]:
    """Read owned pipeline ticker symbols from the classification JSON holdings.

    The `holdings[].ticker` values are the pipeline symbols and match the
    `stocks/<TICKER>.md` filenames exactly, so no provider-symbol mapping is
    needed here.
    """
    data = json.loads(Path(classification_json).read_text(encoding="utf-8"))
    holdings = data.get("holdings") or []
    return [str(h["ticker"]).strip().upper() for h in holdings if h.get("ticker")]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute which owned tickers have a due stock-page section this run."
    )
    parser.add_argument(
        "--force",
        nargs="*",
        default=None,
        metavar="TICKER",
        help="Scope the run to exactly these tickers, each fully due (bypasses the gate).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print without writing (this script never writes regardless).",
    )
    parser.add_argument(
        "--classification-json",
        type=Path,
        default=DEFAULT_CLASSIFICATION_JSON,
        help="Path to portfolio-classification.json (owned-ticker source).",
    )
    parser.add_argument(
        "--kb-root",
        type=Path,
        default=None,
        help="Override Knowledge-Base root (testing).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    owned = load_owned_tickers(args.classification_json)
    result = compute_due_tickers(
        owned,
        force=args.force,
        dry_run=args.dry_run,
        kb_root=args.kb_root,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
