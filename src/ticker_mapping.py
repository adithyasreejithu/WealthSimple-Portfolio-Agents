"""Validated CLI and database service for ticker aliases and symbol changes."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from config import DATABASE_PATH
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
    return {"ticker_id": ticker_id, "source_symbol": source,
            "canonical_symbol": canonical, "provider_symbol": provider,
            "currency": currency, "status": "verified"}


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


def import_csv(path: Path, db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [add_mapping(db_path=db_path, **{key: value for key, value in row.items() if value != ""})
                for row in csv.DictReader(handle)]


def validate_mappings(source_symbol: str | None = None,
                      db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    mappings = list_mappings(db_path)
    selected = [item for item in mappings
                if not source_symbol or item["source_symbol"] == source_symbol.upper()]
    configure_yfinance_cache(Path(__import__("tempfile").gettempdir()) / "wealthsimple-yfinance-cache")
    results = []
    for item in selected:
        stocks, etfs = fetch_security_info([item["provider_symbol"]])
        valid = not stocks.empty or not etfs.empty
        results.append({**item, "validation_status": "verified" if valid else "failed"})
    return results


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
    else:
        result = import_csv(args.path, args.database)
    print(json.dumps(result, indent=2, default=str) if args.output == "json" else result)
    return 0
