"""Append one row to a research-wiki log. Append-only: existing rows are
never edited or removed. See references/thesis-contract.md for the verdict
vocabulary and per-log column contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402

VERDICTS = ("new", "stronger", "weaker", "unchanged", "broken")

LOG_FILES = {
    "decision": "decision-log.md",
    "research": "research-log.md",
    "update": "update-log.md",
}


class LogError(ValueError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a row to a research-wiki log.")
    parser.add_argument("--log", required=True, choices=sorted(LOG_FILES))
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--action", default=None)
    parser.add_argument("--verdict", default=None, choices=VERDICTS)
    parser.add_argument("--sources", default=None)
    parser.add_argument("--page", default=None)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()

    date = args.date or kb_pages.today()
    tickers = ", ".join(t.strip().upper() for t in args.tickers.split(",") if t.strip())

    try:
        if args.log == "decision":
            if not args.action or not args.verdict:
                raise LogError("--log decision requires --action and --verdict")
            cells = [date, tickers, args.action, args.verdict, args.note]
        elif args.log == "research":
            if not args.action:
                raise LogError("--log research requires --action")
            cells = [date, tickers, args.action, args.sources or "-", args.note]
        else:  # update
            if not args.action or not args.page:
                raise LogError("--log update requires --action and --page")
            cells = [date, args.action, args.page, args.note]
    except LogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    log_path = kb_pages.KB_ROOT / "logs" / LOG_FILES[args.log]
    kb_pages.append_log_row(log_path, cells)
    print(f"Appended to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
