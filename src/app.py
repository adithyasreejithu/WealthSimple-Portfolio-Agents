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

from config import (
    ANALYTICS_EXPORT_FILENAME,
    ANALYTICS_EXPORT_FOLDER,
    DATABASE_PATH,
    DATA_FOLDER,
    DEFAULT_BENCHMARK_SYMBOL,
    PROCESSED_FOLDER_NAME,
    SOURCE_PREFIX,
)
from data_sorter import move_to_processed_folder, sort_data
from database import close_connection, get_shared_connection, initialize_database
from database_command import (
    get_email_checkpoint,
    reconcile_email_transactions,
    reconcile_statement_activities,
    update_email_checkpoint,
    upload_email_transactions,
    upload_portfolio_classifications,
    upload_statement_transactions,
)
from analytics import portfolio_report
from email_extractor import fetch_email_transactions
from holdings_reconciler import format_report, reconcile_holdings
from market_data import sync_earnings_dividends, sync_financial_snapshots, sync_market_data
from position_engine import ensure_positions_fresh, recompute_positions
from statement_extractor import extract_statement_pdf
from staging import (
    complete_batch, create_batch, mark_file, resolve_batch,
    stage_dataframe,
)
from system_logger import get_logger
import ticker_mapping


logger = get_logger(__name__)
SUPPORTED_DATA_SUFFIXES = frozenset({".csv", ".pdf", ".xlsx", ".xls"})
CLASSIFY_SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "classify-portfolio" / "scripts"
)
BOOTSTRAP_SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "bootstrap-stock-research" / "scripts"
)


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
        reconcile_statement_activities(db_path)
        reconcile_email_transactions(db_path)
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
        reconcile_statement_activities(db_path)
        reconcile_email_transactions(db_path)
        received = (
            pd.to_datetime(prepared["received_at"], errors="coerce")
            if "received_at" in prepared.columns and not prepared.empty
            else pd.Series(dtype="datetime64[ns]")
        )
        latest_received = received.max() if not received.empty else pd.NaT
        checkpoint = (
            latest_received.to_pydatetime()
            if not pd.isna(latest_received)
            else max(prepared["date"], default=date.today())
        )
        update_email_checkpoint(checkpoint, rows, db_path)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    mark_file(staged_file_id, "published", db_path=db_path)
    return rows


def _pending_email_symbols(staged_file_id: int, db_path: Path | str) -> list[str]:
    rows = get_shared_connection(db_path).execute(
        """
        SELECT DISTINCT source_symbol
        FROM staged_records
        WHERE staged_file_id = ? AND source_symbol IS NOT NULL
          AND resolution_status <> 'resolved'
        ORDER BY source_symbol
        """,
        [staged_file_id],
    ).fetchall()
    return [str(symbol) for (symbol,) in rows]


def _pending_email_error(symbols: list[str]) -> str:
    joined = ", ".join(symbols)
    return (
        f"published with pending ticker(s): {joined}; "
        "run `python src/app.py resolve-tickers` to map them and retry"
    )


def _sync_email_history(db_path: Path | str) -> str | None:
    result = sync_market_data(db_path)
    if result.succeeded:
        return None
    return result.error or "historical price synchronization failed"


def _run_portfolio_classification(db_path: Path | str) -> SourceResult:
    """Classify current holdings and persist the result, as the final step of
    a full pipeline run. The classification workflow itself stays read-only
    (a separate read-only DuckDB connection, per
    docs/agents/portfolio-classifier/architecture.md); this wraps it with the
    same explicit classification-sync write the CLI command performs, so a
    routine full pipeline run always reflects current classifications without
    a separate manual step. Failure here never rolls back ingestion that
    already completed, matching the yfinance-sync failure-handling pattern.
    """
    # The classification workflow and its helper modules live beside the
    # classify-portfolio skill, not in src/, so expose them for import.
    sys.path.insert(0, str(CLASSIFY_SKILL_SCRIPTS))
    from classification_workflow import classify_portfolio, write_output

    try:
        # DuckDB rejects a second connection to the same file when its
        # configuration differs, and the workflow always opens its own
        # read-only one. Release the pipeline's read-write connection first;
        # ingestion is committed by this point, and upload_portfolio_
        # classifications below reopens the shared connection on demand.
        close_connection()
        payload = classify_portfolio(db_path)
        output_path = write_output(payload)
        rows = upload_portfolio_classifications(output_path, db_path)
        return SourceResult("classification", output_path, "succeeded", rows)
    except Exception as exc:
        logger.exception("Portfolio classification failed")
        return SourceResult("classification", None, "failed", error=str(exc))


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


