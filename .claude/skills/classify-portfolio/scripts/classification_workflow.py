"""Constrained, read-only portfolio classification workflow."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "read-portfolio-classification-data" / "scripts"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "fetch-yfinance-classification-data" / "scripts"))

from config import BASE_DIR, DATABASE_PATH
from read_classification_data import read_classification_data, read_wishlist_classification_data
from fetch_classification_data import fetch_classification_data, mode_fields
from portfolio_classifier import classify_holding, load_portfolio_grouping_bundle


OUTPUT_DIR = (BASE_DIR / "exports" / "portfolio-classification").resolve()
DEFAULT_OUTPUT_PATH = OUTPUT_DIR / "portfolio-classification.json"

RECOMMENDED_FIELDS = (
    "company_name", "asset_class", "currency", "exchange", "sector", "industry",
    "etf_category", "dividend_yield", "market_cap",
)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def enrichment_mode(security_type: Any) -> str:
    normalized = str(security_type or "").strip().upper()
    if normalized == "ETF" or "FUND" in normalized:
        return "etf-classification"
    if normalized in {"EQUITY", "STOCK"}:
        return "equity-classification"
    return "identity"


def build_enrichment_requests(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for record in records:
        mode = enrichment_mode(record.get("asset_class"))
        missing = [field for field in RECOMMENDED_FIELDS if field in mode_fields(mode) and _is_missing(record.get(field))]
        if missing and record.get("provider_symbol"):
            requests.append({"ticker": record["ticker"], "provider_symbol": record["provider_symbol"], "mode": mode, "fields": missing})
    return requests


def merge_enrichment(records: list[dict[str, Any]], enrichment: Iterable[Mapping[str, Any]]) -> None:
    indexed = {record["ticker"]: record for record in records}
    for item in enrichment:
        record = indexed.get(item["ticker"])
        if record is None:
            continue
        populated = []
        for field, value in item.get("fields", {}).items():
            if _is_missing(record.get(field)) and not _is_missing(value):
                record[field] = value
                record["field_provenance"][field] = "yfinance"
                populated.append(field)
        record["enrichment"] = {
            "mode": item["mode"], "attempted_fields": list(item["attempted_fields"]),
            "populated_fields": populated, "errors": [item["error"]] if item.get("error") else [],
        }


def validate_output(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != "1.0" or payload.get("workflow") != "classify-my-portfolio":
        raise ValueError("Invalid classification output envelope")
    holdings = payload.get("holdings")
    if not isinstance(holdings, list):
        raise ValueError("Classification holdings must be a list")
    required = {"ticker", "company_name", "primary_group", "secondary_tags", "confidence", "reasoning", "evidence_used", "missing_data", "review_needed", "fields", "field_provenance", "enrichment"}
    for holding in holdings:
        if not isinstance(holding, dict) or not required.issubset(holding):
            raise ValueError("Classification holding does not match schema 1.0")


def classify_portfolio(
    db_path: str | Path = DATABASE_PATH,
    fetcher: Callable[[Iterable[Mapping[str, Any]]], list[dict[str, Any]]] = fetch_classification_data,
) -> dict[str, Any]:
    # Owned holdings plus declared wishlist tickers -- classifying both in one
    # pass is what makes the full-replace sync in
    # `database_command.upload_portfolio_classifications` safe (see that
    # function's docstring): nothing legitimately classified is ever missing
    # from a current export.
    records = read_classification_data(db_path) + read_wishlist_classification_data(db_path)
    requests = build_enrichment_requests(records)
    enrichment = fetcher(requests) if requests else []
    merge_enrichment(records, enrichment)
    bundle = load_portfolio_grouping_bundle()
    holdings = []
    for record in records:
        record.setdefault("enrichment", {"mode": enrichment_mode(record.get("asset_class")), "attempted_fields": [], "populated_fields": [], "errors": []})
        result = classify_holding(record, bundle)
        fields = {key: value for key, value in record.items() if key not in {"field_provenance", "enrichment", "ticker", "company_name", "ticker_id"}}
        holdings.append({
            "ticker": result.ticker, "company_name": result.company_name,
            "primary_group": result.primary_group, "secondary_tags": list(result.secondary_tags),
            "confidence": result.confidence, "reasoning": result.reasoning,
            "evidence_used": list(result.evidence_used), "missing_data": list(result.missing_data),
            "review_needed": result.review_needed, "fields": fields,
            "field_provenance": record["field_provenance"], "enrichment": record["enrichment"],
        })
    payload = {
        "schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow": "classify-my-portfolio", "database_mode": "read_only",
        "summary": {
            "holding_count": len(holdings),
            "classified_count": sum(not item["review_needed"] for item in holdings),
            "review_count": sum(item["review_needed"] for item in holdings),
            "enrichment_attempted": len(enrichment),
            "enrichment_failed": sum(bool(item.get("error")) for item in enrichment),
        },
        "holdings": holdings,
    }
    validate_output(payload)
    return payload


def resolve_output_path(value: str | Path | None) -> Path:
    path = Path(value).expanduser() if value is not None else DEFAULT_OUTPUT_PATH
    path = (BASE_DIR / path).resolve() if not path.is_absolute() else path.resolve()
    if path != OUTPUT_DIR and OUTPUT_DIR not in path.parents:
        raise ValueError(f"Output must be inside {OUTPUT_DIR}")
    if path.suffix.lower() != ".json":
        raise ValueError("Output filename must end in .json")
    return path


def write_output(payload: Mapping[str, Any], output_path: str | Path | None = None, pretty: bool = False) -> Path:
    path = resolve_output_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2 if pretty else None, sort_keys=pretty) + "\n", encoding="utf-8")
    return path
