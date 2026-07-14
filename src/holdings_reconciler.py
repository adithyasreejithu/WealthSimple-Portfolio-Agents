"""Compare computed holdings against a Wealthsimple holdings CSV export.

Acceptance/regression tool for the position engine (see
`docs/architecture/ingestion_and_reconciliation.md`): the broker's own
holdings snapshot is treated as ground truth. This is the harness
`docs/holdings_reconciliation_2026-07-07.md` was built against, wired up as a
reusable CLI command (`python src/app.py reconcile-holdings`) instead of a
one-off script.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from analytics import get_holdings
from config import DATABASE_PATH

# Quantities from a broker export are exact share counts, so no relative
# slack is given. Book value and market-currency unrealized P/L get a small
# relative allowance on top of a fixed floor to absorb price-snapshot lag
# between the CSV's intraday price and the DB's last stored close (see
# docs/holdings_reconciliation_2026-07-07.md's "Data Quality Notes").
QUANTITY_ABS_TOLERANCE = Decimal("0.000001")
BOOK_VALUE_ABS_TOLERANCE = Decimal("0.05")
BOOK_VALUE_REL_TOLERANCE = Decimal("0.001")
UNREALIZED_ABS_TOLERANCE = Decimal("2")
UNREALIZED_REL_TOLERANCE = Decimal("0.015")


@dataclass(frozen=True)
class TickerMismatch:
    ticker: str
    field: str
    db_value: Decimal
    csv_value: Decimal
    detail: str


@dataclass(frozen=True)
class ReconciliationResult:
    mismatches: list[TickerMismatch]
    db_only_tickers: list[str]
    csv_only_tickers: list[str]

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.db_only_tickers and not self.csv_only_tickers


def _decimal(value: str) -> Decimal:
    return Decimal(value.strip())


def parse_holdings_csv(path: str | Path) -> dict[str, dict[str, Decimal]]:
    """Parse a Wealthsimple holdings export into ``{symbol: {field: value}}``.

    Expects the columns from a Wealthsimple "holdings report" CSV (see
    `ref/holdings-report-2026-07-07.csv` for a real example): `Symbol`,
    `Quantity`, `Book Value (CAD)`, `Market Unrealized Returns`. Trailing
    blank rows and an "As of ..." footer line (present in real exports) are
    skipped rather than treated as a parse error.
    """
    rows: dict[str, dict[str, Decimal]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol:
                continue
            try:
                rows[symbol] = {
                    "quantity": _decimal(row["Quantity"]),
                    "book_value_cad": _decimal(row["Book Value (CAD)"]),
                    "unrealized_mkt": _decimal(row["Market Unrealized Returns"]),
                }
            except (KeyError, InvalidOperation):
                continue
    return rows


def _within(actual: Decimal, expected: Decimal, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    tolerance = max(abs_tol, rel_tol * abs(expected))
    return abs(actual - expected) <= tolerance


def reconcile_holdings(
    report_path: str | Path,
    db_path: str | Path = DATABASE_PATH,
) -> ReconciliationResult:
    """Compare ``get_holdings(db_path)`` against a broker holdings CSV export."""
    csv_rows = parse_holdings_csv(report_path)
    db_holdings = {h.ticker_symbol: h for h in get_holdings(db_path)}

    mismatches: list[TickerMismatch] = []
    db_only = sorted(set(db_holdings) - set(csv_rows))
    csv_only = sorted(set(csv_rows) - set(db_holdings))

    checks = (
        ("quantity", QUANTITY_ABS_TOLERANCE, Decimal("0")),
        ("book_value_cad", BOOK_VALUE_ABS_TOLERANCE, BOOK_VALUE_REL_TOLERANCE),
        ("unrealized_mkt", UNREALIZED_ABS_TOLERANCE, UNREALIZED_REL_TOLERANCE),
    )
    field_source = {
        "quantity": lambda h: h.quantity,
        "book_value_cad": lambda h: h.cost_basis,
        "unrealized_mkt": lambda h: h.unrealized_mkt,
    }

    for symbol in sorted(set(db_holdings) & set(csv_rows)):
        holding = db_holdings[symbol]
        csv_row = csv_rows[symbol]
        for field, abs_tol, rel_tol in checks:
            db_value = field_source[field](holding)
            csv_value = csv_row[field]
            if not _within(db_value, csv_value, abs_tol, rel_tol):
                mismatches.append(
                    TickerMismatch(
                        symbol, field, db_value, csv_value,
                        f"DB {db_value} vs CSV {csv_value}",
                    )
                )

    return ReconciliationResult(mismatches=mismatches, db_only_tickers=db_only, csv_only_tickers=csv_only)


def format_report(result: ReconciliationResult) -> str:
    if result.ok:
        return "reconcile-holdings: OK - all tickers match within tolerance."

    lines = ["reconcile-holdings: MISMATCH"]
    for mismatch in result.mismatches:
        lines.append(f"  [{mismatch.ticker}] {mismatch.field}: {mismatch.detail}")
    if result.db_only_tickers:
        lines.append(f"  DB-only tickers (not in report): {', '.join(result.db_only_tickers)}")
    if result.csv_only_tickers:
        lines.append(f"  Report-only tickers (not in DB): {', '.join(result.csv_only_tickers)}")
    return "\n".join(lines)