def _resolve_and_publish_export_file(
    staged_file_id: int,
    file: Path,
    data_dir: Path | str,
    db_path: Path | str,
) -> SourceResult:
    """Publish one already-staged export file, or quarantine it.

    Shared by `run_full_exports` (fresh batches) and
    `retry_quarantined_exports_for_symbol` (reprocessing an existing
    quarantined `staged_file_id` in place) so both paths make the identical
    publish-or-quarantine decision.
    """
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
        error = (
            f"not published; unresolved ticker(s): "
            f"{', '.join(symbol for (symbol,) in unresolved_rows)}; "
            "run `python src/app.py resolve-tickers` to map them and retry"
        )
        mark_file(staged_file_id, "quarantined", error, db_path)
        return SourceResult("export", file, "failed", error=error)
    try:
        imported = sort_data(
            source_file=file,
            data_dir=Path(data_dir),
            db_path=db_path,
            enrich_tickers=False,
            processed_dir=_archive_dir(data_dir, "full_exports"),
        )
        mark_file(staged_file_id, "published", db_path=db_path)
        return SourceResult(
            "export",
            file,
            imported.status,
            imported.rows_written,
            None if imported.status == "succeeded" else "Ticker resolution failed",
        )
    except Exception as exc:
        logger.exception("Full export pipeline failed | file=%s", file)
        mark_file(staged_file_id, "quarantined", str(exc), db_path)
        return SourceResult("export", file, "failed", error=str(exc))


