"""Durable staging and evidence-based ticker resolution for ingestion batches."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATABASE_PATH
from database import get_shared_connection
from system_logger import get_logger
from ticker_pipeline import (
    contains_fx_rate_label,
    listing_currency_from_fx,
    resolve_or_enrich_ticker,
)


logger = get_logger(__name__)


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return value


def _payload(row: dict[str, Any]) -> str:
    return json.dumps({key: _json_value(value) for key, value in row.items()}, default=str)


def _text_or_none(value: Any) -> str | None:
    normalized = _json_value(value)
    if normalized is None:
        return None
    text = str(normalized).strip()
    return text if text and text.upper() != "NAN" else None


def _number_or_none(value: Any) -> Any:
    normalized = _json_value(value)
    if normalized is None or not str(normalized).strip():
        return None
    return normalized


def _date_or_none(value: Any) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def create_batch(db_path: Path | str = DATABASE_PATH) -> int:
    row = get_shared_connection(db_path).execute(
        "INSERT INTO ingestion_batches (status) VALUES ('staging') RETURNING batch_id"
    ).fetchone()
    return int(row[0])


def _source_hash(source_path: Path | None, source_type: str, data: pd.DataFrame) -> str:
    if source_path and source_path.exists():
        return hashlib.sha256(source_path.read_bytes()).hexdigest()
    return hashlib.sha256(f"{source_type}:{data.to_json(date_format='iso')}".encode()).hexdigest()


def reusable_dataframe(source_type: str, source_path: Path,
                       db_path: Path | str = DATABASE_PATH) -> pd.DataFrame | None:
    """Return the latest durable staged extraction for an unchanged failed file."""
    source_hash = _source_hash(source_path, source_type, pd.DataFrame())
    connection = get_shared_connection(db_path)
    row = connection.execute(
        """
        SELECT staged_file_id FROM staged_files
        WHERE source_type = ? AND source_hash = ? AND status IN ('staged', 'quarantined')
        ORDER BY staged_at DESC LIMIT 1
        """, [source_type, source_hash]
    ).fetchone()
    if not row:
        return None
    payloads = connection.execute(
        "SELECT normalized_payload FROM staged_records WHERE staged_file_id = ? ORDER BY record_sequence",
        [row[0]],
    ).fetchall()
    logger.info("Reusing staged extraction | source=%s | file=%s | rows=%d",
                source_type, source_path, len(payloads))
    return pd.DataFrame([
        json.loads(payload if isinstance(payload, str) else json.dumps(payload))
        for (payload,) in payloads
    ])


def stage_dataframe(
    batch_id: int,
    source_type: str,
    source_path: Path | None,
    file_sequence: int,
    data: pd.DataFrame,
    db_path: Path | str = DATABASE_PATH,
) -> int:
    connection = get_shared_connection(db_path)
    staged_file_id = int(connection.execute(
        """
        INSERT INTO staged_files (
            batch_id, source_type, source_path, source_hash, file_sequence, status
        ) VALUES (?, ?, ?, ?, ?, 'staged') RETURNING staged_file_id
        """,
        [batch_id, source_type, str(source_path) if source_path else None,
         _source_hash(source_path, source_type, data), file_sequence],
    ).fetchone()[0])

    for sequence, row in enumerate(data.to_dict(orient="records"), start=1):
        if source_type == "statement":
            symbol = _text_or_none(row.get("ticker_id"))
            description = _text_or_none(row.get("description"))
            security_name = description.split(":", 1)[0].strip() if description else None
            transaction_type = row.get("transaction")
            transaction_date = row.get("date")
            fx_rate = _number_or_none(row.get("fx_rate"))
            contains_fx_rate = contains_fx_rate_label(fx_rate)
            price_currency = "USD" if contains_fx_rate == "Yes" else None
            if symbol and _is_trade(transaction_type):
                inferred_listing_currency = listing_currency_from_fx(contains_fx_rate)
                listing_evidence = (
                    "statement_fx" if contains_fx_rate == "Yes" else "statement_no_fx"
                )
            else:
                inferred_listing_currency = None
                listing_evidence = None
        elif source_type == "email":
            symbol = _text_or_none(row.get("ticker"))
            if symbol and symbol.upper() == "EMAIL":
                symbol = None
            security_name = None
            transaction_type = row.get("transaction")
            transaction_date = row.get("date")
            fx_rate = None
            contains_fx_rate = contains_fx_rate_label(fx_rate)
            price_currency = row.get("price_currency")
            inferred_listing_currency = None
            listing_evidence = None
        else:
            symbol = _text_or_none(row.get("symbol"))
            security_name = _text_or_none(row.get("name"))
            transaction_type = row.get("activity_sub_type") or row.get("activity_type")
            transaction_date = row.get("transaction_date")
            fx_rate = None
            contains_fx_rate = contains_fx_rate_label(fx_rate)
            price_currency = None  # export currency is transaction currency, not listing currency
            inferred_listing_currency = None
            listing_evidence = None

        normalized = dict(row)
        connection.execute(
            """
            INSERT INTO staged_records (
                staged_file_id, record_sequence, transaction_date, transaction_type,
                source_symbol, security_name, fx_rate, contains_fx_rate, price_currency,
                inferred_listing_currency, listing_evidence,
                raw_payload, normalized_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [staged_file_id, sequence, _date_or_none(transaction_date), _json_value(transaction_type),
             symbol.upper() if symbol else None, _json_value(security_name),
             _json_value(fx_rate), contains_fx_rate, _json_value(price_currency), inferred_listing_currency,
             listing_evidence, _payload(row), _payload(normalized)],
        )
    logger.info("Staged source | batch=%d | sequence=%d | source=%s | rows=%d",
                batch_id, file_sequence, source_type, len(data))
    return staged_file_id


