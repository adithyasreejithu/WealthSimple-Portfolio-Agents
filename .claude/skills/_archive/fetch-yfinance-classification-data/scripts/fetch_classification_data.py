"""Ephemeral, allowlisted yfinance enrichment for the portfolio classification workflow."""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

MAX_ENRICHMENT_BATCH_SIZE = 100

IDENTITY_FIELDS = frozenset({"company_name", "asset_class", "exchange", "currency", "financial_currency"})
EQUITY_FIELDS = IDENTITY_FIELDS | frozenset({"sector", "industry", "dividend_yield", "market_cap"})
ETF_FIELDS = IDENTITY_FIELDS | frozenset(
    {"fund_family", "etf_category", "dividend_yield", "aum", "expense_ratio", "nav", "top_holdings", "sector_weights"}
)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def mode_fields(mode: str) -> frozenset[str]:
    return {"identity": IDENTITY_FIELDS, "equity-classification": EQUITY_FIELDS, "etf-classification": ETF_FIELDS}[mode]


def fetch_classification_data(
    requests: Iterable[Mapping[str, Any]], ticker_factory: Callable[[str], Any] | None = None
) -> list[dict[str, Any]]:
    """Fetch only allowlisted fields for verified provider symbols."""
    request_list = list(requests)
    if len(request_list) > MAX_ENRICHMENT_BATCH_SIZE:
        raise ValueError(f"Enrichment batch exceeds {MAX_ENRICHMENT_BATCH_SIZE} holdings")
    if ticker_factory is None:
        import yfinance as yf
        ticker_factory = yf.Ticker
    key_map = {
        "company_name": ("longName", "shortName"), "asset_class": ("quoteType",),
        "exchange": ("fullExchangeName", "exchange"), "currency": ("currency",),
        "financial_currency": ("financialCurrency",), "sector": ("sector",), "industry": ("industry",),
        "dividend_yield": ("dividendYield", "yield"), "market_cap": ("marketCap",),
        "fund_family": ("fundFamily",), "etf_category": ("category", "fundCategory"),
        "aum": ("totalAssets",), "expense_ratio": ("annualReportExpenseRatio",), "nav": ("navPrice",),
    }
    output = []
    for request in request_list:
        ticker = str(request.get("ticker") or "").strip().upper()
        provider_symbol = str(request.get("provider_symbol") or "").strip().upper()
        mode = str(request.get("mode") or "")
        fields = list(request.get("fields") or [])
        allowed = mode_fields(mode) if mode in {"identity", "equity-classification", "etf-classification"} else None
        if not ticker or not provider_symbol or allowed is None or not set(fields).issubset(allowed):
            raise ValueError("Invalid constrained yfinance request")
        item = {"ticker": ticker, "mode": mode, "attempted_fields": fields, "fields": {}, "error": None}
        try:
            client = ticker_factory(provider_symbol)
            info = client.get_info() or {}
            funds_data = getattr(client, "funds_data", None) if mode == "etf-classification" else None
            for field in fields:
                value = next((info.get(key) for key in key_map.get(field, ()) if not _is_missing(info.get(key))), None)
                if field == "top_holdings" and funds_data is not None:
                    value = getattr(funds_data, "top_holdings", None)
                if field == "sector_weights" and funds_data is not None:
                    value = getattr(funds_data, "sector_weightings", None)
                if not _is_missing(value):
                    item["fields"][field] = _json_value(value)
        except Exception as exc:  # Per-ticker failures are part of the output contract.
            item["error"] = f"{type(exc).__name__}: {exc}"
        output.append(item)
    return output


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if not isinstance(request, list):
        raise SystemExit("request must be a JSON list")
    json.dump(fetch_classification_data(request), sys.stdout)
    sys.stdout.write("\n")