def retry_quarantined_exports_for_symbol(
    source_symbol: str,
    db_path: Path | str = DATABASE_PATH,
    data_dir: Path | str = DATA_FOLDER,
) -> list[SourceResult]:
    """Reprocess quarantined export files blocked by `source_symbol`.

    Applies the mapping already saved in `ticker_symbol_history` directly to
    the stuck `staged_records` (export resolution is a live `tickers`-table
    match, never a `ticker_symbol_history` lookup -- see
    `ticker_pipeline.resolve_or_enrich_ticker`), then republishes in place on
    the existing `staged_file_id`. Never creates a new batch, so it can never
    leave a duplicate orphaned quarantine row behind the way a full pipeline
    rerun would.
    """
    initialize_database(db_path)
    connection = get_shared_connection(db_path)
    symbol = source_symbol.strip().upper()
    mapping = connection.execute(
        """
        SELECT ticker_id FROM ticker_symbol_history
        WHERE source_symbol = ? AND effective_to IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        [symbol],
    ).fetchone()
    if not mapping:
        return [SourceResult(
            "export", None, "failed", error=f"No saved mapping for {symbol}; resolve it first.",
        )]
    ticker_id = int(mapping[0])
    files = connection.execute(
        """
        SELECT DISTINCT f.staged_file_id, f.source_path
        FROM staged_files f JOIN staged_records r USING (staged_file_id)
        WHERE f.source_type = 'export' AND f.status = 'quarantined'
          AND r.source_symbol = ? AND r.resolution_status <> 'resolved'
        """,
        [symbol],
    ).fetchall()
    results: list[SourceResult] = []
    for staged_file_id, source_path in files:
        connection.execute(
            """
            UPDATE staged_records SET ticker_id = ?, resolution_status = 'resolved',
                resolution_method = 'manual_mapping'
            WHERE staged_file_id = ? AND source_symbol = ? AND resolution_status <> 'resolved'
            """,
            [ticker_id, staged_file_id, symbol],
        )
        file_path = Path(source_path) if source_path else None
        if file_path is None or not file_path.exists():
            error = (
                f"Mapping saved, but the original export file is no longer at "
                f"{source_path or '(unknown path)'}; run the full pipeline to reprocess it."
            )
            mark_file(staged_file_id, "quarantined", error, db_path)
            results.append(SourceResult("export", file_path, "failed", error=error))
            continue
        results.append(
            _resolve_and_publish_export_file(staged_file_id, file_path, data_dir, db_path)
        )
    return results


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
        results.append(_resolve_and_publish_export_file(staged_file_id, file, data_dir, db_path))
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
            error = (
                f"not published; unresolved ticker(s): "
                f"{', '.join(symbol for (symbol,) in unresolved_rows)}; "
                "run `python src/app.py resolve-tickers` to map them and retry"
            )
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
    pending_symbols = _pending_email_symbols(staged_id, db_path)

    try:
        rows = _publish_email_batch(staged_id, data, db_path)
        history_error = _sync_email_history(db_path)
        partial_errors = []
        if pending_symbols:
            partial_errors.append(_pending_email_error(pending_symbols))
        if history_error:
            partial_errors.append(history_error)
        if partial_errors:
            error = "; ".join(partial_errors)
            mark_file(staged_id, "partial", error, db_path)
            logger.warning("Email pipeline %s", error)
            complete_batch(batch_id, db_path)
            return SourceResult("email", None, "partial", rows, error)
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
                    error = (
                        f"not published; unresolved ticker(s): "
                        f"{', '.join(symbol for (symbol,) in unresolved_rows)}; "
                        "run `python src/app.py resolve-tickers` to map them and retry"
                    )
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
            pending_symbols = _pending_email_symbols(staged_file_id, db_path)
            try:
                rows = _publish_email_batch(staged_file_id, data, db_path)
                history_error = _sync_email_history(db_path)
                partial_errors = []
                if pending_symbols:
                    partial_errors.append(_pending_email_error(pending_symbols))
                if history_error:
                    partial_errors.append(history_error)
                if partial_errors:
                    error = "; ".join(partial_errors)
                    mark_file(staged_file_id, "partial", error, db_path)
                    logger.warning("Email pipeline %s", error)
                    results.append(SourceResult("email", None, "partial", rows, error))
                else:
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
                    error = (
                        f"not published; unresolved ticker(s): "
                        f"{', '.join(symbol for (symbol,) in unresolved_rows)}; "
                        "run `python src/app.py resolve-tickers` to map them and retry"
                    )
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

    ensure_positions_fresh(get_shared_connection(db_path))
    complete_batch(batch_id, db_path)
    if not results:
        results.append(SourceResult(source, None, "skipped"))

    if source == "all":
        results.append(_run_portfolio_classification(db_path))

    return PipelineResult(tuple(results))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse pipeline options, including the legacy top-level invocation."""
    parser = argparse.ArgumentParser(description="Run the Wealthsimple data pipeline.")
    parser.add_argument(
        "--source",
        choices=("all", "export", "statements", "email"),
        default="all",
    )
    parser.add_argument("--data-folder", type=Path, default=DATA_FOLDER)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    return parser.parse_args(argv)


def _print_root_help() -> None:
    """Show the canonical command catalog while retaining legacy pipeline flags."""
    parser = argparse.ArgumentParser(
        description="Run Wealthsimple portfolio pipeline and analysis commands.",
        epilog=(
            "Legacy pipeline syntax remains supported: "
            "python src/app.py --source all"
        ),
    )
    commands = parser.add_subparsers(dest="command", title="commands")
    commands.add_parser("pipeline", help="Run the staged data pipeline.")
    commands.add_parser("analytics", help="Show a portfolio analytics report.")
    commands.add_parser("statements", help="Extract PDF statement activity.")
    commands.add_parser("email", help="Extract email transactions.")
    commands.add_parser("yfinance", help="Fetch market metadata and history.")
    commands.add_parser(
        "yfinance-sync", help="Synchronize owned ticker market data to DuckDB."
    )
    commands.add_parser(
        "earnings-dividends", help="Fetch company-declared earnings and dividend calendars."
    )
    commands.add_parser(
        "earnings-dividends-sync",
        help="Synchronize owned tickers' earnings/dividend calendars to DuckDB.",
    )
    commands.add_parser(
        "financial-snapshots", help="Fetch per-quarter company financial statement data."
    )
    commands.add_parser(
        "financial-snapshots-sync",
        help="Synchronize owned tickers' per-quarter financial statement data to DuckDB.",
    )
    commands.add_parser(
        "annual-financial-context",
        help="Fetch one ticker's annual financial statements as ephemeral "
             "first-run KB context (not persisted).",
    )
    commands.add_parser("ticker-map", help="Manage ticker mappings.")
    commands.add_parser(
        "resolve-tickers",
        help="Resolve pending ticker mappings interactively, then retry quarantined ingestion.",
    )
    commands.add_parser("import-activities", help="Import one activity export.")
    commands.add_parser(
        "portfolio-classify",
        help="Run the read-only portfolio classification workflow over the live database.",
    )
    commands.add_parser(
        "classification-sync",
        help="Sync the classify-portfolio JSON output into DuckDB.",
    )
    commands.add_parser(
        "recompute-positions",
        help="Rebuild position_ledger/position_snapshots from source tables.",
    )
    commands.add_parser(
        "reconcile-holdings",
        help="Compare computed holdings against a broker holdings CSV export.",
    )
    commands.add_parser(
        "run",
        help="Create and manage investment-research run workspaces "
             "(create, show, list, register-evidence, build-manifest, validate, "
             "set-status, archive).",
    )
    parser.print_help()


def _print_portfolio_report(report: dict[str, object]) -> None:
    def _format_value(value: object) -> str:
        if isinstance(value, (int, float, Decimal)):
            return f"{value:,.2f}"
        return str(value)

    def _section_title(title: str) -> None:
        print()
        print(title)
        print("-" * len(title))

    summary = report.get("summary", {})
    allocation = report.get("allocation", {})
    targets = report.get("targets", {})
    income = report.get("income", {})
    fees = report.get("fees", {})
    data_quality = report.get("data_quality", {})
    unavailable_metrics = report.get("unavailable_metrics", [])
    holdings = report.get("holdings", [])

    print("Portfolio Analytics")
    print("=" * len("Portfolio Analytics"))
    print(f"Portfolio value : {_format_value(summary.get('portfolio_value', 0))}")

    cash = summary.get("cash", {})
    if isinstance(cash, dict):
        print(f"Cash balance    : {_format_value(cash.get('balance', 0))}")
        print(f"Cash source     : {cash.get('source', 'unknown')}")

    print(f"Book cost       : {_format_value(summary.get('book_cost', 0))}")
    unrealized = summary.get("unrealized_gain", {})
    if isinstance(unrealized, dict):
        percent = unrealized.get("percent")
        percent_text = f"{percent:.2%}" if percent is not None else "n/a"
        print(f"Unrealized gain : {_format_value(unrealized.get('amount', 0))} ({percent_text})")
    print(f"Realized gain   : {_format_value(summary.get('realized_gain_total', 0))}")
    print(f"Holdings        : {len(holdings)}")

    if isinstance(income, dict):
        print(f"Dividend source : {income.get('source', 'unknown')}")
        totals = income.get("totals_by_currency", {})
        if isinstance(totals, dict):
            for currency, amount in sorted(totals.items()):
                print(f"Dividends {currency:<4} : {_format_value(amount)}")
    if isinstance(fees, dict) and isinstance(fees.get("fx"), dict):
        fx = fees["fx"]
        if fx.get("available"):
            print(f"Estimated FX fee: {_format_value(fx.get('estimated_fx_fee_cad', 0))} CAD")
        else:
            print(f"Estimated FX fee: unavailable for {fx.get('source', 'source')}")

    by_group = allocation.get("by_group", {}) if isinstance(allocation, dict) else {}
    if by_group:
        _section_title("Allocation by Group")
        drift_by_group = {
            row["group"]: row for row in (targets.get("groups", []) if isinstance(targets, dict) else [])
        }
        for group, info in sorted(by_group.items(), key=lambda item: -item[1]["weight"]):
            drift_row = drift_by_group.get(group)
            drift_text = ""
            if drift_row and drift_row.get("drift_pp") is not None:
                flag = " (rebalance)" if drift_row.get("rebalance_needed") else ""
                drift_text = f"  target {drift_row['target_percent']:.0f}%  drift {drift_row['drift_pp']:+.1f}pp{flag}"
            print(f"{group:<15} {info['weight']:>6.1%}{drift_text}")

    if isinstance(data_quality, dict):
        counts = data_quality.get("counts_by_code", {})
        if counts:
            _section_title("Data Quality")
            for code, count in sorted(counts.items()):
                print(f"{code:<22} {count}")

    if unavailable_metrics:
        _section_title("Unavailable Metrics")
        print(f"{len(unavailable_metrics)} metric(s) unavailable; see --export JSON for reasons.")

    if not holdings:
        _section_title("Positions")
        print("No open positions.")
        return

    _section_title("Positions")
    headers = ("Ticker", "Exchange", "Quantity", "Market Value", "Status")
    rows: list[tuple[str, str, str, str, str]] = []
    for holding in holdings:
        if isinstance(holding, dict):
            rows.append(
                (
                    str(holding.get("ticker_symbol", "")),
                    str(holding.get("exchange", "")),
                    _format_value(holding.get("quantity", 0)),
                    _format_value(holding.get("market_value", 0)),
                    "provisional" if holding.get("has_provisional_activity") else "confirmed",
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
        max(len(headers[4]), *(len(row[4]) for row in rows)),
    ]
    header_line = (
        f"{headers[0]:<{widths[0]}}  "
        f"{headers[1]:<{widths[1]}}  "
        f"{headers[2]:>{widths[2]}}  "
        f"{headers[3]:>{widths[3]}}  "
        f"{headers[4]:<{widths[4]}}"
    )
    print(header_line)
    print(
        f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  "
        f"{'-' * widths[3]}  {'-' * widths[4]}"
    )
    for ticker, exchange, quantity, market_value, status in rows:
        print(
            f"{ticker:<{widths[0]}}  "
            f"{exchange:<{widths[1]}}  "
            f"{quantity:>{widths[2]}}  "
            f"{market_value:>{widths[3]}}  "
            f"{status:<{widths[4]}}"
        )


def _json_default(value: object) -> object:
    if isinstance(value, (date, Path)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _write_analytics_export(
    report: dict[str, object], export_folder: Path | str = ANALYTICS_EXPORT_FOLDER
) -> Path:
    """Write the analytics report as a stable-named JSON export and return its path."""
    export_folder = Path(export_folder)
    export_folder.mkdir(parents=True, exist_ok=True)
    export_path = export_folder / ANALYTICS_EXPORT_FILENAME
    envelope = {
        "schema_version": "1.0",
        "generated_at": report.get("generated_at"),
        "workflow": "portfolio-analytics",
        "parameters": report.get("parameters", {}),
        "report": report,
    }
    export_path.write_text(
        json.dumps(envelope, default=_json_default, indent=2, sort_keys=True), encoding="utf-8"
    )
    return export_path


def run_analytics(
    db_path: Path | str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    dividend_source: str = "email",
    cash_flow_source: str = "activities",
    fx_source: str = "statements",
    benchmark_symbol: str | None = DEFAULT_BENCHMARK_SYMBOL,
    benchmark_fetcher: Callable[[list[str], date, date], object] | None = None,
) -> dict[str, object]:
    """Return the current read-only analytics report."""
    initialize_database(db_path)
    return portfolio_report(
        db_path,
        date_from=date_from,
        date_to=date_to,
        dividend_source=dividend_source,
        cash_flow_source=cash_flow_source,
        fx_source=fx_source,
        benchmark_symbol=benchmark_symbol,
        benchmark_fetcher=benchmark_fetcher,
    )


def _run_analytics_command(argv: list[str]) -> int:
    """Parse and execute the analytics command independently of pipeline flags."""
    parser = argparse.ArgumentParser(description="Show a read-only portfolio analytics report.")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--date-from", type=date.fromisoformat)
    parser.add_argument("--date-to", type=date.fromisoformat)
    parser.add_argument(
        "--dividend-source", choices=("email", "activities", "statements"), default="email"
    )
    parser.add_argument(
        "--cash-flow-source", choices=("activities", "statements"), default="activities"
    )
    parser.add_argument(
        "--fx-source", choices=("statements", "exports", "email"), default="statements"
    )
    parser.add_argument(
        "--benchmark", default=DEFAULT_BENCHMARK_SYMBOL,
        help="Benchmark ticker symbol fetched live from yfinance for comparison stats.",
    )
    parser.add_argument(
        "--no-benchmark", action="store_true", help="Skip the live benchmark comparison fetch."
    )
    parser.add_argument(
        "--export", action="store_true",
        help=f"Write the report to {ANALYTICS_EXPORT_FOLDER / ANALYTICS_EXPORT_FILENAME} instead of printing it.",
    )
    parser.add_argument(
        "--export-folder", type=Path, default=ANALYTICS_EXPORT_FOLDER,
        help="Destination folder for --export.",
    )
    args = parser.parse_args(argv)
    try:
        report = run_analytics(
            args.database,
            date_from=args.date_from,
            date_to=args.date_to,
            dividend_source=args.dividend_source,
            cash_flow_source=args.cash_flow_source,
            fx_source=args.fx_source,
            benchmark_symbol=None if args.no_benchmark else args.benchmark,
        )
    except Exception:
        logger.exception("Analytics report failed")
        return 1
    if args.export:
        export_path = _write_analytics_export(report, args.export_folder)
        print(f"Exported analytics report: {export_path}")
    else:
        _print_portfolio_report(report)
    return 0


def _print_pipeline_results(result: PipelineResult) -> None:
    for source_result in result.results:
        file_text = f" [{source_result.source_file}]" if source_result.source_file else ""
        error_text = f" - {source_result.error}" if source_result.error else ""
        print(
            f"{source_result.source}{file_text}: {source_result.status} "
            f"({source_result.rows} row(s)){error_text}"
        )


def _run_pipeline_command(argv: list[str]) -> int:
    """Run the pipeline for canonical and legacy command forms."""
    args = parse_args(argv)
    try:
        result = run_pipeline(args.source, args.data_folder, args.database)
    except Exception:
        logger.exception("Pipeline startup failed")
        return 1
    _print_pipeline_results(result)
    return 0 if result.succeeded else 1


def _run_resolve_tickers_command(argv: list[str]) -> int:
    """Resolve every pending ticker interactively, then retry ingestion that was quarantined because of it."""
    parser = argparse.ArgumentParser(
        description="Resolve pending ticker mappings and retry any quarantined ingestion."
    )
    parser.add_argument("--data-folder", type=Path, default=DATA_FOLDER)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)

    try:
        pending = ticker_mapping.list_pending(args.database)
        if not pending:
            print("No pending ticker mappings.")
            return 0
        resolutions = ticker_mapping.resolve_pending_interactively(args.database)
    except Exception:
        logger.exception("Ticker resolution failed")
        return 1

    verified = [item for item in resolutions if item.get("status") == "verified"]
    print()
    for item in resolutions:
        symbol = item.get("source_symbol", "?")
        if item.get("status") == "verified":
            print(
                f"{symbol}: mapped to {item.get('provider_symbol')} ({item.get('currency')}) "
                f"- {item.get('resolved_email_rows', 0)} email row(s) updated"
            )
        else:
            print(f"{symbol}: skipped")

    if not verified:
        print("\nNo mappings were saved; nothing to retry.")
        return 0

    print("\nRetrying ingestion for previously quarantined source(s)...")
    try:
        result = run_pipeline("all", args.data_folder, args.database)
    except Exception:
        logger.exception("Pipeline retry failed after ticker resolution")
        return 1
    _print_pipeline_results(result)
    return 0 if result.succeeded else 1


def _run_delegated_command(command: str, argv: list[str]) -> int:
    """Run a module-owned command while keeping failures at the app boundary."""
    delegated_commands = {
        "statements": ("statement_extractor", "main"),
        "email": ("email_extractor", "main"),
        "yfinance": ("yfinance_extractor", "main"),
        "earnings-dividends": ("earnings_dividends_extractor", "main"),
        "financial-snapshots": ("financial_snapshots_extractor", "main"),
        "annual-financial-context": ("annual_financial_context", "main"),
        "ticker-map": ("ticker_mapping", "main"),
        "import-activities": ("data_sorter", "main"),
        "portfolio-classify": ("classify_portfolio", "main"),
    }
    module_name, function_name = delegated_commands[command]
    if command == "portfolio-classify":
        # The classification workflow and its helper modules live beside the
        # classify-portfolio skill, not in src/, so expose them for import.
        sys.path.insert(0, str(CLASSIFY_SKILL_SCRIPTS))
    elif command == "annual-financial-context":
        # The bootstrap-stock-research skill's scripts live beside the skill,
        # not in src/, so expose them for import.
        sys.path.insert(0, str(BOOTSTRAP_SKILL_SCRIPTS))
    module = __import__(module_name, fromlist=[function_name])
    command_main = getattr(module, function_name)
    try:
        return int(command_main(argv))
    except Exception:
        logger.exception("CLI command failed | command=%s", command)
        return 1


def _run_yfinance_sync_command(argv: list[str]) -> int:
    """Synchronize owned ticker metadata and history into DuckDB."""
    parser = argparse.ArgumentParser(
        description="Synchronize owned ticker market data into DuckDB."
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Optionally limit synchronization to canonical or Yahoo symbols.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Backfill again from each ticker's first portfolio activity.",
    )
    args = parser.parse_args(argv)
    result = sync_market_data(args.database, args.tickers, full=args.full)
    error_text = f" - {result.error}" if result.error else ""
    print(
        f"yfinance: {'succeeded' if result.succeeded else 'failed'} "
        f"({result.rows} row(s), {result.tickers} ticker(s), "
        f"{result.skipped} skipped){error_text}"
    )
    return 0 if result.succeeded else 1


def _run_earnings_dividends_sync_command(argv: list[str]) -> int:
    """Synchronize owned tickers' company-declared earnings/dividend calendars into DuckDB."""
    parser = argparse.ArgumentParser(
        description="Synchronize company-declared earnings and dividend calendars into DuckDB."
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Optionally limit synchronization to canonical or Yahoo symbols.",
    )
    parser.add_argument("--skip-earnings", action="store_true", help="Skip the earnings calendar sync.")
    parser.add_argument("--skip-dividends", action="store_true", help="Skip the dividend schedule sync.")
    args = parser.parse_args(argv)
    result = sync_earnings_dividends(
        args.database,
        args.tickers,
        skip_earnings=args.skip_earnings,
        skip_dividends=args.skip_dividends,
    )
    error_text = f" - {result.error}" if result.error else ""
    print(
        f"earnings-dividends: {'succeeded' if result.succeeded else 'failed'} "
        f"({result.earnings_rows} earnings row(s), {result.dividend_rows} dividend row(s), "
        f"{result.tickers} ticker(s)){error_text}"
    )
    return 0 if result.succeeded else 1


def _run_financial_snapshots_sync_command(argv: list[str]) -> int:
    """Synchronize owned tickers' per-quarter financial statement data into DuckDB."""
    parser = argparse.ArgumentParser(
        description="Synchronize per-quarter company financial statement data into DuckDB."
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Optionally limit synchronization to canonical or Yahoo symbols.",
    )
    args = parser.parse_args(argv)
    result = sync_financial_snapshots(args.database, args.tickers)
    error_text = f" - {result.error}" if result.error else ""
    print(
        f"financial-snapshots: {'succeeded' if result.succeeded else 'failed'} "
        f"({result.snapshot_rows} row(s), {result.tickers} ticker(s)){error_text}"
    )
    return 0 if result.succeeded else 1


def _run_classification_sync_command(argv: list[str]) -> int:
    """Persist the latest classify-portfolio JSON output into DuckDB."""
    parser = argparse.ArgumentParser(
        description="Sync the classification JSON output into DuckDB."
    )
    parser.add_argument("--input", type=Path, help="Path to the classification JSON file.")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)
    try:
        count = upload_portfolio_classifications(args.input, args.database)
    except Exception as exc:
        print(f"classification-sync failed: {exc}", file=sys.stderr)
        return 1
    print(f"classification-sync: succeeded ({count} row(s))")
    return 0


def _run_recompute_positions_command(argv: list[str]) -> int:
    """Force a full rebuild of position_ledger/position_snapshots.

    Normally unnecessary -- every holdings-reading command calls
    `ensure_positions_fresh` itself -- but useful after a manual database
    edit, a reconciliation-tolerance config change, or when debugging.
    """
    parser = argparse.ArgumentParser(
        description="Rebuild position_ledger and position_snapshots from the current source tables."
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)
    connection = get_shared_connection(args.database)
    ticker_count = recompute_positions(connection)
    print(f"recompute-positions: succeeded ({ticker_count} ticker(s))")
    return 0


def _run_reconcile_holdings_command(argv: list[str]) -> int:
    """Compare computed holdings against a broker holdings CSV export."""
    parser = argparse.ArgumentParser(
        description="Compare computed holdings against a Wealthsimple holdings CSV export."
    )
    parser.add_argument("--report", type=Path, required=True, help="Path to the broker holdings CSV export.")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)
    result = reconcile_holdings(args.report, args.database)
    print(format_report(result))
    return 0 if result.ok else 1


