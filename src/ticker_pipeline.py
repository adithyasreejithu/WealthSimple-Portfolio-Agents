"""Shared ticker correction and enrichment helpers for ingestion paths.

Temporary rule in this version:
- FX rate present => treat the security as USD-listed and keep the base ticker unchanged.
- FX rate absent => treat the security as CAD-listed, but only append .TO at the yfinance boundary.

This should eventually be replaced by a proper security master or exchange map.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATABASE_PATH, YFINANCE_CANADIAN_SUFFIX
from database import get_shared_connection
from database_command import ensure_tickers
from yfinance_extractor import TickerHint


def _text(value: Any) -> str:
    if value is None or value is pd.NA:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def fx_rate_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value)
    if not text:
        return False
    lowered = text.strip().lower()
    if lowered in {"no", "false", "0", "none", "nan"}:
        return False
    if lowered in {"yes", "true", "1"}:
        return True
    return True


def contains_fx_rate_label(value: Any) -> str:
    return "Yes" if fx_rate_present(value) else "No"


def base_ticker_symbol(symbol: Any) -> str:
    ticker = _text(symbol).upper()
    if not ticker:
        return ""
    if ticker.endswith(YFINANCE_CANADIAN_SUFFIX):
        return ticker[: -len(YFINANCE_CANADIAN_SUFFIX)]
    return ticker


def yfinance_query_symbol(symbol: Any, contains_fx_rate: Any) -> str:
    ticker = base_ticker_symbol(symbol)
    if not ticker:
        return ""
    if contains_fx_rate_label(contains_fx_rate) == "Yes":
        return ticker
    return f"{ticker}{YFINANCE_CANADIAN_SUFFIX}"


def listing_currency_from_fx(contains_fx_rate: Any) -> str:
    return "USD" if contains_fx_rate_label(contains_fx_rate) == "Yes" else "CAD"


def existing_ticker_candidates(
    symbol: str,
    db_path: Path | str,
) -> list[dict[str, Any]]:
    rows = get_shared_connection(db_path).execute(
        """
        SELECT ticker_id, ticker_symbol, exchange, currency, security_name, security_type,
               contains_fx_rate
        FROM tickers
        WHERE ticker_symbol = ?
        ORDER BY currency, exchange, ticker_id
        """,
        [symbol.upper()],
    ).fetchall()
    return [
        {
            "ticker_id": int(ticker_id),
            "ticker_symbol": ticker_symbol,
            "exchange": exchange,
            "currency": currency,
            "security_name": security_name,
            "security_type": security_type,
            "contains_fx_rate": contains_fx_rate,
        }
        for ticker_id, ticker_symbol, exchange, currency, security_name, security_type,
            contains_fx_rate in rows
    ]


@dataclass(frozen=True)
class ResolutionResult:
    ticker_id: int | None
    resolved_symbol: str
    listing_currency: str
    resolution_method: str | None
    enriched: bool


def resolve_or_enrich_ticker(
    symbol: str,
    contains_fx_rate: Any,
    name: str = "",
    source_type: str = "statement",
    db_path: Path | str = DATABASE_PATH,
) -> ResolutionResult:
    """Return a ticker id for the corrected symbol, enriching first-seen tickers if needed."""
    base_symbol = base_ticker_symbol(symbol)
    listing_currency = listing_currency_from_fx(contains_fx_rate)
    if not base_symbol:
        return ResolutionResult(None, "", listing_currency, None, False)

    candidates = existing_ticker_candidates(base_symbol, db_path)
    if not candidates and listing_currency == "CAD":
        # Support existing databases that stored the Yahoo-formatted symbol.
        candidates = existing_ticker_candidates(
            yfinance_query_symbol(base_symbol, contains_fx_rate), db_path
        )
    if len(candidates) == 1:
        return ResolutionResult(
            candidates[0]["ticker_id"],
            base_symbol,
            candidates[0]["currency"].upper(),
            "existing_ticker",
            False,
        )

    if source_type != "statement":
        return ResolutionResult(None, base_symbol, listing_currency, "unresolved", False)

    hint = TickerHint(base_symbol, listing_currency, _text(name))
    ensure_tickers([hint], db_path, require_all=False)
    refreshed = existing_ticker_candidates(base_symbol, db_path)
    refreshed_exact = [
        candidate for candidate in refreshed
        if candidate["currency"].upper() == listing_currency
    ]
    if len(refreshed_exact) == 1:
        get_shared_connection(db_path).execute(
            "UPDATE tickers SET contains_fx_rate = COALESCE(contains_fx_rate, ?) WHERE ticker_id = ?",
            [contains_fx_rate_label(contains_fx_rate), refreshed_exact[0]["ticker_id"]],
        )
        return ResolutionResult(
            refreshed_exact[0]["ticker_id"],
            base_symbol,
            listing_currency,
            "enriched_ticker",
            True,
        )
    if len(refreshed) == 1:
        get_shared_connection(db_path).execute(
            "UPDATE tickers SET contains_fx_rate = COALESCE(contains_fx_rate, ?) WHERE ticker_id = ?",
            [contains_fx_rate_label(contains_fx_rate), refreshed[0]["ticker_id"]],
        )
        return ResolutionResult(
            refreshed[0]["ticker_id"],
            base_symbol,
            listing_currency,
            "enriched_ticker",
            True,
        )
    return ResolutionResult(None, base_symbol, listing_currency, "unresolved", True)
