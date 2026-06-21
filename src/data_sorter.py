"""
Data sorting utility for WealthSimple Portfolio Agents.

This module ingests a WealthSimple activities CSV, normalizes the rows for
downstream DuckDB loading, and moves the processed source file into
`processed_data/`.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


SOURCE_PREFIX = "activities-export-"
OUTPUT_FILENAME = "cleaned_activities.csv"
PROCESSED_FOLDER_NAME = "processed_data"

REQUIRED_COLUMNS = {
    "transaction_date",
    "settlement_date",
    "account_id",
    "account_type",
    "activity_type",
    "activity_sub_type",
    "direction",
    "symbol",
    "name",
    "currency",
    "quantity",
    "unit_price",
    "commission",
    "net_cash_amount",
}

DROP_COLUMNS = {"account_id", "account_type", "activity_sub_type", "direction", "symbol", "name"}


@dataclass(frozen=True)
class SortResult:
    source_file: Path
    output_file: Path
    rows_written: int
    dataframe: pd.DataFrame
    unknown_dataframe: pd.DataFrame


def find_latest_source_file(data_dir: Path) -> Path:
    # Pick the newest WealthSimple export so repeated runs always use the latest file.
    candidates = sorted(data_dir.glob(f"{SOURCE_PREFIX}*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No source CSV found in {data_dir} matching {SOURCE_PREFIX}*.csv")
    return candidates[0]


def ensure_csv_file(path: Path) -> None:
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Input file must be a CSV: {path}")


def is_data_row(row: dict[str, str]) -> bool:
    # Footer lines in the export do not contain a transaction date, so they are not data rows.
    transaction_date = (row.get("transaction_date") or "").strip()
    return bool(transaction_date) and not transaction_date.startswith("As of")


def trim_trailing_footer_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    # Remove trailing non-data footer rows without affecting legitimate records in the middle.
    end = len(rows)
    while end > 0 and not is_data_row(rows[end - 1]):
        end -= 1
    return rows[:end]


def normalize_activity_type(activity_type: str, activity_sub_type: str) -> str:
    # Normalize WealthSimple activity labels into the compact codes expected downstream.
    normalized = (activity_type or "").strip()
    subtype = (activity_sub_type or "").strip()

    if normalized == "MoneyMovement":
        return "CONT"
    if normalized == "Trade":
        return subtype or "UNKNOWN"
    if normalized == "Dividend":
        return "DIV"
    if normalized == "CorporateAction":
        return subtype or "UNKNOWN"
    if normalized == "Interest":
        return "INT"
    return "UNKNOWN"


def title_case_column(column: str) -> str:
    mapping = {
        "transaction_date": "Transaction Date",
        "settlement_date": "Settlement Date",
        "activity_type": "Activity Type",
        "currency": "Currency",
        "quantity": "Quantity",
        "unit_price": "Unit Price",
        "commission": "Commission",
        "net_cash_amount": "Net Cash Amount",
    }
    return mapping.get(column, column)


def cleaned_columns(source_columns: Iterable[str]) -> list[str]:
    ordered = []
    for column in source_columns:
        if column in DROP_COLUMNS:
            continue
        if column == "activity_type":
            ordered.append(title_case_column(column))
            continue
        ordered.append(title_case_column(column))
    return ordered


def clean_rows(rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    if not rows:
        raise ValueError("CSV contains no data rows")

    source_columns = list(rows[0].keys())
    missing = REQUIRED_COLUMNS - set(source_columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

    output_columns = cleaned_columns(source_columns)
    cleaned: list[dict[str, str]] = []
    unknown_rows: list[dict[str, str]] = []

    for row in rows:
        activity_value = normalize_activity_type(row.get("activity_type", ""), row.get("activity_sub_type", ""))
        cleaned_row: dict[str, str] = {}

        for column in source_columns:
            if column in DROP_COLUMNS:
                continue
            # Keep the useful fields in their original order, but present them with normalized names.
            output_name = title_case_column(column)
            if column == "activity_type":
                cleaned_row[output_name] = activity_value
            else:
                cleaned_row[output_name] = row.get(column, "")

        if activity_value == "UNKNOWN":
            unknown_rows.append(cleaned_row)
            continue

        cleaned.append(cleaned_row)

    return output_columns, cleaned, unknown_rows


def write_cleaned_csv(output_file: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def move_to_processed_folder(source_file: Path, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    destination = processed_dir / source_file.name
    if destination.exists():
        # Keep the original filename; only clear a stale same-name file in the processed folder.
        last_delete_error: Exception | None = None
        for _ in range(3):
            try:
                destination.unlink()
                break
            except Exception as exc:  # noqa: BLE001 - retry removal before failing the move
                last_delete_error = exc
        else:
            raise RuntimeError(f"Failed to clear existing destination {destination}") from last_delete_error

    last_error: Exception | None = None
    for _ in range(3):
        try:
            shutil.move(str(source_file), str(destination))
            return destination
        except Exception as exc:  # noqa: BLE001 - surface final failure after retries
            last_error = exc

    raise RuntimeError(f"Failed to move {source_file} to {destination} after 3 attempts") from last_error


def sort_data(source_file: Path | None = None, data_dir: Path | None = None) -> SortResult:
    base_dir = Path(__file__).resolve().parents[1]
    resolved_data_dir = data_dir or (base_dir / "Data")

    if source_file is None:
        source_file = find_latest_source_file(resolved_data_dir)
    else:
        source_file = source_file if source_file.is_absolute() else (base_dir / source_file)

    ensure_csv_file(source_file)

    with source_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    rows = trim_trailing_footer_rows(rows)
    columns, cleaned_rows, unknown_rows = clean_rows(rows)
    output_file = resolved_data_dir / OUTPUT_FILENAME
    # Write the cleaned file first so the transformed data is preserved even if the move fails.
    write_cleaned_csv(output_file, columns, cleaned_rows)
    dataframe = pd.DataFrame(cleaned_rows, columns=columns)
    unknown_dataframe = pd.DataFrame(unknown_rows, columns=columns)

    move_to_processed_folder(source_file, resolved_data_dir / PROCESSED_FOLDER_NAME)
    return SortResult(
        source_file=source_file,
        output_file=output_file,
        rows_written=len(cleaned_rows),
        dataframe=dataframe,
        unknown_dataframe=unknown_dataframe,
    )


def main() -> None:
    result = sort_data()
    # Print the cleaned dataframe for quick review; unknown rows are shown separately when present.
    print("Cleaned dataframe:")
    print(result.dataframe.to_string(index=False))
    if not result.unknown_dataframe.empty:
        print("\nUnknown dataframe:")
        print(result.unknown_dataframe.to_string(index=False))


if __name__ == "__main__":
    main()
