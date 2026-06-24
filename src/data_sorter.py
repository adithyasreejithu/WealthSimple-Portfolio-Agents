"""
Import Wealthsimple activity exports into raw and normalized DuckDB tables.

Every source field is retained in the raw import table. Analytics rows use
normalized column names and reference securities through `ticker_id`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from config import (
    ACTIVITY_EXPORT_COLUMNS,
    ACTIVITY_TYPE_MAPPING,
    COLUMN_RENAMES,
    DATABASE_PATH,
    DATA_FOLDER,
    DROP_COLUMNS,
    PROCESSED_FOLDER_NAME,
    REQUIRED_COLUMNS,
    SOURCE_PREFIX,
)
from database import get_shared_connection, initialize_database
from system_logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SortResult:
    source_file: Path
    import_id: int
    status: str
    rows_written: int
    dataframe: pd.DataFrame
    raw_dataframe: pd.DataFrame
    unknown_dataframe: pd.DataFrame
    unresolved_dataframe: pd.DataFrame
    duplicate_rows: int
    duplicate_file: bool
    processed_path: Path | None


def find_latest_source_file(data_dir: Path) -> Path:
    logger.debug("Searching for source CSV files in %s", data_dir)
    candidates = sorted(
        data_dir.glob(f"{SOURCE_PREFIX}*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No source CSV found in {data_dir} matching {SOURCE_PREFIX}*.csv"
        )
    logger.info("Selected newest source file: %s", candidates[0].name)
    return candidates[0]


def ensure_csv_file(path: Path) -> None:
    if path.suffix.lower() != ".csv":
        logger.error("Rejected non-CSV input file: %s", path)
        raise ValueError(f"Input file must be a CSV: {path}")


def is_data_row(row: dict[str, str]) -> bool:
    transaction_date = (row.get("transaction_date") or "").strip()
    return bool(transaction_date) and not transaction_date.startswith("As of")


def trim_trailing_footer_rows(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    end = len(rows)
    while end > 0 and not is_data_row(rows[end - 1]):
        end -= 1
    if end != len(rows):
        logger.info("Trimmed %d trailing footer rows", len(rows) - end)
    return rows[:end]


def normalize_activity_type(
    activity_type: str,
    activity_sub_type: str,
) -> str:
    normalized = (activity_type or "").strip()
    subtype = (activity_sub_type or "").strip()

    if normalized in ACTIVITY_TYPE_MAPPING:
        return ACTIVITY_TYPE_MAPPING[normalized]
    if normalized in {"Trade", "CorporateAction"}:
        return subtype or "UNKNOWN"
    return "UNKNOWN"


def title_case_column(column: str) -> str:
    """Return the configured analytics name for a source column."""
    return COLUMN_RENAMES.get(column, column)


def clean_rows(
    rows: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Validate and preserve source rows while identifying unknown activities."""
    if not rows:
        logger.error("CSV contained no data rows after loading")
        raise ValueError("CSV contains no data rows")

    source_columns = list(rows[0].keys())
    missing = REQUIRED_COLUMNS - set(source_columns)
    if missing:
        logger.error("CSV missing required columns: %s", ", ".join(sorted(missing)))
        raise ValueError(
            f"CSV is missing required columns: {', '.join(sorted(missing))}"
        )

    output_columns = [
        title_case_column(column)
        for column in source_columns
        if column not in DROP_COLUMNS
    ]
    cleaned: list[dict[str, str]] = []
    unknown_rows: list[dict[str, str]] = []

    for row in rows:
        cleaned_row = {
            title_case_column(column): (row.get(column) or "").strip()
            for column in source_columns
            if column not in DROP_COLUMNS
        }
        cleaned.append(cleaned_row)
        if normalize_activity_type(
            row.get("activity_type", ""),
            row.get("activity_sub_type", ""),
        ) == "UNKNOWN":
            unknown_rows.append(cleaned_row)

    logger.info(
        "Validated rows: %d, unknown activity rows: %d",
        len(cleaned),
        len(unknown_rows),
    )
    return output_columns, cleaned, unknown_rows


