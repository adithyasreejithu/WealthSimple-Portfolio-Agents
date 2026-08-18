"""Average-cost position engine built on top of `v_trade_events`.

Reads the unified BUY/SELL/SPLIT event stream produced by the
`v_trade_events` view (see `database.py`), walks it chronologically per
ticker, and materializes two tables:

- `position_ledger`: one row per event with running quantity/book value, an
  audit trail that also backs historical portfolio value reconstruction.
- `position_snapshots`: the final per-ticker state, read by every holdings
  consumer (analytics, historical values, the classify-portfolio skill).

This is the single place average-cost book value is computed; nothing else
in the codebase should re-derive it. See
`docs/architecture/ingestion_and_reconciliation.md` for the full design.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import DATABASE_PATH, FX_PAIR_SYMBOL, RECON_DATE_WINDOW_DAYS_STMT
from database import get_shared_connection
from system_logger import get_logger

logger = get_logger(__name__)

FINGERPRINT_COMPONENT = "position_engine"
# Bump whenever the engine's math changes: the fingerprint otherwise hashes
# only source-table state, so stored snapshots would keep serving numbers
# computed by the old code.
_ENGINE_VERSION = "2"
_FX_MAX_LAG_DAYS = 5
_Q4 = Decimal("0.0001")
_Q8 = Decimal("0.00000001")

# Every column read here must change whenever a source row that feeds
# v_trade_events changes shape (new row, or a reconciliation pass updates a
# link column) so a stale snapshot is always detected.
FINGERPRINT_SQL = """
SELECT
    (SELECT COUNT(*) FROM transactions) AS txn_count,
    (SELECT COALESCE(MAX(transaction_id), 0) FROM transactions) AS txn_max_id,
    (SELECT COUNT(*) FILTER (WHERE superseded_by_activity_id IS NOT NULL) FROM transactions) AS txn_superseded,
    (SELECT COUNT(*) FROM activities) AS activity_count,
    (SELECT COALESCE(MAX(activity_id), 0) FROM activities) AS activity_max_id,
    (SELECT COUNT(*) FROM email_transactions) AS email_count,
    (SELECT COALESCE(MAX(email_transaction_id), 0) FROM email_transactions) AS email_max_id,
    (SELECT COUNT(*) FILTER (WHERE reconciliation_status = 'provisional') FROM email_transactions) AS email_provisional,
    (SELECT COUNT(*) FILTER (WHERE matched_activity_id IS NOT NULL) FROM email_transactions) AS email_matched_activity,
    (SELECT COUNT(*) FROM historical_records) AS price_count