def _is_trade(value: str | None) -> bool:
    text = str(value or "").upper()
    return "BUY" in text or "SELL" in text


def resolve_batch(
    batch_id: int,
    db_path: Path | str = DATABASE_PATH,
    staged_file_ids: list[int] | None = None,
) -> dict[int, str]:
    """Resolve staged symbols using the shared FX-based correction pipeline."""
    connection = get_shared_connection(db_path)
    params: list[Any] = [batch_id]
    staged_filter = ""
    if staged_file_ids:
        placeholders = ",".join("?" for _ in staged_file_ids)
        staged_filter = f" AND f.staged_file_id IN ({placeholders})"
        params.extend(staged_file_ids)
    rows = connection.execute(
        """
        SELECT r.staged_record_id, r.source_symbol, r.security_name, r.transaction_date,
               r.transaction_type, r.fx_rate, r.contains_fx_rate, f.source_type,
               f.staged_file_id,
               r.inferred_listing_currency, r.listing_evidence
        FROM staged_records r JOIN staged_files f USING (staged_file_id)
        WHERE f.batch_id = ? AND r.source_symbol IS NOT NULL
          AND r.resolution_status <> 'resolved'
          {staged_filter}
        ORDER BY f.file_sequence, r.record_sequence
        """.format(staged_filter=staged_filter), params
    ).fetchall()
    resolutions: dict[int, str] = {}
    grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
    for row in rows:
        symbol = str(row[1] or "").strip().upper()
        currency = listing_currency_from_fx(row[6])
        grouped.setdefault((symbol, currency), []).append(row)

    resolutions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for (symbol, currency), evidence_rows in grouped.items():
        primary_name = next((str(row[2]) for row in evidence_rows if row[2]), "")
        result = resolve_or_enrich_ticker(
            symbol,
            currency == "USD",
            primary_name,
            evidence_rows[0][7],
            db_path,
        )
        method = (
            f"{evidence_rows[0][7]}_{'fx' if evidence_rows[0][6] == 'Yes' else 'no_fx'}"
            if result.ticker_id is not None else "unresolved"
        )
        resolutions_by_key[(symbol, currency)] = {
            "ticker_id": result.ticker_id,
            "symbol": result.resolved_symbol,
            "currency": result.listing_currency,
            "method": method,
            "status": "resolved" if result.ticker_id is not None else "unresolved",
        }
        if result.ticker_id is not None:
            logger.info(
                "Staged ticker resolved | symbol=%s | trading_currency=%s | method=%s | ticker_id=%d",
                result.resolved_symbol,
                result.listing_currency,
                method,
                result.ticker_id,
            )

    for row in rows:
        symbol = str(row[1] or "").strip().upper()
        currency = listing_currency_from_fx(row[6])
        resolution = resolutions_by_key.get((symbol, currency))
        ticker_id = resolution["ticker_id"] if resolution else None
        status = resolution["status"] if resolution else "unresolved"
        method = resolution["method"] if resolution else "unresolved"
        evidence = row[10] or (f"{row[7]}_{'fx' if row[6] == 'Yes' else 'no_fx'}" if row[7] == "statement" else None)
        connection.execute(
            """
            UPDATE staged_records SET ticker_id = ?, resolution_method = ?, resolution_status = ?,
                inferred_listing_currency = COALESCE(inferred_listing_currency, ?),
                listing_evidence = COALESCE(listing_evidence, ?),
                contains_fx_rate = COALESCE(contains_fx_rate, ?)
            WHERE staged_record_id = ?
            """,
            [
                ticker_id,
                method,
                status,
                currency,
                evidence,
                row[6],
                row[0],
            ],
        )
        resolutions[int(row[0])] = status

    connection.execute("UPDATE ingestion_batches SET status = 'resolved' WHERE batch_id = ?", [batch_id])
    return resolutions


def mark_file(staged_file_id: int, status: str, error: str | None = None,
              db_path: Path | str = DATABASE_PATH) -> None:
    get_shared_connection(db_path).execute(
        """
        UPDATE staged_files SET status = ?, error_message = ?,
            published_at = CASE WHEN ? = 'published' THEN now() ELSE published_at END
        WHERE staged_file_id = ?
        """, [status, error, status, staged_file_id]
    )


def complete_batch(batch_id: int, db_path: Path | str = DATABASE_PATH) -> None:
    connection = get_shared_connection(db_path)
    failures = connection.execute(
        "SELECT COUNT(*) FROM staged_files WHERE batch_id = ? AND status = 'quarantined'", [batch_id]
    ).fetchone()[0]
    connection.execute(
        "UPDATE ingestion_batches SET status = ?, completed_at = now() WHERE batch_id = ?",
        ["partial" if failures else "completed", batch_id],
    )
