"""Validated CLI and database service for ticker aliases and symbol changes."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from config import DATABASE_PATH, YFINANCE_CANADIAN_SUFFIXES
from database import get_shared_connection, initialize_database
from database_command import ensure_tickers
from yfinance_extractor import configure_yfinance_cache, fetch_security_info


def add_mapping(
    source_symbol: str,
    canonical_symbol: str,
    yahoo_symbol: str,
    currency: str,
    exchange: str = "",
    effective_from: str | None = None,
    effective_to: str | None = None,
    reason: str = "manual override",
    created_by: str = "user",
    db_path: Path | str = DATABASE_PATH,
) -> dict[str, Any]:
    initialize_database(db_path)
    canonical = canonical_symbol.strip().upper()
    source = source_symbol.strip().upper()
    provider = yahoo_symbol.strip().upper()
    currency = currency.strip().upper()
    if not source or not canonical or not provider or currency not in {"CAD", "USD"}:
        raise ValueError("source, canonical, yahoo symbol, and CAD/USD currency are required")
    candidates = ensure_tickers(
        [{"symbol": canonical, "currency": currency}], db_path, require_all=False
    )
    matches = [item for item in candidates.get(canonical, []) if item["currency"] == currency]
    if len(matches) != 1:
        raise ValueError(f"Canonical ticker could not be validated: {canonical}/{currency}")
    ticker_id = int(matches[0]["ticker_id"])
    connection = get_shared_connection(db_path)
    connection.execute(
        """
        DELETE FROM ticker_symbol_history
        WHERE source_symbol = ? AND currency = ? AND effective_from IS NOT DISTINCT FROM ?
        """, [source, currency, effective_from]
    )
    connection.execute(
        """
        INSERT INTO ticker_symbol_history (
            ticker_id, source_symbol, provider_symbol, currency, exchange,
            effective_from, effective_to, reason, mapping_source, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?)
        """,
        [ticker_id, source, provider, currency, exchange.upper() or matches[0]["exchange"],
         effective_from, effective_to, reason, created_by],
    )
    connection.execute(
        """
        INSERT INTO ticker_provider_mappings (
            ticker_id, provider, provider_symbol, verification_status,
            mapping_source, effective_from, effective_to, reason, created_by
        ) VALUES (?, 'yahoo', ?, 'verified', 'manual', ?, ?, ?, ?)
        ON CONFLICT (ticker_id, provider) DO UPDATE SET
            provider_symbol = excluded.provider_symbol,
            verification_status = 'verified', mapping_source = 'manual',
            effective_from = excluded.effective_from, effective_to = excluded.effective_to,
            reason = excluded.reason, created_by = excluded.created_by, verified_at = now()
        """, [ticker_id, provider, effective_from, effective_to, reason, created_by]
    )
    resolved_rows = connection.execute(
        """
        UPDATE email_transactions
        SET ticker_id = ?, ticker_resolution_status = 'resolved'
        WHERE UPPER(source_symbol) = ?
          AND ticker_resolution_status = 'pending'
          AND (price_currency IS NULL OR price_currency = '' OR price_currency = ?)
        RETURNING email_transaction_id
        """,
        [ticker_id, source, currency],
    ).fetchall()
    reconciled_rows = 0
    market_error = None
    if resolved_rows:
        from database_command import reconcile_email_transactions
        from market_data import sync_market_data

        reconciled_rows = reconcile_email_transactions(db_path)
        market_result = sync_market_data(db_path, [canonical])
        market_error = market_result.error
    return {"ticker_id": ticker_id, "source_symbol": source,
            "canonical_symbol": canonical, "provider_symbol": provider,
            "currency": currency, "status": "verified",
            "resolved_email_rows": len(resolved_rows),
            "reconciled_email_rows": reconciled_rows,
            "market_sync_error": market_error}


def list_pending(db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    """Return every source symbol still blocking ingestion, from email or export.

    Email rows are 'pending' (published with a provisional ticker gap); export
    rows come from staged files that were quarantined entirely because a
    symbol could not be resolved. Both are surfaced together so one workflow
    can fix either cause instead of only ever seeing the email-side gaps.
    The 'EMAIL' source symbol is excluded: it is the Interac deposit
    placeholder (a cash movement, not a security) and never has a ticker to
    resolve.
    """
    initialize_database(db_path)
    connection = get_shared_connection(db_path)
    email_rows = connection.execute(
        """
        SELECT source_symbol, NULLIF(price_currency, ''), COUNT(*),
               MIN(transaction_date), MAX(transaction_date)
        FROM email_transactions
        WHERE ticker_resolution_status = 'pending'
          AND source_symbol IS NOT NULL
          AND source_symbol <> 'EMAIL'
        GROUP BY source_symbol, NULLIF(price_currency, '')
        """
    ).fetchall()
    export_rows = connection.execute(
        """
        SELECT r.source_symbol, NULLIF(COALESCE(r.inferred_listing_currency, r.price_currency), ''),
               COUNT(*), MIN(r.transaction_date), MAX(r.transaction_date)
        FROM staged_records r
        JOIN staged_files f USING (staged_file_id)
        WHERE f.source_type = 'export' AND f.status = 'quarantined'
          AND r.resolution_status = 'unresolved'
          AND r.source_symbol IS NOT NULL
        GROUP BY r.source_symbol, NULLIF(COALESCE(r.inferred_listing_currency, r.price_currency), '')
        """
    ).fetchall()

    merged: dict[str, dict[str, Any]] = {}
    tagged_rows = [(*row, "email") for row in email_rows] + [(*row, "export") for row in export_rows]
    for symbol, currency, count, first_seen, last_seen, source_label in tagged_rows:
        entry = merged.setdefault(symbol, {
            "source_symbol": symbol, "detected_currency": None, "trade_count": 0,
            "first_seen": None, "last_seen": None, "sources": set(),
        })
        entry["detected_currency"] = entry["detected_currency"] or currency
        entry["trade_count"] += count
        seen_dates = [d for d in (entry["first_seen"], first_seen) if d is not None]
        entry["first_seen"] = min(seen_dates) if seen_dates else None
        seen_dates = [d for d in (entry["last_seen"], last_seen) if d is not None]
        entry["last_seen"] = max(seen_dates) if seen_dates else None
        entry["sources"].add(source_label)

    mappings_by_symbol = _active_mappings_by_symbol(db_path)
    result = []
    for symbol, entry in sorted(merged.items(), key=lambda kv: kv[0]):
        candidates = mappings_by_symbol.get(symbol, [])
        chosen = next(
            (m for m in candidates if m["currency"] == entry["detected_currency"]),
            candidates[0] if candidates else None,
        )
        result.append({
            **entry, "sources": sorted(entry["sources"]),
            "already_mapped": chosen is not None, "mapping": chosen,
        })
    return result


def _active_mappings_by_symbol(db_path: Path | str) -> dict[str, list[dict[str, Any]]]:
    """Return every source_symbol's currently-active saved mapping(s).

    `resolve_or_enrich_ticker` never consults `ticker_symbol_history` (export
    resolution is a live `tickers`-table match), so this is the only place
    that reports "this symbol was already mapped once" back to callers such
    as `list_pending`.
    """
    rows = get_shared_connection(db_path).execute(
        """
        SELECT h.source_symbol, t.ticker_symbol, h.provider_symbol, h.currency, h.exchange
        FROM ticker_symbol_history h JOIN tickers t USING (ticker_id)
        WHERE h.effective_to IS NULL
        """
    ).fetchall()
    mapped: dict[str, list[dict[str, Any]]] = {}
    for source_symbol, canonical, provider_symbol, currency, exchange in rows:
        mapped.setdefault(source_symbol, []).append({
            "canonical_symbol": canonical, "provider_symbol": provider_symbol,
            "currency": currency, "exchange": exchange,
        })
    return mapped


def resolve_pending_symbol(
    source_symbol: str,
    canonical_symbol: str,
    yahoo_symbol: str,
    currency: str,
    exchange: str = "",
    *,
    created_by: str = "dashboard-api",
    db_path: Path | str = DATABASE_PATH,
) -> dict[str, Any]:
    """Resolve one pending source symbol without prompting.

    Applies the same two steps as `resolve_pending_interactively` -- verify the
    provider symbol against Yahoo Finance, then save the mapping -- for callers
    that cannot prompt, such as the dashboard's resolve action. The interactive
    command keeps its own loop because it re-prompts on a failed match; here a
    bad symbol is simply an error the caller surfaces.

    Raises `ValueError` when the provider symbol cannot be verified, so an
    unverified alias never reaches `ticker_provider_mappings`.
    """
    configure_yfinance_cache(_default_yfinance_cache_dir())
    provider = str(yahoo_symbol or "").strip().upper()
    normalized_currency = str(currency or "").strip().upper()
    if _verify_provider_symbol(provider, normalized_currency) is None:
        raise ValueError(
            f"Could not verify {provider} as a {normalized_currency or 'tradeable'} security on "
            "Yahoo Finance."
        )
    return add_mapping(
        source_symbol,
        canonical_symbol,
        provider,
        normalized_currency,
        exchange,
        reason="resolved pending ticker",
        created_by=created_by,
        db_path=db_path,
    )


def resolve_pending_interactively(db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    configure_yfinance_cache(_default_yfinance_cache_dir())
    pending = list_pending(db_path)
    results: list[dict[str, Any]] = []
    for item in pending:
        symbol = str(item["source_symbol"])
        detected = str(item["detected_currency"] or "")
        sources = item.get("sources")
        source_note = f"; source: {', '.join(sources)}" if sources else ""
        print(f"Resolve {symbol} ({item['trade_count']} pending trade(s){source_note})")
        currency = input(f"Currency [CAD/USD]{f' [{detected}]' if detected else ''}: ").strip().upper() or detected
        canonical = input(f"Canonical symbol [{symbol}]: ").strip().upper() or symbol
        is_canadian_suffixed = any(canonical.endswith(suffix) for suffix in YFINANCE_CANADIAN_SUFFIXES)
        default_yahoo = canonical if is_canadian_suffixed else (
            f"{canonical}.TO" if currency == "CAD" else canonical
        )
        match = None
        while match is None:
            yahoo = input(f"Yahoo symbol [{default_yahoo}]: ").strip().upper() or default_yahoo
            if yahoo == "SKIP":
                break
            match = _verify_provider_symbol(yahoo, currency)
            if match is None:
                print(
                    f"Could not verify {yahoo} as a {currency or 'tradeable'} security on Yahoo "
                    f"Finance. Try again, or enter SKIP to leave {symbol} unresolved."
                )
        if match is None:
            results.append({"source_symbol": symbol, "status": "skipped"})
            continue
        print(f"Matched: {match['company_name']} ({match['provider_symbol']}, {match['exchange']})")
        exchange = input("Exchange (for example TSX or NASDAQ): ").strip().upper()
        confirmation = input(
            f"Save {symbol} -> {canonical} ({currency}, {exchange or 'provider exchange'}, {yahoo})? [y/N]: "
        ).strip().lower()
        if confirmation not in {"y", "yes"}:
            results.append({"source_symbol": symbol, "status": "skipped"})
            continue
        results.append(add_mapping(
            symbol, canonical, yahoo, currency, exchange,
            reason="resolved pending email ticker", created_by="interactive-cli",
            db_path=db_path,
        ))
    return results


def _default_yfinance_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def _verify_provider_symbol(yahoo_symbol: str, currency: str) -> dict[str, Any] | None:
    """Confirm `yahoo_symbol` resolves to a real, currency-matching equity or
    ETF on Yahoo Finance before it can be saved as a provider symbol mapping.

    Interactive resolution previously accepted whatever text was typed
    (including a rejected default like "no") as the literal Yahoo symbol,
    silently mapping a source ticker to an unrelated real security.
    """
    stocks, etfs = fetch_security_info([{"symbol": yahoo_symbol, "currency": currency}])
    frame = stocks if not stocks.empty else etfs
    if frame.empty:
        return None
    row = frame.iloc[0]
    return {
        "provider_symbol": row["provider_symbol"],
        "company_name": row["company_name"],
        "exchange": row["exchange"],
    }


def list_mappings(db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    initialize_database(db_path)
    rows = get_shared_connection(db_path).execute(
        """
        SELECT h.source_symbol, t.ticker_symbol, h.provider_symbol, h.currency,
               t.currency, t.financial_currency, h.exchange,
               h.effective_from, h.effective_to, h.reason,
               h.mapping_source, h.created_by
        FROM ticker_symbol_history h JOIN tickers t USING (ticker_id)
        ORDER BY h.source_symbol, h.effective_from NULLS FIRST
        """
    ).fetchall()
    keys = ("source_symbol", "canonical_symbol", "provider_symbol", "mapping_currency",
            "trading_currency", "financial_currency", "exchange", "effective_from",
            "effective_to", "reason", "mapping_source", "created_by")
    return [{key: (value.isoformat() if isinstance(value, date) else value)
             for key, value in zip(keys, row)} for row in rows]


def retire_mapping(source_symbol: str, currency: str, effective_to: str,
                   db_path: Path | str = DATABASE_PATH) -> int:
    initialize_database(db_path)
    connection = get_shared_connection(db_path)
    rows = connection.execute(
        """UPDATE ticker_symbol_history SET effective_to = ?
           WHERE source_symbol = ? AND currency = ? AND effective_to IS NULL
           RETURNING source_symbol""",
        [effective_to, source_symbol.upper(), currency.upper()],
    ).fetchall()
    return len(rows)


def _resolve_merge_candidate(
    connection: Any, symbol: str, currency: str, exchange: str,
) -> tuple[int, str, str]:
    """Return (ticker_id, ticker_symbol, exchange) for one merge side, or raise ValueError."""
    params: list[Any] = [symbol, currency]
    exchange_clause = ""
    if exchange:
        exchange_clause = " AND exchange = ?"
        params.append(exchange)
    rows = connection.execute(
        f"""
        SELECT ticker_id, ticker_symbol, exchange
        FROM tickers
        WHERE ticker_symbol = ? AND currency = ?{exchange_clause}
        """,
        params,
    ).fetchall()
    if not rows:
        suffix = f" on {exchange}" if exchange else ""
        raise ValueError(f"No ticker found for {symbol}/{currency}{suffix}")
    if len(rows) > 1:
        exchanges = ", ".join(sorted(row[2] for row in rows))
        raise ValueError(
            f"{symbol}/{currency} matches {len(rows)} tickers across exchanges "
            f"{exchanges}; pass --exchange to disambiguate"
        )
    ticker_id, ticker_symbol, ticker_exchange = rows[0]
    return int(ticker_id), ticker_symbol, ticker_exchange


def _raise_on_collision(
    connection: Any, table: str, id_column: str, match_columns: list[str],
    survivor_id: int, loser_id: int,
) -> None:
    """Raise if repointing the loser's rows onto the survivor would collide on
    the table's ticker_id-inclusive UNIQUE constraint. Rows must never
    silently vanish or stay stranded on a to-be-retired ticker_id, so any
    collision here is fatal, not skipped -- and this must run before anything
    is mutated, since none of this module's writes can rely on a rollback.
    """
    match_clause = " AND ".join(
        f"t2.{column} IS NOT DISTINCT FROM t1.{column}" for column in match_columns
    )
    colliding = connection.execute(
        f"""
        SELECT t1.{id_column} FROM {table} t1
        WHERE t1.ticker_id = ?
          AND EXISTS (
            SELECT 1 FROM {table} t2
            WHERE t2.ticker_id = ? AND {match_clause}
          )
        """,
        [loser_id, survivor_id],
    ).fetchall()
    if colliding:
        ids = ", ".join(str(row[0]) for row in colliding)
        raise ValueError(
            f"{table} row(s) {ids} would collide with an existing ticker_id {survivor_id} "
            f"row after merging ticker_id {loser_id}; resolve the duplicate(s) manually first"
        )


def _merge_unique_constrained_table(
    connection: Any, table: str, id_column: str, match_columns: list[str],
    survivor_id: int, loser_id: int, dry_run: bool,
) -> dict[str, Any]:
    """Repoint ticker_id after confirming no collision (see `_raise_on_collision`)."""
    _raise_on_collision(connection, table, id_column, match_columns, survivor_id, loser_id)
    moved = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker_id = ?", [loser_id]).fetchone()[0]
    if not dry_run:
        connection.execute(f"UPDATE {table} SET ticker_id = ? WHERE ticker_id = ?", [survivor_id, loser_id])
    return {"moved": moved, "collided_and_dropped": 0}


def _merge_singleton_table(
    connection: Any, table: str, nullable_columns: list[str],
    survivor_id: int, loser_id: int, dry_run: bool,
) -> dict[str, Any]:
    """Merge a 1:1 (PK is ticker_id alone) detail table: survivor's row wins if
    present; otherwise adopt the loser's row; drop an empty loser row instead
    of adopting it.
    """
    survivor_has = connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE ticker_id = ?", [survivor_id]
    ).fetchone()[0] > 0
    if nullable_columns:
        loser_row = connection.execute(
            f"SELECT {', '.join(nullable_columns)} FROM {table} WHERE ticker_id = ?", [loser_id]
        ).fetchone()
        loser_has = loser_row is not None
        loser_has_data = loser_has and any(value is not None for value in loser_row)
    else:
        loser_has = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ticker_id = ?", [loser_id]
        ).fetchone()[0] > 0
        loser_has_data = loser_has

    if not loser_has:
        return {"kept": "survivor" if survivor_has else "none", "dropped_loser_row": False}
    if survivor_has:
        if not dry_run:
            connection.execute(f"DELETE FROM {table} WHERE ticker_id = ?", [loser_id])
        return {"kept": "survivor", "dropped_loser_row": True}
    if loser_has_data:
        if not dry_run:
            connection.execute(f"UPDATE {table} SET ticker_id = ? WHERE ticker_id = ?", [survivor_id, loser_id])
        return {"kept": "loser", "dropped_loser_row": False}
    if not dry_run:
        connection.execute(f"DELETE FROM {table} WHERE ticker_id = ?", [loser_id])
    return {"kept": "none", "dropped_loser_row": True}


_TICKER_REFERENCING_TABLES = (
    "email_transactions", "activities", "staged_records",
    "historical_records", "ticker_provider_mappings", "stock_details",
    "etf_details", "portfolio_classifications", "ticker_symbol_history",
)


def _move_transactions(connection: Any, from_id: int, to_id: int) -> None:
    """Repoint transactions.ticker_id, detaching any incoming
    email_transactions.matched_transaction_id reference first and restoring
    it afterward -- that incoming FK triggers the same DuckDB row-rebuild
    issue `_rename_ticker_symbol` works around, since ticker_id also
    participates in a UNIQUE constraint on this table.
    """
    moved_ids = [row[0] for row in connection.execute(
        "SELECT transaction_id FROM transactions WHERE ticker_id = ?", [from_id]
    ).fetchall()]
    if not moved_ids:
        return
    placeholders = ",".join("?" for _ in moved_ids)
    protected = connection.execute(
        f"SELECT email_transaction_id, matched_transaction_id FROM email_transactions "
        f"WHERE matched_transaction_id IN ({placeholders})",
        moved_ids,
    ).fetchall()
    if protected:
        connection.execute(
            f"UPDATE email_transactions SET matched_transaction_id = NULL "
            f"WHERE matched_transaction_id IN ({placeholders})",
            moved_ids,
        )
    connection.execute("UPDATE transactions SET ticker_id = ? WHERE ticker_id = ?", [to_id, from_id])
    for email_transaction_id, matched_transaction_id in protected:
        connection.execute(
            "UPDATE email_transactions SET matched_transaction_id = ? WHERE email_transaction_id = ?",
            [matched_transaction_id, email_transaction_id],
        )


def _rename_ticker_symbol(
    connection: Any, ticker_id: int, new_symbol: str, security_name_suffix: str = "",
) -> None:
    """Rename `tickers.ticker_symbol` for a row that has foreign-key references.

    DuckDB rebuilds a row (delete+insert) whenever an indexed column changes,
    which trips foreign-key checks even for an in-place update that never
    touches the referenced primary key (`ticker_id` itself is untouched here).
    Route around it: park every referencing row on a disposable placeholder
    ticker, rename the now-unreferenced row, then repoint everything back.
    """
    placeholder_symbol = f"__MERGE_HOLD_{ticker_id}__"
    holding_id = int(connection.execute(
        """
        INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
        SELECT ?, exchange, currency, security_name, security_type FROM tickers WHERE ticker_id = ?
        RETURNING ticker_id
        """,
        [placeholder_symbol, ticker_id],
    ).fetchone()[0])

    _move_transactions(connection, ticker_id, holding_id)
    for table in _TICKER_REFERENCING_TABLES:
        connection.execute(f"UPDATE {table} SET ticker_id = ? WHERE ticker_id = ?", [holding_id, ticker_id])

    if security_name_suffix:
        connection.execute(
            "UPDATE tickers SET ticker_symbol = ?, security_name = security_name || ? WHERE ticker_id = ?",
            [new_symbol, security_name_suffix, ticker_id],
        )
    else:
        connection.execute("UPDATE tickers SET ticker_symbol = ? WHERE ticker_id = ?", [new_symbol, ticker_id])

    for table in _TICKER_REFERENCING_TABLES:
        connection.execute(f"UPDATE {table} SET ticker_id = ? WHERE ticker_id = ?", [ticker_id, holding_id])
    _move_transactions(connection, holding_id, ticker_id)

    connection.execute("DELETE FROM tickers WHERE ticker_id = ?", [holding_id])


def merge_tickers(
    old_symbol: str,
    new_symbol: str,
    currency: str,
    exchange: str = "",
    effective_to: str | None = None,
    reason: str = "provider symbol rename",
    created_by: str = "user",
    dry_run: bool = False,
    db_path: Path | str = DATABASE_PATH,
) -> dict[str, Any]:
    """Consolidate two already-populated ticker identities into one.

    Used when a provider/broker renames a symbol (e.g. SPLG -> SPYM) after the
    pipeline already created a separate `tickers` row for the new symbol text,
    since ticker resolution is keyed purely by exact symbol match and never
    consults `ticker_symbol_history`. The older (lower) ticker_id always
    survives so repeated future renames of the same security keep merging
    into one permanent anchor identity; its `ticker_symbol` is always updated
    to `new_symbol`. The losing ticker's row is renamed with a `_MERGED_`
    marker and retained (never deleted), since `ticker_symbol_history` rows
    for its original identity still reference it and must stay backtrackable.

    Every write here runs outside an explicit transaction (no BEGIN/COMMIT):
    DuckDB rebuilds a row (delete+insert) whenever an indexed/unique-
    constrained column changes, and its foreign-key checker becomes unreliable
    when that rebuild happens inside a multi-statement transaction that has
    already touched other rows of an FK-related table -- confirmed by direct
    experimentation against this DuckDB version, and true for both `tickers`
    (renaming `ticker_symbol`, part of `UNIQUE(ticker_symbol, exchange)`) and
    `transactions` (repointing `ticker_id`, part of a ticker_id-inclusive
    UNIQUE constraint, whenever a row is referenced by
    `email_transactions.matched_transaction_id`). The same statements are
    reliable run individually. To compensate for the resulting lack of
    automatic rollback, every possible collision is detected and raised
    *before* any table is mutated, so a failure partway through mutation
    should never occur in practice.
    """
    initialize_database(db_path)
    connection = get_shared_connection(db_path)
    old = old_symbol.strip().upper()
    new = new_symbol.strip().upper()
    currency = currency.strip().upper()
    exchange = exchange.strip().upper()
    effective_to = effective_to or date.today().isoformat()

    old_id, old_original_symbol, old_exchange = _resolve_merge_candidate(connection, old, currency, exchange)
    new_id, new_original_symbol, new_exchange = _resolve_merge_candidate(connection, new, currency, exchange)
    if old_id == new_id:
        raise ValueError(f"{old} and {new} already resolve to the same ticker_id ({old_id}); nothing to merge")

    survivor_id, loser_id = (old_id, new_id) if old_id < new_id else (new_id, old_id)
    loser_original_symbol, loser_exchange = (
        (old_original_symbol, old_exchange) if loser_id == old_id else (new_original_symbol, new_exchange)
    )

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "old_symbol": old, "new_symbol": new,
        "surviving_ticker_id": survivor_id, "losing_ticker_id": loser_id,
        "currency": currency, "exchange": exchange or old_exchange or new_exchange,
        "tables": {},
    }

    # Collision detection for both ticker_id-inclusive-UNIQUE tables runs first,
    # before anything is mutated, so a merge that would collide fails loudly
    # without having touched any other table yet.
    transactions_match_columns = [
        "transaction_date", "transaction_type", "quantity", "execution_date", "debit", "credit", "fx_rate",
    ]
    _raise_on_collision(
        connection, "transactions", "transaction_id", transactions_match_columns, survivor_id, loser_id,
    )
    email_match_columns = [
        "account", "transaction_type", "quantity", "average_price", "total_cost", "debit", "transaction_date",
    ]
    _raise_on_collision(
        connection, "email_transactions", "email_transaction_id", email_match_columns, survivor_id, loser_id,
    )

    # transactions: repointing ticker_id must temporarily detach any
    # email_transactions.matched_transaction_id pointing at a row about to
    # move, since that incoming FK triggers the same DuckDB rebuild issue.
    moved_transaction_ids = [row[0] for row in connection.execute(
        "SELECT transaction_id FROM transactions WHERE ticker_id = ?", [loser_id]
    ).fetchall()]
    if not dry_run and moved_transaction_ids:
        placeholders = ",".join("?" for _ in moved_transaction_ids)
        protected_matches = connection.execute(
            f"SELECT email_transaction_id, matched_transaction_id FROM email_transactions "
            f"WHERE matched_transaction_id IN ({placeholders})",
            moved_transaction_ids,
        ).fetchall()
        if protected_matches:
            connection.execute(
                f"UPDATE email_transactions SET matched_transaction_id = NULL "
                f"WHERE matched_transaction_id IN ({placeholders})",
                moved_transaction_ids,
            )
        connection.execute("UPDATE transactions SET ticker_id = ? WHERE ticker_id = ?", [survivor_id, loser_id])
        for email_transaction_id, matched_transaction_id in protected_matches:
            connection.execute(
                "UPDATE email_transactions SET matched_transaction_id = ? WHERE email_transaction_id = ?",
                [matched_transaction_id, email_transaction_id],
            )
    report["tables"]["transactions"] = {"moved": len(moved_transaction_ids), "collided_and_dropped": 0}

    report["tables"]["email_transactions"] = _merge_unique_constrained_table(
        connection, "email_transactions", "email_transaction_id", email_match_columns, survivor_id, loser_id, dry_run,
    )

    overlap_dropped = connection.execute(
        """
        SELECT COUNT(*) FROM historical_records
        WHERE ticker_id = ? AND record_date IN (
            SELECT record_date FROM historical_records WHERE ticker_id = ?
        )
        """,
        [loser_id, survivor_id],
    ).fetchone()[0]
    moved = connection.execute(
        "SELECT COUNT(*) FROM historical_records WHERE ticker_id = ?", [loser_id]
    ).fetchone()[0] - overlap_dropped
    if not dry_run:
        connection.execute(
            """
            DELETE FROM historical_records
            WHERE ticker_id = ? AND record_date IN (
                SELECT record_date FROM historical_records WHERE ticker_id = ?
            )
            """,
            [loser_id, survivor_id],
        )
        connection.execute(
            "UPDATE historical_records SET ticker_id = ? WHERE ticker_id = ?",
            [survivor_id, loser_id],
        )
    report["tables"]["historical_records"] = {"moved": moved, "overlap_dates_dropped": overlap_dropped}

    moved = connection.execute("SELECT COUNT(*) FROM activities WHERE ticker_id = ?", [loser_id]).fetchone()[0]
    if not dry_run:
        connection.execute("UPDATE activities SET ticker_id = ? WHERE ticker_id = ?", [survivor_id, loser_id])
    report["tables"]["activities"] = {"moved": moved}

    moved = connection.execute("SELECT COUNT(*) FROM staged_records WHERE ticker_id = ?", [loser_id]).fetchone()[0]
    if not dry_run:
        connection.execute("UPDATE staged_records SET ticker_id = ? WHERE ticker_id = ?", [survivor_id, loser_id])
    report["tables"]["staged_records"] = {"moved": moved}

    loser_provider_count = connection.execute(
        "SELECT COUNT(*) FROM ticker_provider_mappings WHERE ticker_id = ?", [loser_id]
    ).fetchone()[0]
    if not dry_run:
        connection.execute(
            """
            UPDATE ticker_provider_mappings SET ticker_id = ?
            WHERE ticker_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM ticker_provider_mappings p2
                WHERE p2.ticker_id = ? AND p2.provider = ticker_provider_mappings.provider
              )
            """,
            [survivor_id, loser_id, survivor_id],
        )
        connection.execute("DELETE FROM ticker_provider_mappings WHERE ticker_id = ?", [loser_id])
        connection.execute(
            """
            UPDATE ticker_provider_mappings
            SET provider_symbol = ?, verification_status = 'verified',
                mapping_source = 'merge', reason = ?, verified_at = now()
            WHERE ticker_id = ? AND provider = 'yahoo'
            """,
            [new, reason, survivor_id],
        )
    report["tables"]["ticker_provider_mappings"] = {
        "loser_rows_removed": loser_provider_count,
        "survivor_symbol_updated_to": new,
    }

    report["tables"]["stock_details"] = _merge_singleton_table(
        connection, "stock_details", ["sector", "industry"], survivor_id, loser_id, dry_run,
    )
    report["tables"]["etf_details"] = _merge_singleton_table(
        connection, "etf_details",
        ["fund_family", "yield", "expense_ratio", "aum", "nav", "top_holdings", "sector_weights"],
        survivor_id, loser_id, dry_run,
    )
    report["tables"]["portfolio_classifications"] = _merge_singleton_table(
        connection, "portfolio_classifications", [], survivor_id, loser_id, dry_run,
    )

    if not dry_run:
        remaining = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM transactions WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM email_transactions WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM activities WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM staged_records WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM historical_records WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM ticker_provider_mappings WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM stock_details WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM etf_details WHERE ticker_id = ?) +
              (SELECT COUNT(*) FROM portfolio_classifications WHERE ticker_id = ?)
            """,
            [loser_id] * 9,
        ).fetchone()[0]
        if remaining:
            raise RuntimeError(
                f"Merge verification failed: {remaining} row(s) still reference "
                f"ticker_id {loser_id} after repointing; data was already partially "
                f"moved and this database now needs manual inspection"
            )

    merged_symbol = f"{loser_original_symbol}_MERGED_{loser_id}"
    history_closed = 0
    if not dry_run:
        history_closed = len(connection.execute(
            """
            UPDATE ticker_symbol_history SET effective_to = ?
            WHERE ticker_id = ? AND effective_to IS NULL
            RETURNING ticker_id
            """,
            [effective_to, loser_id],
        ).fetchall())
        # Rename the loser to its retired marker first, freeing up the live
        # symbol text before the survivor claims it (both rows briefly hold a
        # symbol subject to the UNIQUE(ticker_symbol, exchange) constraint).
        _rename_ticker_symbol(
            connection, loser_id, merged_symbol,
            security_name_suffix=f" (merged into ticker_id {survivor_id})",
        )
        _rename_ticker_symbol(connection, survivor_id, new)
        # effective_from is the merge date, not the closed row's original
        # effective_from: the alias only became true of the survivor as of
        # this merge, and reusing the closed row's effective_from would
        # collide with it on UNIQUE(source_symbol, currency, effective_from)
        # since that constraint isn't scoped by ticker_id. effective_to is
        # left open (NULL) since this mapping is active going forward.
        connection.execute(
            """
            INSERT INTO ticker_symbol_history (
                ticker_id, source_symbol, provider_symbol, currency, exchange,
                effective_from, effective_to, reason, mapping_source, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'merge', ?)
            """,
            [survivor_id, loser_original_symbol, loser_original_symbol, currency,
             loser_exchange, effective_to, reason, created_by],
        )
    report["ticker_symbol_history_closed"] = history_closed
    report["ticker_symbol_history_inserted"] = 0 if dry_run else 1
    report["surviving_ticker_symbol_updated_to"] = new
    report["losing_ticker_marked_merged"] = not dry_run
    report["losing_ticker_symbol_renamed_to"] = merged_symbol

    return report


