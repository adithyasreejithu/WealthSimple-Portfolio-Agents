"""Render a deterministic holdings-reconciliation markdown report.

Wraps the existing `holdings_reconciler.reconcile_holdings()` regression
harness and `analytics.get_holdings()`/`get_excluded_positions()` (which
carry `position_engine.py`'s `data_quality_flags`) to produce a report in
the same section structure as `docs/holdings_reconciliation_2026-07-07.md`.
No reconciliation math lives here -- see `references/report-contract.md`
for the fixed section list and the flag-to-root-cause mapping this module
implements.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from analytics import Holding, get_excluded_positions, get_holdings  # noqa: E402
from config import DATABASE_PATH  # noqa: E402
from holdings_reconciler import (  # noqa: E402
    ReconciliationResult,
    TickerMismatch,
    parse_holdings_csv,
    reconcile_holdings,
)

# Maps a `position_engine.py` data-quality flag to the root-cause tag and
# label used in `docs/holdings_reconciliation_2026-07-07.md`. Kept here
# rather than in `position_engine.py` because the RC vocabulary is a
# reporting concern specific to this skill, not part of the engine itself.
FLAG_ROOT_CAUSE: dict[str, tuple[str, str]] = {
    "split_without_quantity": ("RC1", "Stock split never applied"),
    "fx_stale": ("RC2b", "Currency/FX resolution issue (stale FX rate used)"),
    "fx_unavailable": ("RC2b", "Currency/FX resolution issue (no FX rate available)"),
    "buy_missing_cost": ("RC2c", "Missing cost basis on a buy"),
    "sell_missing_proceeds": ("RC3", "Missing proceeds on a sell"),
    "oversell_clamped": ("RC-EXCL", "Oversell clamped to zero rather than left negative"),
}
UNKNOWN_ROOT_CAUSE = ("RC-UNKNOWN", "Unexplained mismatch -- needs investigation")


def _root_causes_for(flags: tuple[str, ...], has_mismatch: bool) -> list[tuple[str, str]]:
    tags = [FLAG_ROOT_CAUSE[f] for f in flags if f in FLAG_ROOT_CAUSE]
    if has_mismatch and not tags:
        tags = [UNKNOWN_ROOT_CAUSE]
    return tags


def _fmt_qty(value: Decimal) -> str:
    return f"{value:.4f}"


def _fmt_money(value: Decimal) -> str:
    return f"{value:.2f}"


def _mismatches_by_ticker(result: ReconciliationResult) -> dict[str, list[TickerMismatch]]:
    grouped: dict[str, list[TickerMismatch]] = {}
    for mismatch in result.mismatches:
        grouped.setdefault(mismatch.ticker, []).append(mismatch)
    return grouped


def _flags_by_ticker(holdings: list[Holding], excluded: list[Holding]) -> dict[str, tuple[str, ...]]:
    flags: dict[str, tuple[str, ...]] = {}
    for holding in holdings:
        flags[holding.ticker_symbol] = holding.data_quality_flags
    for holding in excluded:
        flags.setdefault(holding.ticker_symbol, holding.data_quality_flags)
    return flags


def _render_header(report_path: Path, db_path: Path, generated_at: date) -> str:
    return (
        "# Holdings Reconciliation Report\n"
        f"**Date Generated:** {generated_at.isoformat()}  \n"
        f"**Report Period:** Holdings snapshot as of {generated_at.isoformat()}  \n"
        "**Data Sources:**\n"
        f"- **Ground Truth:** `{report_path}` (Wealthsimple official export)\n"
        f"- **Pipeline Computation:** `src/analytics.py::get_holdings()` reading `{db_path}`\n"
    )


def _render_executive_summary(
    compared_count: int, mismatched_count: int, db_only: list[str], csv_only: list[str]
) -> str:
    rate = (mismatched_count / compared_count * 100) if compared_count else 0.0
    lines = [
        "## Executive Summary\n",
        f"Comparison of {compared_count} ticker holdings reveals "
        f"**{mismatched_count} mismatches** ({rate:.0f}% mismatch rate).\n",
    ]
    if db_only:
        lines.append(f"- **DB-only tickers** (not in the ground-truth report): {', '.join(db_only)}\n")
    if csv_only:
        lines.append(f"- **Report-only tickers** (not in the DB): {', '.join(csv_only)}\n")
    if not mismatched_count and not db_only and not csv_only:
        lines.append("- Every ticker matched within tolerance; no data-quality issues observed.\n")
    return "".join(lines)


def _render_mismatch_table(
    tickers: list[str],
    holdings_by_ticker: dict[str, Holding],
    csv_rows: dict[str, dict[str, Decimal]],
    mismatches_by_ticker: dict[str, list[TickerMismatch]],
    flags_by_ticker: dict[str, tuple[str, ...]],
) -> str:
    lines = [
        "## Detailed Mismatch Table\n",
        "| Ticker | DB Qty | CSV Qty | DB Book Value (CAD) | CSV Book Value (CAD) | "
        "DB Unrealized (mkt) | CSV Unrealized (mkt) | Root Cause(s) |",
        "|--------|--------|---------|----------------------|------------------------|"
        "----------------------|------------------------|---------------|",
    ]
    for ticker in tickers:
        holding = holdings_by_ticker[ticker]
        csv_row = csv_rows[ticker]
        mismatches = {m.field: m for m in mismatches_by_ticker.get(ticker, [])}
        has_mismatch = bool(mismatches)

        def cell(field: str, db_value: Decimal, csv_value: Decimal, fmt) -> tuple[str, str]:
            if field in mismatches:
                return fmt(db_value), fmt(csv_value)
            return fmt(db_value), "✓"

        db_qty, csv_qty = cell("quantity", holding.quantity, csv_row["quantity"], _fmt_qty)
        db_book, csv_book = cell("book_value_cad", holding.cost_basis, csv_row["book_value_cad"], _fmt_money)
        db_unreal, csv_unreal = cell(
            "unrealized_mkt", holding.unrealized_mkt, csv_row["unrealized_mkt"], _fmt_money
        )

        tags = _root_causes_for(flags_by_ticker.get(ticker, ()), has_mismatch) if has_mismatch else []
        tag_text = ", ".join(tag for tag, _label in tags) if tags else "—"

        lines.append(
            f"| **{ticker}** | {db_qty} | {csv_qty} | {db_book} | {csv_book} | "
            f"{db_unreal} | {csv_unreal} | {tag_text} |"
        )
    return "\n".join(lines) + "\n"


def _render_legend(observed_tags: set[str]) -> str:
    if not observed_tags:
        return ""
    lines = ["## Legend\n"]
    seen_labels = {tag: label for tag, label in list(FLAG_ROOT_CAUSE.values()) + [UNKNOWN_ROOT_CAUSE]}
    for tag in sorted(observed_tags):
        lines.append(f"- **{tag}:** {seen_labels.get(tag, tag)}")
    return "\n".join(lines) + "\n"


def _render_excluded_positions(excluded: list[Holding]) -> str:
    if not excluded:
        return ""
    lines = ["## Excluded Positions\n", "Positions excluded from current-holdings analytics as a data error:\n"]
    for holding in excluded:
        flag_text = ", ".join(holding.data_quality_flags) if holding.data_quality_flags else "negative quantity"
        lines.append(f"- **{holding.ticker_symbol}:** quantity {_fmt_qty(holding.quantity)} ({flag_text})")
    return "\n".join(lines) + "\n"


def _render_root_cause_analysis(
    mismatches_by_ticker: dict[str, list[TickerMismatch]], flags_by_ticker: dict[str, tuple[str, ...]]
) -> tuple[str, set[str]]:
    by_tag: dict[str, list[str]] = {}
    for ticker in mismatches_by_ticker:
        for tag, _label in _root_causes_for(flags_by_ticker.get(ticker, ()), has_mismatch=True):
            by_tag.setdefault(tag, []).append(ticker)

    if not by_tag:
        return "", set()

    lines = ["## Root Cause Analysis\n"]
    seen_labels = {tag: label for tag, label in list(FLAG_ROOT_CAUSE.values()) + [UNKNOWN_ROOT_CAUSE]}
    for tag in sorted(by_tag):
        tickers = sorted(by_tag[tag])
        lines.append(f"### {tag}: {seen_labels.get(tag, tag)}\n")
        lines.append(f"Affected tickers: {', '.join(tickers)}\n")
    return "\n".join(lines) + "\n", set(by_tag)


_CORRECTIVE_ACTIONS = """## Corrective Actions