"""


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum)


@dataclass
class _TickerState:
    quantity: Decimal = Decimal("0")
    book_cad: Decimal = Decimal("0")
    book_mkt: Decimal = Decimal("0")
    realized_cad: Decimal = Decimal("0")
    provisional_quantity: Decimal = Decimal("0")
    flags: set[str] = field(default_factory=set)


def compute_fingerprint(connection: Any) -> str:
    """Hash the state of every source table the position engine reads."""
    row = connection.execute(FINGERPRINT_SQL).fetchone()
    return hashlib.sha256(json.dumps([_ENGINE_VERSION, row], default=str).encode("utf-8")).hexdigest()


def _stored_fingerprint(connection: Any) -> str | None:
    row = connection.execute(
        "SELECT ledger_fingerprint FROM position_engine_meta WHERE component = ?",
        [FINGERPRINT_COMPONENT],
    ).fetchone()
    return row[0] if row else None


def is_stale(connection: Any) -> bool:
    """Return whether `position_snapshots` no longer reflects the source tables."""
    return _stored_fingerprint(connection) != compute_fingerprint(connection)


def ensure_positions_fresh(connection: Any) -> bool:
    """Recompute positions if source tables changed since the last run.

    Returns True if a recompute happened. Safe to call on every read path;
    the fingerprint check is a handful of COUNT/MAX queries.
    """
    if is_stale(connection):
        recompute_positions(connection)
        return True
    return False


def _build_fx_series(connection: Any) -> dict[date, Decimal]:
    """Load CAD-per-USD closes for the configured FX pair, if ingested."""
    rows = connection.execute(
        """
        SELECT h.record_date, h.close
        FROM historical_records h
        JOIN tickers t ON t.ticker_id = h.ticker_id
        WHERE t.ticker_symbol = ?
        ORDER BY h.record_date
        """,
        [FX_PAIR_SYMBOL],
    ).fetchall()
    return {row[0]: Decimal(str(row[1])) for row in rows}


def latest_fx_rate(connection: Any, currency: str) -> tuple[Decimal, str | None]:
    """Return (CAD per unit of `currency` as of today, data-quality flag or None).

    Used to value current market prices in CAD (unlike `_resolve_fx`, which
    resolves the rate as of a historical event date). Same fallback chain:
    the configured FX pair's most recent close, then the most recent
    `transactions.fx_rate` seen anywhere, then 1.0.
    """
    if currency == "CAD":
        return Decimal("1"), None
    series = _build_fx_series(connection)
    if series:
        most_recent = max(series)
        return series[most_recent], None
    row = connection.execute(
        "SELECT fx_rate FROM transactions WHERE fx_rate IS NOT NULL AND fx_rate > 0 "
        "ORDER BY transaction_date DESC LIMIT 1"
    ).fetchone()
    if row:
        return Decimal(str(row[0])), "fx_stale"
    return Decimal("1"), "fx_unavailable"


def read_live_position_values(connection: Any, ticker_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Stored position state joined to the latest ingested close per ticker.

    Plain `SELECT`s only -- safe on a read-only connection. This is the one
    place "what is this position worth right now" is queried; both the
    write path (`analytics.py::_get_net_positions`, after
    `ensure_positions_fresh` self-heals `position_snapshots`) and read-only
    callers (e.g. investment-analyst-resources, which cannot take the write
    lock to self-heal) share it, so there is exactly one query for the
    underlying facts even though the two paths differ in whether they
    refresh `position_snapshots` first.
    """
    filter_clause = ""
    params: list[Any] = []
    if ticker_ids is not None:
        placeholders = ",".join("?" for _ in ticker_ids)
        filter_clause = f"AND s.ticker_id IN ({placeholders})"
        params = list(ticker_ids)
    rows = connection.execute(
        f"""
        WITH latest_prices AS (
            SELECT DISTINCT ON (ticker_id)
                ticker_id,
                record_date,
                close
            FROM historical_records
            ORDER BY ticker_id, record_date DESC
        )
        SELECT
            t.ticker_id,
            t.ticker_symbol,
            t.exchange,
            t.security_name,
            t.security_type,
            t.currency,
            s.quantity,
            s.book_value_cad,
            s.book_value_mkt,
            s.provisional_quantity,
            s.realized_gain_cad,
            s.data_quality_flags,
            lp.record_date,
            lp.close
        FROM position_snapshots s
        JOIN tickers t ON t.ticker_id = s.ticker_id
        LEFT JOIN latest_prices lp ON lp.ticker_id = s.ticker_id
        WHERE (s.quantity <> 0 OR s.data_quality_flags IS NOT NULL) {filter_clause}
        ORDER BY t.ticker_symbol, t.exchange
        """,
        params,
    ).fetchall()
    columns = [
        "ticker_id", "ticker_symbol", "exchange", "security_name", "security_type", "currency",
        "quantity", "book_value_cad", "book_value_mkt", "provisional_quantity", "realized_gain_cad",
        "data_quality_flags", "last_price_date", "last_price",
    ]
    return [dict(zip(columns, row)) for row in rows]


def _fx_on_or_before(series: dict[date, Decimal], event_date: date) -> Decimal | None:
    for lag in range(_FX_MAX_LAG_DAYS + 1):
        candidate = series.get(event_date - timedelta(days=lag))
        if candidate is not None:
            return candidate
    return None


def _resolve_fx(
    currency: str,
    event_fx_rate: Decimal | None,
    event_date: date,
    fx_series: dict[date, Decimal],
    latest_txn_fx: Decimal | None,
) -> tuple[Decimal, str | None]:
    """Return (CAD per unit of `currency`, data-quality flag or None).

    Resolution order: the event's own recorded fx_rate (statements carry
    this directly); the configured FX pair's historical close on or shortly
    before the event date; the most recent fx_rate seen anywhere in
    `transactions` (flagged `fx_stale`, since it is not dated to this event);
    finally 1.0 (flagged `fx_unavailable`) so valuation never raises.
    """
    if currency == "CAD":
        return Decimal("1"), None
    if event_fx_rate is not None and event_fx_rate > 0:
        return Decimal(str(event_fx_rate)), None
    from_series = _fx_on_or_before(fx_series, event_date)
    if from_series is not None:
        return from_series, None
    if latest_txn_fx is not None:
        return latest_txn_fx, "fx_stale"
    return Decimal("1"), "fx_unavailable"