def import_csv(path: Path, db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [add_mapping(db_path=db_path, **{key: value for key, value in row.items() if value != ""})
                for row in csv.DictReader(handle)]


def validate_mappings(source_symbol: str | None = None,
                      db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    mappings = list_mappings(db_path)
    selected = [item for item in mappings
                if not source_symbol or item["source_symbol"] == source_symbol.upper()]
    configure_yfinance_cache(_default_yfinance_cache_dir())
    results = []
    for item in selected:
        stocks, etfs = fetch_security_info([item["provider_symbol"]])
        valid = not stocks.empty or not etfs.empty
        results.append({**item, "validation_status": "verified" if valid else "failed"})
    return results


def _format_inline_dict(value: dict[str, Any]) -> str:
    return ", ".join(f"{key}={_format_cell(inner)}" for key, inner in value.items())


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_format_inline_dict(inner) if isinstance(inner, dict) else _format_cell(inner)}"
            for key, inner in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _format_table(rows: list[dict[str, Any]]) -> str:
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    formatted_rows = [[_format_cell(row.get(header)) for header in headers] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in formatted_rows))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.upper().ljust(width) for header, width in zip(headers, widths)),
        "  ".join("-" * width for width in widths),
    ]
    for row in formatted_rows:
        lines.append("  ".join(value.ljust(width) for value, width in zip(row, widths)))
    return "\n".join(lines)