The average-cost, split, FX, and reconciliation logic this report checks
against already lives in `src/position_engine.py` (see
`docs/architecture/ingestion_and_reconciliation.md`). If a mismatch above
has a root-cause tag, consult that document's "Interpreting a
`reconcile-holdings` mismatch" section rather than re-deriving a fix here.
"""

_VERIFICATION_CHECKLIST = """## Verification Checklist

Re-run after any source-data change:

```bash
python src/app.py recompute-positions
python src/app.py reconcile-holdings --report <broker-holdings.csv>
```

Expected outcome: every ticker's quantity, CAD book value, and market-currency
unrealized P/L match within tolerance, and no ticker is excluded as a
negative/oversell position.
"""

_APPENDIX = """## Appendix: Data Quality Notes

- **Price data:** Uses the last stored `close` from `historical_records`,
  which may lag the CSV's intraday snapshot by hours (cents-level
  difference, acceptable within tolerance).
- **Activities table:** The authoritative source for corporate actions;
  trusted over statement `STKREORG` rows (see `position_engine.py`).
- **Email transactions:** Represent the most recent trades; included until
  reconciled with statements or activities (`reconciliation_status`).
- **Cash balance:** Uses the explicit balance where available, falling back
  to net cash flow otherwise.
"""


def build_report(report_path: str | Path, db_path: str | Path = DATABASE_PATH, generated_at: date | None = None) -> str:
    """Build the full markdown report as a string (no file I/O)."""
    generated_at = generated_at or date.today()
    report_path = Path(report_path)
    db_path = Path(db_path)

    result = reconcile_holdings(report_path, db_path)
    holdings = get_holdings(str(db_path))
    excluded = get_excluded_positions(str(db_path))
    csv_rows = parse_holdings_csv(report_path)

    holdings_by_ticker = {h.ticker_symbol: h for h in holdings}
    flags_by_ticker = _flags_by_ticker(holdings, excluded)
    mismatches_by_ticker = _mismatches_by_ticker(result)
    compared_tickers = sorted(set(holdings_by_ticker) & set(csv_rows))

    sections = [
        _render_header(report_path, db_path, generated_at),
        _render_executive_summary(
            len(compared_tickers), len(mismatches_by_ticker), result.db_only_tickers, result.csv_only_tickers
        ),
        _render_mismatch_table(
            compared_tickers, holdings_by_ticker, csv_rows, mismatches_by_ticker, flags_by_ticker
        ),
    ]

    root_cause_section, observed_tags = _render_root_cause_analysis(mismatches_by_ticker, flags_by_ticker)
    legend = _render_legend(observed_tags)
    if legend:
        sections.append(legend)

    excluded_section = _render_excluded_positions(excluded)
    if excluded_section:
        sections.append(excluded_section)

    if root_cause_section:
        sections.append(root_cause_section)

    sections.append(_CORRECTIVE_ACTIONS)
    sections.append(_VERIFICATION_CHECKLIST)
    sections.append(_APPENDIX)

    return "\n---\n\n".join(section.strip() + "\n" for section in sections)


def _default_output_path(generated_at: date) -> Path:
    return ROOT / "exports" / "holdings-reconciliation" / f"holdings_reconciliation_{generated_at.isoformat()}.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a holdings-reconciliation markdown report.")
    parser.add_argument("--report", required=True, help="Path to the Wealthsimple holdings CSV export.")
    parser.add_argument("--database", default=str(DATABASE_PATH), help="Path to the DuckDB database file.")
    parser.add_argument("--output", help="Output markdown path (defaults to docs/holdings_reconciliation_<date>.md).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = date.today()
    try:
        report = build_report(args.report, args.database, generated_at)
    except Exception as exc:  # noqa: BLE001
        print(f"holdings reconciliation report failed: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else _default_output_path(generated_at)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
