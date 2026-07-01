"""End-to-end Wealthsimple data pipeline orchestrator."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from config import DATABASE_PATH, DATA_FOLDER, PROCESSED_FOLDER_NAME, SOURCE_PREFIX
from data_sorter import move_to_processed_folder, sort_data
from database import get_shared_connection, initialize_database
from database_command import (
    get_email_checkpoint,
    update_email_checkpoint,
    upload_email_transactions,
    upload_statement_transactions,
)
from analytics import portfolio_report
from email_extractor import fetch_email_transactions
from statement_extractor import extract_statement_pdf
from staging import (
    complete_batch, create_batch, mark_file, resolve_batch,
    stage_dataframe,
)
from system_logger import get_logger


logger = get_logger(__name__)
SUPPORTED_DATA_SUFFIXES = frozenset({".csv", ".pdf", ".xlsx", ".xls"})


@dataclass(frozen=True)
class SourceResult:
    source: str
    source_file: Path | None
    status: str
    rows: int = 0
    error: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    results: tuple[SourceResult, ...]

    @property
    def succeeded(self) -> bool:
        return all(result.status in {"succeeded", "skipped"} for result in self.results)


def check_data_files(data_dir: Path | str = DATA_FOLDER) -> list[Path]:
    """Return supported top-level files waiting for pipeline inspection."""
    folder = Path(data_dir)
    files = (
        sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_DATA_SUFFIXES
        )
        if folder.exists()
        else []
    )
    logger.info("Found %d data file(s) requiring inspection", len(files))
    logger.info(
        "Data file counts | exports=%d | statements=%d | ignored_excel=%d",
        sum(path.name.startswith(SOURCE_PREFIX) and path.suffix.lower() == ".csv" for path in files),
        sum(path.suffix.lower() == ".pdf" for path in files),
        sum(path.suffix.lower() in {".xlsx", ".xls"} for path in files),
    )
    return files


def rename_monthly_documents(data_dir: Path | str = DATA_FOLDER) -> list[Path]:
    """Rename monthly PDF statements to YYYY-MM.pdf without overwriting files."""
    files = check_data_files(data_dir)
    logger.info("Checking %d data file(s) for Wealthsimple naming cleanup", len(files))
    renamed: list[Path] = []
    for file in files:
        if file.suffix.lower() != ".pdf":
            continue
        match = re.search(r"\d{4}-\d{2}", file.name)
        if not match:
            logger.warning("No YYYY-MM found in %s", file.name)
            continue
        year_month = match.group()
        logger.info("Found new file with WS naming convention %s", year_month)
        new_path = file.with_name(f"{year_month}.pdf")
        if new_path == file or new_path.exists():
            logger.info("File already has target name or target exists: %s", new_path.name)
            continue
        logger.info("Renaming %s to %s", file.name, new_path.name)
        file.rename(new_path)
        renamed.append(new_path)
    return renamed


def _archive_dir(data_dir: Path | str, source: str) -> Path:
    return Path(data_dir) / PROCESSED_FOLDER_NAME / source


def _staged_ticker_ids(staged_file_id: int, db_path: Path | str) -> list[int | None]:
    connection = get_shared_connection(db_path)
    rows = connection.execute(
        """
        SELECT ticker_id
        FROM staged_records
        WHERE staged_file_id = ?
        ORDER BY record_sequence
        """,
        [staged_file_id],
    ).fetchall()
    return [row[0] if row[0] is None else int(row[0]) for row in rows]


def _publish_statement_file(
    staged_file_id: int,
    file: Path,
    data: pd.DataFrame,
    data_dir: Path | str,
    db_path: Path | str,
) -> int:
    prepared = data.copy()
    prepared["ticker_id"] = _staged_ticker_ids(staged_file_id, db_path)
    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        rows = upload_statement_transactions(prepared, db_path)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    move_to_processed_folder(file, _archive_dir(data_dir, "statements"))
    mark_file(staged_file_id, "published", db_path=db_path)
    return rows


def _publish_email_batch(
    staged_file_id: int,
    data: pd.DataFrame,
    db_path: Path | str,
) -> int:
    prepared = data.copy()
    prepared["ticker_id"] = _staged_ticker_ids(staged_file_id, db_path)
    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        rows = upload_email_transactions(prepared, db_path)
        update_email_checkpoint(date.today(), rows, db_path)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    mark_file(staged_file_id, "published", db_path=db_path)
    return rows


def _stage_statement_files(
    batch_id: int,
    data_dir: Path | str,
    db_path: Path | str,
    sequence_start: int,
) -> tuple[list[tuple[int, Path, pd.DataFrame]], int, list[SourceResult]]:
    rename_monthly_documents(data_dir)
    files = sorted(Path(data_dir).glob("*.pdf"), key=lambda path: path.name)
    staged: list[tuple[int, Path, pd.DataFrame]] = []
    results: list[SourceResult] = []
    sequence = sequence_start
    for file in files:
        try:
            data = extract_statement_pdf(file)
            staged_id = stage_dataframe(batch_id, "statement", file, sequence, data, db_path)
            staged.append((staged_id, file, data))
        except Exception as exc:
            staged_id = stage_dataframe(batch_id, "statement", file, sequence, pd.DataFrame(), db_path)
            mark_file(staged_id, "quarantined", str(exc), db_path)
            results.append(SourceResult("statement", file, "failed", error=str(exc)))
        sequence += 1
    return staged, sequence, results


def _stage_email_batch(
    batch_id: int,
    db_path: Path | str,
    sequence: int,
) -> tuple[list[tuple[int, pd.DataFrame]], int, list[SourceResult]]:
    results: list[SourceResult] = []
    try:
        data = fetch_email_transactions(start_date=get_email_checkpoint(db_path))
        staged_id = stage_dataframe(batch_id, "email", None, sequence, data, db_path)
        return [(staged_id, data)], sequence + 1, results
    except Exception as exc:
        staged_id = stage_dataframe(batch_id, "email", None, sequence, pd.DataFrame(), db_path)
        mark_file(staged_id, "quarantined", str(exc), db_path)
        results.append(SourceResult("email", None, "failed", error=str(exc)))
        return [], sequence + 1, results


def _stage_export_files(
    batch_id: int,
    data_dir: Path | str,
    db_path: Path | str,
    sequence_start: int,
) -> tuple[list[tuple[int, Path, pd.DataFrame]], int, list[SourceResult]]:
    files = sorted(
        (
            path for path in check_data_files(data_dir)
            if path.name.startswith(SOURCE_PREFIX) and path.suffix.lower() == ".csv"
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    staged: list[tuple[int, Path, pd.DataFrame]] = []
    results: list[SourceResult] = []
    sequence = sequence_start
    for file in files:
        try:
            data = pd.read_csv(file, dtype=str, keep_default_na=False)
            staged_id = stage_dataframe(batch_id, "export", file, sequence, data, db_path)
            staged.append((staged_id, file, data))
        except Exception as exc:
            staged_id = stage_dataframe(batch_id, "export", file, sequence, pd.DataFrame(), db_path)
            mark_file(staged_id, "quarantined", str(exc), db_path)
            results.append(SourceResult("export", file, "failed", error=str(exc)))
        sequence += 1
    return staged, sequence, results


def run_full_exports(
    data_dir: Path | str = DATA_FOLDER,
    db_path: Path | str = DATABASE_PATH,
) -> list[SourceResult]:
    """Process every pending full export oldest-first."""
    initialize_database(db_path)
    batch_id = create_batch(db_path)
    staged, _, staging_results = _stage_export_files(batch_id, data_dir, db_path, 1)
    results = list(staging_results)
    if not staged:
        results.append(SourceResult("export", None, "skipped"))
        complete_batch(batch_id, db_path)
        return results

    resolve_batch(batch_id, db_path, [staged_file_id for staged_file_id, _, _ in staged])
    for staged_file_id, file, _data in staged:
        unresolved_rows = get_shared_connection(db_path).execute(
            """
            SELECT DISTINCT source_symbol
            FROM staged_records
            WHERE staged_file_id = ? AND source_symbol IS NOT NULL
              AND resolution_status <> 'resolved'
            ORDER BY source_symbol
            """,
            [staged_file_id],
        ).fetchall()
        if unresolved_rows:
            error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
            mark_file(staged_file_id, "quarantined", error, db_path)
            results.append(SourceResult("export", file, "failed", error=error))
            continue
        try:
            imported = sort_data(
                source_file=file,
                data_dir=Path(data_dir),
                db_path=db_path,
                enrich_tickers=False,
                processed_dir=_archive_dir(data_dir, "full_exports"),
            )
            mark_file(staged_file_id, "published", db_path=db_path)
            results.append(
                SourceResult(
                    "export",
                    file,
                    imported.status,
                    imported.rows_written,
                    None if imported.status == "succeeded" else "Ticker resolution failed",
                )
            )
        except Exception as exc:
            logger.exception("Full export pipeline failed | file=%s", file)
            mark_file(staged_file_id, "quarantined", str(exc), db_path)
            results.append(SourceResult("export", file, "failed", error=str(exc)))
    complete_batch(batch_id, db_path)
    return results


def run_statements(
    data_dir: Path | str = DATA_FOLDER,
    db_path: Path | str = DATABASE_PATH,
) -> list[SourceResult]:
    """Extract, normalize, upload, and archive each statement independently."""
    initialize_database(db_path)
    batch_id = create_batch(db_path)
    staged, _, staging_results = _stage_statement_files(batch_id, data_dir, db_path, 1)
    results = list(staging_results)
    if not staged:
        results.append(SourceResult("statements", None, "skipped"))
        complete_batch(batch_id, db_path)
        return results

    resolve_batch(batch_id, db_path, [staged_file_id for staged_file_id, _, _ in staged])
    for staged_file_id, file, data in staged:
        unresolved_rows = get_shared_connection(db_path).execute(
            """
            SELECT DISTINCT source_symbol
            FROM staged_records
            WHERE staged_file_id = ? AND source_symbol IS NOT NULL
              AND resolution_status <> 'resolved'
            ORDER BY source_symbol
            """,
            [staged_file_id],
        ).fetchall()
        if unresolved_rows:
            error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
            mark_file(staged_file_id, "quarantined", error, db_path)
            logger.error(
                "Source quarantined and not published | source=%s | file=%s | tickers=%s",
                "statement", file, ",".join(symbol for (symbol,) in unresolved_rows),
            )
            results.append(SourceResult("statement", file, "failed", error=error))
            continue
        try:
            rows = _publish_statement_file(staged_file_id, file, data, data_dir, db_path)
            results.append(SourceResult("statement", file, "succeeded", rows))
        except Exception as exc:
            logger.exception("Statement pipeline failed | file=%s", file)
            mark_file(staged_file_id, "quarantined", str(exc), db_path)
            results.append(SourceResult("statement", file, "failed", error=str(exc)))
    complete_batch(batch_id, db_path)
    return results


def run_email(
    db_path: Path | str = DATABASE_PATH,
    fetcher: Callable[..., pd.DataFrame] = fetch_email_transactions,
) -> SourceResult:
    """Fetch email from the database checkpoint and commit rows with the checkpoint."""
    initialize_database(db_path)
    batch_id = create_batch(db_path)
    try:
        data = fetcher(start_date=get_email_checkpoint(db_path))
    except Exception as exc:
        logger.exception("Email pipeline failed")
        staged_id = stage_dataframe(batch_id, "email", None, 1, pd.DataFrame(), db_path)
        mark_file(staged_id, "quarantined", str(exc), db_path)
        complete_batch(batch_id, db_path)
        return SourceResult("email", None, "failed", error=str(exc))

    staged_id = stage_dataframe(batch_id, "email", None, 1, data, db_path)
    resolve_batch(batch_id, db_path, [staged_id])
    unresolved_rows = get_shared_connection(db_path).execute(
        """
        SELECT DISTINCT source_symbol
        FROM staged_records
        WHERE staged_file_id = ? AND source_symbol IS NOT NULL
          AND resolution_status <> 'resolved'
        ORDER BY source_symbol
        """,
        [staged_id],
    ).fetchall()
    if unresolved_rows:
        error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
        mark_file(staged_id, "quarantined", error, db_path)
        complete_batch(batch_id, db_path)
        return SourceResult("email", None, "failed", error=error)

    try:
        rows = _publish_email_batch(staged_id, data, db_path)
        complete_batch(batch_id, db_path)
        return SourceResult("email", None, "succeeded", rows)
    except Exception as exc:
        logger.exception("Email pipeline failed")
        mark_file(staged_id, "quarantined", str(exc), db_path)
        complete_batch(batch_id, db_path)
        return SourceResult("email", None, "failed", error=str(exc))


def run_pipeline(
    source: str = "all",
    data_dir: Path | str = DATA_FOLDER,
    db_path: Path | str = DATABASE_PATH,
) -> PipelineResult:
    """Stage all selected sources, resolve shared ticker evidence, then publish in order."""
    initialize_database(db_path)
    batch_id = create_batch(db_path)
    results: list[SourceResult] = []
    sequence = 1

    if source in {"all", "statements"}:
        statement_staged, sequence, stage_results = _stage_statement_files(batch_id, data_dir, db_path, sequence)
        results.extend(stage_results)
        if statement_staged:
            resolve_batch(batch_id, db_path, [staged_file_id for staged_file_id, _, _ in statement_staged])
            for staged_file_id, file, data in statement_staged:
                unresolved_rows = get_shared_connection(db_path).execute(
                    """
                    SELECT DISTINCT source_symbol
                    FROM staged_records
                    WHERE staged_file_id = ? AND source_symbol IS NOT NULL
                      AND resolution_status <> 'resolved'
                    ORDER BY source_symbol
                    """,
                    [staged_file_id],
                ).fetchall()
                if unresolved_rows:
                    error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
                    mark_file(staged_file_id, "quarantined", error, db_path)
                    results.append(SourceResult("statement", file, "failed", error=error))
                    continue
                try:
                    rows = _publish_statement_file(staged_file_id, file, data, data_dir, db_path)
                    results.append(SourceResult("statement", file, "succeeded", rows))
                except Exception as exc:
                    logger.exception("Statement publication failed | file=%s", file)
                    mark_file(staged_file_id, "quarantined", str(exc), db_path)
                    results.append(SourceResult("statement", file, "failed", error=str(exc)))

    if source in {"all", "email"}:
        email_staged, sequence, stage_results = _stage_email_batch(batch_id, db_path, sequence)
        results.extend(stage_results)
        for staged_file_id, data in email_staged:
            resolve_batch(batch_id, db_path, [staged_file_id])
            unresolved_rows = get_shared_connection(db_path).execute(
                """
                SELECT DISTINCT source_symbol
                FROM staged_records
                WHERE staged_file_id = ? AND source_symbol IS NOT NULL
                  AND resolution_status <> 'resolved'
                ORDER BY source_symbol
                """,
                [staged_file_id],
            ).fetchall()
            if unresolved_rows:
                error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
                mark_file(staged_file_id, "quarantined", error, db_path)
                results.append(SourceResult("email", None, "failed", error=error))
                continue
            try:
                rows = _publish_email_batch(staged_file_id, data, db_path)
                results.append(SourceResult("email", None, "succeeded", rows))
            except Exception as exc:
                logger.exception("Email publication failed")
                mark_file(staged_file_id, "quarantined", str(exc), db_path)
                results.append(SourceResult("email", None, "failed", error=str(exc)))

    if source in {"all", "export"}:
        export_staged, sequence, stage_results = _stage_export_files(batch_id, data_dir, db_path, sequence)
        results.extend(stage_results)
        if export_staged:
            resolve_batch(batch_id, db_path, [staged_file_id for staged_file_id, _, _ in export_staged])
            for staged_file_id, file, _data in export_staged:
                unresolved_rows = get_shared_connection(db_path).execute(
                    """
                    SELECT DISTINCT source_symbol
                    FROM staged_records
                    WHERE staged_file_id = ? AND source_symbol IS NOT NULL
                      AND resolution_status <> 'resolved'
                    ORDER BY source_symbol
                    """,
                    [staged_file_id],
                ).fetchall()
                if unresolved_rows:
                    error = f"not published; unresolved ticker(s): {', '.join(symbol for (symbol,) in unresolved_rows)}"
                    mark_file(staged_file_id, "quarantined", error, db_path)
                    results.append(SourceResult("export", file, "failed", error=error))
                    continue
                try:
                    imported = sort_data(
                        source_file=file,
                        data_dir=Path(data_dir),
                        db_path=db_path,
                        enrich_tickers=False,
                        processed_dir=_archive_dir(data_dir, "full_exports"),
                    )
                    mark_file(staged_file_id, "published", db_path=db_path)
                    results.append(
                        SourceResult(
                            "export",
                            file,
                            imported.status,
                            imported.rows_written,
                            None if imported.status == "succeeded" else "Ticker resolution failed",
                        )
                    )
                except Exception as exc:
                    logger.exception("Export publication failed | file=%s", file)
                    mark_file(staged_file_id, "quarantined", str(exc), db_path)
                    results.append(SourceResult("export", file, "failed", error=str(exc)))

    complete_batch(batch_id, db_path)
    if not results:
        results.append(SourceResult(source, None, "skipped"))
    return PipelineResult(tuple(results))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Wealthsimple data pipeline.")
    parser.add_argument(
        "--source",
        choices=("all", "export", "statements", "email"),
        default="all",
    )
    parser.add_argument("--data-folder", type=Path, default=DATA_FOLDER)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    return parser.parse_args(argv)


def _print_portfolio_report(report: dict[str, object]) -> None:
    def _format_value(value: object) -> str:
        if isinstance(value, (int, float, Decimal)):
            return f"{value:,.2f}"
        return str(value)

    def _section_title(title: str) -> None:
        print()
        print(title)
        print("-" * len(title))

    print("Portfolio Analytics")
    print("=" * len("Portfolio Analytics"))
    print(f"Portfolio value : {_format_value(report['portfolio_value'])}")

    cash = report["cash"]
    if isinstance(cash, dict):
        print(f"Cash balance    : {_format_value(cash.get('balance', 0))}")
        print(f"Cash source     : {cash.get('source', 'unknown')}")

    holdings = report.get("holdings", [])
    print(f"Holdings        : {len(holdings)}")

    if not holdings:
        _section_title("Positions")
        print("No open positions.")
        return

    _section_title("Positions")
    headers = ("Ticker", "Exchange", "Quantity", "Market Value")
    rows: list[tuple[str, str, str, str]] = []
    for holding in holdings:
        if isinstance(holding, dict):
            rows.append(
                (
                    str(holding.get("ticker_symbol", "")),
                    str(holding.get("exchange", "")),
                    _format_value(holding.get("quantity", 0)),
                    _format_value(holding.get("market_value", 0)),
                )
            )

    if not rows:
        print("No open positions.")
        return

    widths = [
        max(len(headers[0]), *(len(row[0]) for row in rows)),
        max(len(headers[1]), *(len(row[1]) for row in rows)),
        max(len(headers[2]), *(len(row[2]) for row in rows)),
        max(len(headers[3]), *(len(row[3]) for row in rows)),
    ]
    header_line = (
        f"{headers[0]:<{widths[0]}}  "
        f"{headers[1]:<{widths[1]}}  "
        f"{headers[2]:>{widths[2]}}  "
        f"{headers[3]:>{widths[3]}}"
    )
    print(header_line)
    print(
        f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  {'-' * widths[3]}"
    )
    for ticker, exchange, quantity, market_value in rows:
        print(
            f"{ticker:<{widths[0]}}  "
            f"{exchange:<{widths[1]}}  "
            f"{quantity:>{widths[2]}}  "
            f"{market_value:>{widths[3]}}"
        )


def _json_default(value: object) -> object:
    if isinstance(value, (date, Path)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _print_portfolio_report_json(report: dict[str, object]) -> None:
    print(json.dumps(report, default=_json_default, indent=2, sort_keys=True))


def run_analytics(db_path: Path | str = DATABASE_PATH) -> dict[str, object]:
    """Return the current read-only analytics report."""
    initialize_database(db_path)
    return portfolio_report(db_path)


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "ticker-map":
        from ticker_mapping import main as ticker_mapping_main
        return ticker_mapping_main(raw_args[1:])
    if raw_args and raw_args[0] == "analytics":
        parser = argparse.ArgumentParser(description="Show a read-only portfolio analytics report.")
        parser.add_argument("--database", type=Path, default=DATABASE_PATH)
        parser.add_argument("--export", action="store_true", help="Export the report as JSON.")
        args = parser.parse_args(raw_args[1:])
        try:
            report = run_analytics(args.database)
        except Exception:
            logger.exception("Analytics report failed")
            return 1
        if args.export:
            _print_portfolio_report_json(report)
        else:
            _print_portfolio_report(report)
        return 0
    args = parse_args(raw_args)
    try:
        result = run_pipeline(args.source, args.data_folder, args.database)
    except Exception:
        logger.exception("Pipeline startup failed")
        return 1
    for source_result in result.results:
        file_text = f" [{source_result.source_file}]" if source_result.source_file else ""
        error_text = f" - {source_result.error}" if source_result.error else ""
        print(
            f"{source_result.source}{file_text}: {source_result.status} "
            f"({source_result.rows} row(s)){error_text}"
        )
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