def _detect_unresolved_splits(connection: Any) -> set[int]:
    """Return ticker_ids with a statement STKREORG row lacking a matching
    activities CorporateAction quantity within the statement reconciliation
    window (`config.RECON_DATE_WINDOW_DAYS_STMT`).

    `v_trade_events` silently drops STKREORG rows (they carry no quantity),
    so a split recorded only in a statement -- never imported via an
    activities export -- would otherwise vanish without a trace instead of
    corrupting the running share count. Flagged as `split_without_quantity`.
    """
    rows = connection.execute(
        """
        SELECT DISTINCT tr.ticker_id
        FROM transactions tr
        WHERE UPPER(tr.transaction_type) = 'STKREORG'
          AND NOT EXISTS (
              SELECT 1 FROM activities a
              WHERE a.ticker_id = tr.ticker_id
                AND a.activity_type = 'CorporateAction'
                AND a.quantity IS NOT NULL
                AND ABS(DATE_DIFF('day', a.transaction_date, tr.transaction_date)) <= ?
          )
        """,
        [RECON_DATE_WINDOW_DAYS_STMT],
    ).fetchall()
    return {int(row[0]) for row in rows}


def _load_events(connection: Any) -> list[tuple]:
    return connection.execute(
        """
        SELECT v.ticker_id, t.currency, v.event_date, v.event_type, v.source,
               v.source_id, v.quantity, v.amount_cad, v.amount_currency, v.fx_rate,
               v.amount_quality
        FROM v_trade_events v
        JOIN tickers t ON t.ticker_id = v.ticker_id
        ORDER BY v.ticker_id, v.event_date, v.source_priority, v.source_id
        """
    ).fetchall()


def _apply_buy(
    state: _TickerState,
    quantity: Decimal,
    amount_cad: Decimal | None,
    amount_quality: str | None,
    fx_rate: Decimal,
    source: str,
) -> Decimal:
    if amount_cad is None:
        state.flags.add("buy_missing_cost")
        cost_cad = Decimal("0")
    else:
        if amount_quality and amount_quality != "reported":
            state.flags.add("buy_cost_estimated")
        cost_cad = amount_cad
    state.quantity += quantity
    state.book_cad += cost_cad
    state.book_mkt += cost_cad / fx_rate
    if source == "email":
        state.provisional_quantity += quantity
    return cost_cad


def _apply_sell(
    state: _TickerState,
    quantity: Decimal,
    amount_cad: Decimal | None,
    amount_quality: str | None,
    ticker_id: int,
    source: str,
) -> Decimal:
    sold = quantity
    oversold = quantity - state.quantity if quantity > state.quantity else Decimal("0")
    if oversold > 0:
        state.flags.add("oversell_clamped")
        sold = state.quantity
    avg_cad = (state.book_cad / state.quantity) if state.quantity > 0 else Decimal("0")
    avg_mkt = (state.book_mkt / state.quantity) if state.quantity > 0 else Decimal("0")
    if amount_cad is None:
        state.flags.add("sell_missing_proceeds")
        proceeds_cad = sold * avg_cad
    else:
        if amount_quality and amount_quality != "reported":
            state.flags.add("sell_proceeds_estimated")
        # The reported/derived amount covers the full confirmed `quantity`,
        # but only `sold` shares are actually leaving this position's book on
        # an oversell -- prorate proceeds to `sold` for realized gain so the
        # unmatched shares' proceeds don't get counted as pure gain against
        # zero cost. Cash (analytics.get_cash_summary) still rolls forward
        # the full reported amount separately; only this ledger/realized-gain
        # figure is prorated.
        proceeds_cad = amount_cad if oversold == 0 else amount_cad * (sold / quantity)
        if oversold > 0:
            logger.warning(
                "Oversell excluded from realized gain | ticker_id=%s | source=%s | "
                "unmatched_quantity=%s | unmatched_proceeds_cad=%s",
                ticker_id, source, oversold, amount_cad - proceeds_cad,
            )
    state.realized_cad += proceeds_cad - sold * avg_cad
    state.book_cad -= sold * avg_cad
    state.book_mkt -= sold * avg_mkt
    state.quantity -= sold
    if source == "email":
        state.provisional_quantity -= sold
    return proceeds_cad