def _format_record(record: dict[str, Any]) -> str:
    if not record:
        return "No results."
    width = max(len(key) for key in record)
    return "\n".join(f"{key.ljust(width)} : {_format_cell(value)}" for key, value in record.items())


def format_text(result: Any) -> str:
    """Render a command result as aligned, human-readable text (not a JSON or repr dump)."""
    if isinstance(result, list):
        if not result:
            return "No results."
        if all(isinstance(item, dict) for item in result):
            return _format_table(result)
        return "\n".join(str(item) for item in result)
    if isinstance(result, dict):
        return _format_record(result)
    return str(result)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage ticker mappings")
    commands = parser.add_subparsers(dest="command", required=True)
    parsers = []
    for name in ("add", "update"):
        command = commands.add_parser(name)
        parsers.append(command)
        command.add_argument("--source-symbol", required=True)
        command.add_argument("--canonical-symbol", required=True)
        command.add_argument("--yahoo-symbol", required=True)
        command.add_argument("--currency", required=True, choices=("CAD", "USD"))
        command.add_argument("--exchange", default="")
        command.add_argument("--effective-from")
        command.add_argument("--effective-to")
        command.add_argument("--reason", default="manual override")
        command.add_argument("--created-by", default="user")
    parsers.append(commands.add_parser("list"))
    validate = commands.add_parser("validate")
    parsers.append(validate)
    validate.add_argument("--source-symbol")
    retire = commands.add_parser("retire")
    parsers.append(retire)
    retire.add_argument("--source-symbol", required=True)
    retire.add_argument("--currency", required=True, choices=("CAD", "USD"))
    retire.add_argument("--effective-to", required=True)
    importer = commands.add_parser("import-csv")
    parsers.append(importer)
    importer.add_argument("path", type=Path)
    pending = commands.add_parser("pending")
    parsers.append(pending)
    resolver = commands.add_parser("resolve-pending")
    parsers.append(resolver)
    merge = commands.add_parser("merge")
    parsers.append(merge)
    merge.add_argument("--old-symbol", required=True)
    merge.add_argument("--new-symbol", required=True)
    merge.add_argument("--currency", required=True, choices=("CAD", "USD"))
    merge.add_argument("--exchange", default="")
    merge.add_argument("--effective-to")
    merge.add_argument("--reason", default="provider symbol rename")
    merge.add_argument("--created-by", default="user")
    merge.add_argument("--dry-run", action="store_true")
    merge.add_argument("--yes", action="store_true")
    for command in parsers:
        command.add_argument("--database", type=Path, default=DATABASE_PATH)
        command.add_argument("--output", choices=("text", "json"), default="text")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command in {"add", "update"}:
        values = vars(args).copy()
        for key in ("command", "database", "output"):
            values.pop(key)
        result: Any = add_mapping(db_path=args.database, **values)
    elif args.command == "list":
        result = list_mappings(args.database)
    elif args.command == "validate":
        result = validate_mappings(args.source_symbol, args.database)
    elif args.command == "retire":
        result = {"updated": retire_mapping(args.source_symbol, args.currency, args.effective_to, args.database)}
    elif args.command == "pending":
        result = list_pending(args.database)
    elif args.command == "resolve-pending":
        result = resolve_pending_interactively(args.database)
    elif args.command == "merge":
        if not args.dry_run and not args.yes:
            preview = merge_tickers(
                args.old_symbol, args.new_symbol, args.currency,
                exchange=args.exchange, effective_to=args.effective_to,
                reason=args.reason, created_by=args.created_by,
                dry_run=True, db_path=args.database,
            )
            print(json.dumps(preview, indent=2, default=str) if args.output == "json" else format_text(preview))
            print("\nRe-run with --yes to apply.")
            return 1
        result = merge_tickers(
            args.old_symbol, args.new_symbol, args.currency,
            exchange=args.exchange, effective_to=args.effective_to,
            reason=args.reason, created_by=args.created_by,
            dry_run=args.dry_run, db_path=args.database,
        )
    else:
        result = import_csv(args.path, args.database)
    print(json.dumps(result, indent=2, default=str) if args.output == "json" else format_text(result))
    return 0