def _run_workspace_command(argv: list[str]) -> int:
    """Create and manage run workspaces (`docs/architecture/run_workspace.md`).

    Imported lazily so the rest of the CLI does not pay for pydantic on every
    invocation, matching how the other delegated commands stay self-contained.
    """
    from workspace.cli import main as workspace_main

    return workspace_main(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch every user-facing command from the canonical application entry point."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args in (["--help"], ["-h"]):
        _print_root_help()
        return 0
    if raw_args and raw_args[0] == "pipeline":
        return _run_pipeline_command(raw_args[1:])
    if raw_args and raw_args[0] == "analytics":
        return _run_analytics_command(raw_args[1:])
    if raw_args and raw_args[0] == "yfinance-sync":
        return _run_yfinance_sync_command(raw_args[1:])
    if raw_args and raw_args[0] == "earnings-dividends-sync":
        return _run_earnings_dividends_sync_command(raw_args[1:])
    if raw_args and raw_args[0] == "financial-snapshots-sync":
        return _run_financial_snapshots_sync_command(raw_args[1:])
    if raw_args and raw_args[0] == "classification-sync":
        return _run_classification_sync_command(raw_args[1:])
    if raw_args and raw_args[0] == "resolve-tickers":
        return _run_resolve_tickers_command(raw_args[1:])
    if raw_args and raw_args[0] == "recompute-positions":
        return _run_recompute_positions_command(raw_args[1:])
    if raw_args and raw_args[0] == "reconcile-holdings":
        return _run_reconcile_holdings_command(raw_args[1:])
    if raw_args and raw_args[0] == "run":
        return _run_workspace_command(raw_args[1:])

    # Delegate to module entry points so each command keeps one argument contract.
    delegated_commands = {
        "statements", "email", "yfinance", "earnings-dividends", "financial-snapshots",
        "annual-financial-context", "ticker-map", "import-activities", "portfolio-classify"
    }
    if raw_args and raw_args[0] in delegated_commands:
        return _run_delegated_command(raw_args[0], raw_args[1:])

    return _run_pipeline_command(raw_args)


if __name__ == "__main__":
    raise SystemExit(main())