def recompute_positions(connection: Any = None, db_path: str | Path = DATABASE_PATH) -> int:
    """Rebuild `position_ledger` and `position_snapshots` from `v_trade_events`.

    Always a full rebuild rather than an incremental update: at the scale of
    a personal portfolio's transaction history this is fast, and it avoids
    an entire class of incremental-update bugs when a reconciliation pass
    changes which source row an event comes from. Returns the number of
    tickers with a position (including zero/negative ones, which downstream
    holdings code is responsible for filtering).

    Both reconciliation passes run first, unconditionally: the engine's
    correctness depends on the dedup link columns (`superseded_by_activity_id`,
    `matched_activity_id`/`matched_transaction_id`) being up to date, and a
    database whose rows predate those passes (e.g. one migrated from an older
    schema) would otherwise double-count every trade that appears in both
    `activities` and `transactions`. The passes are idempotent
    clear-and-rebuild, so rerunning them here is always safe.
    """
    connection = connection if connection is not None else get_shared_connection(db_path)

    # Imported lazily so read-only consumers of this module (the
    # classification skill script imports compute_fingerprint) don't pull in
    # database_command's heavyweight dependencies.
    from database_command import reconcile_email_transactions, reconcile_statement_activities

    reconcile_statement_activities(connection=connection)
    reconcile_email_transactions(connection=connection)

    fx_series = _build_fx_series(connection)
    latest_txn_fx_row = connection.execute(
        "SELECT fx_rate FROM transactions WHERE fx_rate IS NOT NULL AND fx_rate > 0 "
        "ORDER BY transaction_date DESC LIMIT 1"
    ).fetchone()
    latest_txn_fx = Decimal(str(latest_txn_fx_row[0])) if latest_txn_fx_row else None
    unresolved_split_tickers = _detect_unresolved_splits(connection)

    events = _load_events(connection)

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("DELETE FROM position_ledger")
        connection.execute("DELETE FROM position_snapshots")

        states: dict[int, _TickerState] = {}
        for (
            ticker_id, currency, event_date, event_type, source, source_id,
            raw_quantity, raw_amount_cad, raw_amount_currency, raw_fx_rate,
            amount_quality,
        ) in events:
            state = states.setdefault(ticker_id, _TickerState())
            quantity = Decimal(str(raw_quantity))
            amount_cad = Decimal(str(raw_amount_cad)) if raw_amount_cad is not None else None
            event_fx_rate = Decimal(str(raw_fx_rate)) if raw_fx_rate is not None else None
            fx_rate, fx_flag = _resolve_fx(currency, event_fx_rate, event_date, fx_series, latest_txn_fx)
            if fx_flag:
                state.flags.add(fx_flag)
            # The view's amount column is only truly CAD when amount_currency
            # says so; activities rows carry the raw transaction-currency cash
            # amount, so convert before it enters book value or realized gain.
            if amount_cad is not None and raw_amount_currency and raw_amount_currency != "CAD":
                amount_fx, amount_flag = _resolve_fx(
                    str(raw_amount_currency), event_fx_rate, event_date, fx_series, latest_txn_fx
                )
                amount_cad *= amount_fx
                if amount_flag:
                    state.flags.add(amount_flag)

            cost_cad = proceeds_cad = None
            if event_type == "BUY":
                cost_cad = _apply_buy(state, quantity, amount_cad, amount_quality, fx_rate, source)
                ledger_delta = quantity
            elif event_type == "SELL":
                proceeds_cad = _apply_sell(state, quantity, amount_cad, amount_quality, ticker_id, source)
                ledger_delta = -quantity
            elif event_type == "SPLIT":
                state.quantity += quantity
                ledger_delta = quantity
            else:
                continue

            if state.quantity < 0:
                state.flags.add("negative_quantity")

            connection.execute(
                """
                INSERT INTO position_ledger (
                    ticker_id, event_date, event_type, source, source_id,
                    quantity_delta, cost_cad, proceeds_cad, fx_rate,
                    running_quantity, running_book_cad, running_book_mkt, realized_gain_cad
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ticker_id, event_date, event_type, source, source_id,
                    _q(ledger_delta, _Q8),
                    _q(cost_cad, _Q4) if cost_cad is not None else None,
                    _q(proceeds_cad, _Q4) if proceeds_cad is not None else None,
                    _q(fx_rate, _Q8),
                    _q(state.quantity, _Q8), _q(state.book_cad, _Q4), _q(state.book_mkt, _Q4),
                    _q(state.realized_cad, _Q4),
                ],
            )

        computed_at = datetime.now()
        for ticker_id, state in states.items():
            if ticker_id in unresolved_split_tickers:
                state.flags.add("split_without_quantity")
            connection.execute(
                """
                INSERT INTO position_snapshots (
                    ticker_id, quantity, book_value_cad, book_value_mkt,
                    realized_gain_cad, provisional_quantity, data_quality_flags, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ticker_id,
                    _q(state.quantity, _Q8), _q(state.book_cad, _Q4), _q(state.book_mkt, _Q4),
                    _q(state.realized_cad, _Q4), _q(state.provisional_quantity, _Q8),
                    json.dumps(sorted(state.flags)) if state.flags else None,
                    computed_at,
                ],
            )

        fingerprint = compute_fingerprint(connection)
        connection.execute("DELETE FROM position_engine_meta WHERE component = ?", [FINGERPRINT_COMPONENT])
        connection.execute(
            "INSERT INTO position_engine_meta (component, ledger_fingerprint, computed_at) VALUES (?, ?, ?)",
            [FINGERPRINT_COMPONENT, fingerprint, computed_at],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        logger.exception("Position engine recompute failed")
        raise

    logger.info(
        "Position engine recompute complete | tickers=%d | events=%d", len(states), len(events)
    )
    return len(states)