def _read_source_rows(source_file: Path) -> list[dict[str, str]]:
    with source_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}"
            )
        rows = [
            {
                column: (row.get(column) or "").strip()
                for column in ACTIVITY_EXPORT_COLUMNS
            }
            for row in reader
        ]
    return trim_trailing_footer_rows(rows)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(values: list[Any]) -> str:
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _parse_date(value: str, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def _parse_decimal(value: str, field_name: str) -> Decimal | None:
    if not value:
        return None
    normalized = value.replace(",", "").replace("$", "").strip()
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def _ticker_candidates(connection: Any) -> dict[str, list[dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT ticker_id, ticker_symbol, exchange, currency, security_name
        FROM tickers
        """
    ).fetchall()
    candidates: dict[str, list[dict[str, Any]]] = {}
    for ticker_id, symbol, exchange, currency, security_name in rows:
        candidates.setdefault(symbol.strip().upper(), []).append(
            {
                "ticker_id": ticker_id,
                "exchange": exchange,
                "currency": currency,
                "security_name": security_name,
            }
        )
    return candidates


def _resolve_ticker_id(
    row: dict[str, str],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[int | None, str | None]:
    symbol = row["symbol"].strip().upper()
    if not symbol:
        return None, None

    matches = candidates.get(symbol, [])
    if not matches:
        return None, f"No ticker record matches symbol {symbol}"
    if len(matches) == 1:
        return int(matches[0]["ticker_id"]), None

    source_name = _normalize_name(row["name"])
    if source_name:
        name_matches = [
            candidate
            for candidate in matches
            if _normalize_name(candidate["security_name"]) == source_name
        ]
        if len(name_matches) == 1:
            return int(name_matches[0]["ticker_id"]), None
        if name_matches:
            matches = name_matches

    source_currency = row["currency"].strip().upper()
    if source_currency:
        currency_matches = [
            candidate
            for candidate in matches
            if candidate["currency"].strip().upper() == source_currency
        ]
        if len(currency_matches) == 1:
            return int(currency_matches[0]["ticker_id"]), None

    exchanges = ", ".join(sorted(candidate["exchange"] for candidate in matches))
    return None, f"Symbol {symbol} is ambiguous across exchanges: {exchanges}"


def _raw_rows_with_metadata(
    rows: list[dict[str, str]],
) -> list[tuple[Any, ...]]:
    fingerprints = [
        _fingerprint([row[column] for column in ACTIVITY_EXPORT_COLUMNS])
        for row in rows
    ]
    seen: Counter[str] = Counter()
    prepared = []
    for source_row_number, (row, row_fingerprint) in enumerate(
        zip(rows, fingerprints),
        start=2,
    ):
        seen[row_fingerprint] += 1
        prepared.append(
            (
                source_row_number,
                *[row[column] or None for column in ACTIVITY_EXPORT_COLUMNS],
                row_fingerprint,
                seen[row_fingerprint],
            )
        )
    return prepared


def _normalize_row(
    row: dict[str, str],
    ticker_id: int | None,
) -> dict[str, Any]:
    normalized = {
        "transaction_date": _parse_date(
            row["transaction_date"],
            "transaction_date",
        ),
        "settlement_date": _parse_date(
            row["settlement_date"],
            "settlement_date",
        ),
        "account_id": row["account_id"],
        "account_type": row["account_type"],
        "activity_type": row["activity_type"] or None,
        "activity_subtype": row["activity_sub_type"] or None,
        "activity_code": normalize_activity_type(
            row["activity_type"],
            row["activity_sub_type"],
        ),
        "direction": row["direction"] or None,
        "ticker_id": ticker_id,
        "transaction_currency": row["currency"] or None,
        "quantity": _parse_decimal(row["quantity"], "quantity"),
        "unit_price": _parse_decimal(row["unit_price"], "unit_price"),
        "commission_amount": _parse_decimal(row["commission"], "commission"),
        "net_cash_amount": _parse_decimal(
            row["net_cash_amount"],
            "net_cash_amount",
        ),
    }
    normalized["row_fingerprint"] = _fingerprint(
        [normalized[key] for key in normalized]
    )
    return normalized


def _insert_raw_rows(
    connection: Any,
    import_id: int,
    rows: list[dict[str, str]],
) -> None:
    prepared = _raw_rows_with_metadata(rows)
    connection.executemany(
        """
        INSERT INTO raw_activity_exports (
            import_id,
            source_row_number,
            transaction_date,
            settlement_date,
            account_id,
            account_type,
            activity_type,
            activity_sub_type,
            direction,
            symbol,
            name,
            currency,
            quantity,
            unit_price,
            commission,
            net_cash_amount,
            row_fingerprint,
            duplicate_ordinal
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(import_id, *values) for values in prepared],
    )


def _publish_activities(
    connection: Any,
    import_id: int,
    normalized_rows: list[dict[str, Any]],
) -> int:
    seen: Counter[str] = Counter()
    duplicate_count = 0

    for row in normalized_rows:
        row_fingerprint = row["row_fingerprint"]
        seen[row_fingerprint] += 1
        duplicate_ordinal = seen[row_fingerprint]
        existing = connection.execute(
            """
            SELECT activity_id
            FROM activities
            WHERE row_fingerprint = ?
              AND duplicate_ordinal = ?
            """,
            [row_fingerprint, duplicate_ordinal],
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE activities
                SET last_seen_import_id = ?
                WHERE activity_id = ?
                """,
                [import_id, existing[0]],
            )
            duplicate_count += 1
            continue

        connection.execute(
            """
            INSERT INTO activities (
                transaction_date,
                settlement_date,
                account_id,
                account_type,
                activity_type,
                activity_subtype,
                activity_code,
                direction,
                ticker_id,
                transaction_currency,
                quantity,
                unit_price,
                commission_amount,
                net_cash_amount,
                row_fingerprint,
                duplicate_ordinal,
                first_seen_import_id,
                last_seen_import_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["transaction_date"],
                row["settlement_date"],
                row["account_id"],
                row["account_type"],
                row["activity_type"],
                row["activity_subtype"],
                row["activity_code"],
                row["direction"],
                row["ticker_id"],
                row["transaction_currency"],
                row["quantity"],
                row["unit_price"],
                row["commission_amount"],
                row["net_cash_amount"],
                row_fingerprint,
                duplicate_ordinal,
                import_id,
                import_id,
            ],
        )
    return duplicate_count


def move_to_processed_folder(
    source_file: Path,
    processed_dir: Path,
) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    destination = processed_dir / source_file.name
    logger.debug(
        "Preparing to move %s to processed folder %s",
        source_file,
        destination,
    )

    if destination.exists():
        if _hash_file(destination) == _hash_file(source_file):
            source_file.unlink()
            logger.info(
                "Removed duplicate source file already archived at %s",
                destination,
            )
            return destination
        raise FileExistsError(
            f"Processed destination already exists with different content: {destination}"
        )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            source_file.rename(destination)
            logger.info("Moved source file to %s", destination)
            return destination
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Move attempt %d failed for %s",
                attempt + 1,
                source_file,
            )

    logger.exception(
        "Failed to move %s to %s after 3 attempts",
        source_file,
        destination,
    )
    raise RuntimeError(
        f"Failed to move {source_file} to {destination} after 3 attempts"
    ) from last_error


def sort_data(
    source_file: Path | None = None,
    data_dir: Path | None = None,
    db_path: Path | str = DATABASE_PATH,
) -> SortResult:
    """Import one full activity export and archive it after successful publication."""
    base_dir = Path(__file__).resolve().parents[1]
    resolved_data_dir = data_dir or DATA_FOLDER
    if source_file is None:
        source_file = find_latest_source_file(resolved_data_dir)
    else:
        source_file = (
            source_file
            if source_file.is_absolute()
            else (base_dir / source_file)
        )

    ensure_csv_file(source_file)
    rows = _read_source_rows(source_file)
    if not rows:
        raise ValueError("CSV contains no data rows")

    file_hash = _hash_file(source_file)
    raw_dataframe = pd.DataFrame(rows, columns=ACTIVITY_EXPORT_COLUMNS)
    unknown_dataframe = raw_dataframe[
        raw_dataframe.apply(
            lambda row: normalize_activity_type(
                str(row["activity_type"]),
                str(row["activity_sub_type"]),
            )
            == "UNKNOWN",
            axis=1,
        )
    ].reset_index(drop=True)

    initialize_database(db_path)
    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        existing_import = connection.execute(
            """
            SELECT import_id, status
            FROM activity_imports
            WHERE file_hash = ?
            """,
            [file_hash],
        ).fetchone()
        duplicate_file = existing_import is not None

        if existing_import and existing_import[1] == "succeeded":
            connection.execute("COMMIT")
            processed_path = move_to_processed_folder(
                source_file,
                resolved_data_dir / PROCESSED_FOLDER_NAME,
            )
            return SortResult(
                source_file=source_file,
                import_id=int(existing_import[0]),
                status="succeeded",
                rows_written=0,
                dataframe=pd.DataFrame(),
                raw_dataframe=raw_dataframe,
                unknown_dataframe=unknown_dataframe,
                unresolved_dataframe=pd.DataFrame(),
                duplicate_rows=len(rows),
                duplicate_file=True,
                processed_path=processed_path,
            )

        if existing_import:
            import_id = int(existing_import[0])
            connection.execute(
                """
                UPDATE activity_imports
                SET source_file = ?,
                    imported_at = CURRENT_TIMESTAMP,
                    status = 'pending',
                    unresolved_ticker_count = 0,
                    error_message = NULL
                WHERE import_id = ?
                """,
                [source_file.name, import_id],
            )
        else:
            import_id = int(
                connection.execute(
                    """
                    INSERT INTO activity_imports (
                        source_file,
                        file_hash,
                        status,
                        source_row_count
                    )
                    VALUES (?, ?, 'pending', ?)
                    RETURNING import_id
                    """,
                    [source_file.name, file_hash, len(rows)],
                ).fetchone()[0]
            )
            _insert_raw_rows(connection, import_id, rows)

        candidates = _ticker_candidates(connection)
        normalized_rows: list[dict[str, Any]] = []
        unresolved_rows: list[dict[str, Any]] = []
        for source_row_number, row in enumerate(rows, start=2):
            ticker_id, resolution_error = _resolve_ticker_id(row, candidates)
            if resolution_error:
                unresolved_rows.append(
                    {
                        "source_row_number": source_row_number,
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "currency": row["currency"],
                        "resolution_error": resolution_error,
                    }
                )
                continue
            normalized_rows.append(_normalize_row(row, ticker_id))

        unresolved_dataframe = pd.DataFrame(unresolved_rows)
        if unresolved_rows:
            connection.execute(
                """
                UPDATE activity_imports
                SET status = 'rejected',
                    unresolved_ticker_count = ?,
                    normalized_row_count = 0,
                    duplicate_row_count = 0,
                    error_message = ?
                WHERE import_id = ?
                """,
                [
                    len(unresolved_rows),
                    "Ticker resolution failed",
                    import_id,
                ],
            )
            connection.execute("COMMIT")
            logger.warning(
                "Activity import rejected | import_id=%d | unresolved=%d",
                import_id,
                len(unresolved_rows),
            )
            return SortResult(
                source_file=source_file,
                import_id=import_id,
                status="rejected",
                rows_written=0,
                dataframe=pd.DataFrame(normalized_rows),
                raw_dataframe=raw_dataframe,
                unknown_dataframe=unknown_dataframe,
                unresolved_dataframe=unresolved_dataframe,
                duplicate_rows=0,
                duplicate_file=duplicate_file,
                processed_path=None,
            )

        duplicate_rows = _publish_activities(
            connection,
            import_id,
            normalized_rows,
        )
        connection.execute(
            """
            UPDATE activity_imports
            SET status = 'succeeded',
                normalized_row_count = ?,
                unresolved_ticker_count = 0,
                duplicate_row_count = ?,
                error_message = NULL
            WHERE import_id = ?
            """,
            [len(normalized_rows), duplicate_rows, import_id],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        logger.exception("Activity import failed for %s", source_file)
        raise

    processed_path = move_to_processed_folder(
        source_file,
        resolved_data_dir / PROCESSED_FOLDER_NAME,
    )
    dataframe = pd.DataFrame(normalized_rows)
    logger.info(
        "Activity import succeeded | import_id=%d | rows=%d | duplicates=%d",
        import_id,
        len(normalized_rows),
        duplicate_rows,
    )
    return SortResult(
        source_file=source_file,
        import_id=import_id,
        status="succeeded",
        rows_written=len(normalized_rows),
        dataframe=dataframe,
        raw_dataframe=raw_dataframe,
        unknown_dataframe=unknown_dataframe,
        unresolved_dataframe=pd.DataFrame(),
        duplicate_rows=duplicate_rows,
        duplicate_file=duplicate_file,
        processed_path=processed_path,
    )


def main() -> None:
    try:
        result = sort_data()
        print(f"Import status: {result.status}")
        print(f"Import ID: {result.import_id}")
        print(f"Normalized rows: {result.rows_written}")
        print(f"Duplicate rows: {result.duplicate_rows}")
        if not result.unresolved_dataframe.empty:
            print("\nUnresolved tickers:")
            print(result.unresolved_dataframe.to_string(index=False))
    except Exception:
        logger.exception("Sorter failed")
        raise


if __name__ == "__main__":
    main()
