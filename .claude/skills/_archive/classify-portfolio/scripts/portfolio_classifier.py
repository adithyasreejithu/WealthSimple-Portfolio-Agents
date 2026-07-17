from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from config import PORTFOLIO_GROUPING_FOLDER


@dataclass(frozen=True)
class ClassificationResult:
    ticker: str
    company_name: str
    primary_group: str
    secondary_tags: tuple[str, ...]
    confidence: str
    reasoning: str
    evidence_used: tuple[str, ...]
    missing_data: tuple[str, ...]
    review_needed: bool


@dataclass(frozen=True)
class PortfolioGroupingBundle:
    policy: Mapping[str, Any]
    rules: Mapping[str, Any]
    security_reference: Mapping[str, Any]
    manual_overrides: Mapping[str, Any]
    required_fields: Mapping[str, Any]
    examples: Mapping[str, Any]

    @property
    def approved_groups(self) -> tuple[str, ...]:
        groups = self.rules.get("approved_groups") or self.policy.get("primary_groups") or []
        return tuple(str(group) for group in groups)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected mapping at {path}")
    return loaded


@lru_cache(maxsize=1)
def load_portfolio_grouping_bundle(base_dir: Path | None = None) -> PortfolioGroupingBundle:
    root = Path(base_dir or PORTFOLIO_GROUPING_FOLDER)
    return PortfolioGroupingBundle(
        policy=_read_yaml(root / "policy_v1_1.yaml"),
        rules=_read_yaml(root / "classification_rules_v1_1.yaml"),
        security_reference=_read_yaml(root / "security_grouping_reference_v1_1.yaml"),
        manual_overrides=_read_yaml(root / "manual_overrides_v1_1.yaml"),
        required_fields=_read_yaml(root / "required_fields_v1_1.yaml"),
        examples=_read_yaml(root / "group_examples_v1_1.yaml"),
    )


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _normalize_upper(value: Any) -> str:
    return _normalize_text(value).upper()


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return _normalize_lower(value) in {"1", "true", "yes", "y", "on"}


def _normalize_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [str(item) for item in value if _normalize_text(item)]
    return [_normalize_text(value)]


def _unique_tags(tags: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        clean = _normalize_text(tag)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return tuple(ordered)


def _parse_float(value: Any) -> float | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _record_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _record_text(record: Mapping[str, Any], *keys: str) -> str:
    return _normalize_text(_record_value(record, *keys))


def _missing_required_fields(record: Mapping[str, Any], required_fields: Mapping[str, Any]) -> list[str]:
    minimum = required_fields.get("required_inputs", {}).get("minimum_for_basic_classification", [])
    missing: list[str] = []
    for field in minimum:
        if field == "company_name":
            if not _record_text(record, "company_name", "security_name", "name"):
                missing.append("company_name")
            continue
        if field == "sector_or_etf_category":
            if not _record_text(record, "sector", "etf_category"):
                missing.append("sector_or_etf_category")
            continue
        if not _record_text(record, field):
            missing.append(field)
    return missing


def _manual_override(bundle: PortfolioGroupingBundle, ticker: str) -> dict[str, Any] | None:
    overrides = bundle.manual_overrides.get("overrides", [])
    for override in overrides:
        if _normalize_upper(override.get("ticker")) == ticker:
            if _is_truthy(override.get("active", True)):
                return override
    return None


def _sector_bias(bundle: PortfolioGroupingBundle, sector: str) -> list[str]:
    sectors = bundle.security_reference.get("sectors", {})
    entry = sectors.get(sector, {}) if isinstance(sectors, dict) else {}
    return [str(group) for group in entry.get("default_group_bias", [])]


def _etf_bias(bundle: PortfolioGroupingBundle, etf_category: str) -> str | None:
    categories = bundle.security_reference.get("etf_categories", {})
    entry = categories.get(etf_category, {}) if isinstance(categories, dict) else {}
    bias = entry.get("primary_group_bias")
    return str(bias) if bias else None


def _keyword_hit(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords if _normalize_text(keyword))


def _classify_by_etf(
    bundle: PortfolioGroupingBundle,
    record: Mapping[str, Any],
    evidence: list[str],
    tags: list[str],
) -> tuple[str | None, str]:
    etf_category = _record_text(record, "etf_category")
    asset_class = _normalize_lower(_record_value(record, "asset_class", "asset"))
    etf_bias = _etf_bias(bundle, etf_category)

    if asset_class == "commodity_etf":
        evidence.append("asset_class:commodity_etf")
        tags.extend(["ETF", "Gold", "Hedge"])
        return "Alternatives", "commodity_etf"
    if asset_class == "cash":
        evidence.append("asset_class:cash")
        return "Cash", "cash"

    if asset_class != "etf":
        return None, ""

    if etf_category:
        evidence.append(f"etf_category:{etf_category}")

    core_categories = {"global_equity", "total_market", "broad_us_equity", "broad_international_equity"}
    income_categories = {"dividend_growth", "high_dividend", "canadian_dividend", "covered_banking_income"}
    growth_categories = {"semiconductor", "artificial_intelligence", "thematic_growth", "country_specific_emerging_markets"}
    alternative_categories = {"gold"}

    if etf_category in core_categories:
        tags.append("ETF")
        return "Core", "core etf category"
    if etf_category in income_categories:
        tags.append("ETF")
        return "Income", "income etf category"
    if etf_category in growth_categories:
        tags.append("ETF")
        return "Growth", "growth etf category"
    if etf_category in alternative_categories:
        tags.append("ETF")
        return "Alternatives", "alternative etf category"

    if etf_bias:
        tags.append("ETF")
        return etf_bias, "etf category bias"

    return None, ""


def _quality_signals(record: Mapping[str, Any], bundle: PortfolioGroupingBundle, ticker: str) -> bool:
    sector = _record_text(record, "sector")
    industry = _record_text(record, "industry")
    name = _record_text(record, "company_name", "security_name", "name")
    examples = bundle.policy.get("groups", {}).get("Quality", {}).get("examples", [])
    if ticker in {str(example).upper() for example in examples}:
        return True
    if any(term in industry.lower() for term in ("bank", "asset management", "telecom", "defense", "aerospace")):
        return True
    if sector in {"Financial Services", "Communication Services", "Industrials", "Consumer Defensive", "Healthcare", "Technology"}:
        return True
    if any(term in name.lower() for term in ("bank", "telecom", "defense", "microsoft", "apple", "alphabet", "meta", "berkshire")):
        return True
    return False


def _growth_signals(record: Mapping[str, Any], ticker: str) -> bool:
    sector = _record_text(record, "sector")
    industry = _record_text(record, "industry")
    name = _record_text(record, "company_name", "security_name", "name")
    user_thesis = _record_text(record, "user_thesis", "reason_for_buying", "notes", "intended_role")
    if _keyword_hit(user_thesis, ("growth", "ai", "semiconductor", "thematic", "satellite", "speculative")):
        return True
    if sector == "Technology" and any(term in industry.lower() for term in ("semiconductor", "software", "information technology")):
        return True
    if sector in {"Technology", "Healthcare"} and any(term in name.lower() for term in ("ai", "semiconductor", "chip", "software")):
        return True
    if ticker in {"NVDA", "PLTR", "SMH", "DRAM", "AXTI", "HIMS", "NBIS", "INDA"}:
        return True
    return False


def _income_signals(record: Mapping[str, Any]) -> bool:
    user_thesis = _record_text(record, "user_thesis", "reason_for_buying", "notes", "intended_role")
    dividend_yield = _parse_float(_record_value(record, "dividend_yield", "yield"))
    if _keyword_hit(user_thesis, ("income", "yield", "cash flow", "dividend income", "high yield")):
        return True
    if dividend_yield is not None and dividend_yield >= 0.04:
        # High yield alone is not enough for blue-chip equities, but it is a
        # useful supporting signal for explicit income-first holdings.
        return True
    return False


def _alternatives_signals(record: Mapping[str, Any]) -> bool:
    asset_class = _normalize_lower(_record_value(record, "asset_class", "asset"))
    sector = _record_text(record, "sector")
    industry = _record_text(record, "industry")
    etf_category = _record_text(record, "etf_category")
    if asset_class in {"commodity_etf", "cash"}:
        return True
    if etf_category in {"gold"}:
        return True
    if sector == "Basic Materials" and "gold" in industry.lower():
        return True
    return False


_ETF_CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "global_equity": ("Global", "Broad Market"),
    "total_market": ("Broad Market",),
    "broad_us_equity": ("United States", "Broad Market"),
    "broad_international_equity": ("International", "Broad Market"),
    "dividend_growth": ("Dividend Growth",),
    "high_dividend": ("High Dividend",),
    "canadian_dividend": ("Canadian Dividend",),
    "covered_banking_income": ("Covered Banking Income",),
    "semiconductor": ("Semiconductors",),
    "artificial_intelligence": ("AI",),
    "thematic_growth": ("Thematic Growth",),
    "country_specific_emerging_markets": ("India", "Emerging Markets", "Thematic Growth"),
    "gold": ("Gold",),
}


def _secondary_tags(record: Mapping[str, Any], override_tags: Sequence[str] | None = None) -> tuple[str, ...]:
    tags: list[str] = []
    asset_class = _record_text(record, "asset_class", "asset")
    sector = _record_text(record, "sector")
    industry = _record_text(record, "industry")
    currency = _record_text(record, "currency")
    etf_category = _record_text(record, "etf_category")

    if override_tags:
        tags.extend(override_tags)
    if asset_class:
        tags.append(asset_class.upper() if asset_class.lower() == "etf" else asset_class.title())
    if sector:
        tags.append(sector)
    if industry:
        tags.append(industry)
    if currency:
        tags.append(currency.upper())
    if etf_category:
        tags.extend(_ETF_CATEGORY_TAGS.get(etf_category, (etf_category.replace("_", " ").title(),)))

    user_thesis = _record_text(record, "user_thesis", "reason_for_buying", "notes", "intended_role")
    if _keyword_hit(user_thesis, ("ai",)):
        tags.append("AI")
    if _keyword_hit(user_thesis, ("semiconductor",)):
        tags.append("Semiconductors")
    if _keyword_hit(user_thesis, ("thematic", "whim")):
        tags.append("Thematic Growth")
    if _keyword_hit(user_thesis, ("satellite",)):
        tags.append("Satellite")
    if _keyword_hit(user_thesis, ("whim",)):
        tags.append("Bought On Whim")

    if _alternatives_signals(record):
        tags.extend(["Gold", "Hedge"])

    return _unique_tags(tags)


def _confidence_from_source(source: str, missing_data: Sequence[str], review_needed: bool) -> str:
    if source == "manual_override":
        return "high"
    if source in {"etf_rule", "role_rule"}:
        return "high" if not missing_data else "medium"
    if source in {"sector_bias", "quality_bias", "growth_bias"}:
        return "medium"
    if review_needed or missing_data:
        return "low"
    return "medium"


def _review_flag(record: Mapping[str, Any], override: Mapping[str, Any] | None, primary_group: str) -> bool:
    if primary_group == "Needs Review":
        return True
    if override and _is_truthy(override.get("review_needed")):
        return True
    return any(
        _is_truthy(record.get(field))
        for field in ("review_needed", "manual_review_flag")
    )


def classify_holding(record: Mapping[str, Any], bundle: PortfolioGroupingBundle | None = None) -> ClassificationResult:
    bundle = bundle or load_portfolio_grouping_bundle()
    ticker = _normalize_upper(_record_value(record, "ticker", "symbol"))
    company_name = _record_text(record, "company_name", "security_name", "name")

    missing_data = _missing_required_fields(record, bundle.required_fields)
    override = _manual_override(bundle, ticker) if ticker else None

    evidence: list[str] = []
    if override:
        primary_group = str(override.get("primary_group", "Needs Review"))
        tags = list(_secondary_tags(record, override.get("secondary_tags")))
        evidence.append(f"manual_override:{ticker}")
        reasoning = str(override.get("rationale", "Manual override applied."))
        review_needed = _review_flag(record, override, primary_group)
        confidence = _confidence_from_source("manual_override", missing_data, review_needed)
        if primary_group not in bundle.approved_groups:
            primary_group = "Needs Review"
            review_needed = True
            confidence = "low"
        return ClassificationResult(
            ticker=ticker,
            company_name=company_name,
            primary_group=primary_group,
            secondary_tags=tags,
            confidence=confidence,
            reasoning=reasoning,
            evidence_used=tuple(evidence),
            missing_data=tuple(missing_data),
            review_needed=review_needed,
        )

    tags = list(_secondary_tags(record))
    source = "fallback"
    primary_group: str | None = None
    reasoning = ""

    asset_class = _normalize_lower(_record_value(record, "asset_class", "asset"))
    if asset_class in {"etf", "commodity_etf", "cash"}:
        primary_group, reason = _classify_by_etf(bundle, record, evidence, tags)
        if primary_group:
            source = "etf_rule"
            reasoning = reason

    if not primary_group:
        user_thesis = _record_text(record, "user_thesis", "reason_for_buying", "notes", "intended_role")
        sector = _record_text(record, "sector")
        if _income_signals(record):
            primary_group = "Income"
            source = "role_rule"
            reasoning = "Income-first wording or high-yield signal present."
            evidence.append("income_signal")
        elif _growth_signals(record, ticker):
            primary_group = "Growth"
            source = "role_rule"
            reasoning = "Growth or thematic satellite signal present."
            evidence.append("growth_signal")
        elif _quality_signals(record, bundle, ticker):
            primary_group = "Quality"
            source = "quality_bias"
            reasoning = "Blue-chip / durable compounder signal present."
            evidence.append("quality_signal")
        else:
            biases = _sector_bias(bundle, sector)
            if biases:
                evidence.append(f"sector_bias:{sector}")
                for bias in biases:
                    if bias == "Income" and _income_signals(record):
                        primary_group = "Income"
                        source = "sector_bias"
                        reasoning = f"{sector} sector bias plus income signal."
                        break
                    if bias == "Growth" and _growth_signals(record, ticker):
                        primary_group = "Growth"
                        source = "sector_bias"
                        reasoning = f"{sector} sector bias plus growth signal."
                        break
                    if bias == "Quality" and _quality_signals(record, bundle, ticker):
                        primary_group = "Quality"
                        source = "sector_bias"
                        reasoning = f"{sector} sector bias plus quality signal."
                        break
                    if bias == "Alternatives" and _alternatives_signals(record):
                        primary_group = "Alternatives"
                        source = "sector_bias"
                        reasoning = f"{sector} sector bias plus alternatives signal."
                        break

    if not primary_group:
        primary_group = "Needs Review"
        source = "fallback"
        reasoning = "Insufficient evidence or conflicting signals."
        evidence.append("fallback_needs_review")

    if primary_group not in bundle.approved_groups:
        primary_group = "Needs Review"

    review_needed = _review_flag(record, None, primary_group) or primary_group == "Needs Review" or bool(missing_data)
    confidence = _confidence_from_source(source, missing_data, review_needed)
    if source == "fallback":
        confidence = "low"

    return ClassificationResult(
        ticker=ticker,
        company_name=company_name,
        primary_group=primary_group,
        secondary_tags=_secondary_tags(record, override_tags=()),
        confidence=confidence,
        reasoning=reasoning,
        evidence_used=tuple(evidence),
        missing_data=tuple(missing_data),
        review_needed=review_needed,
    )


DEFAULT_SAMPLE_HOLDINGS: tuple[dict[str, Any], ...] = (
    {
        "ticker": "XEQT",
        "company_name": "iShares Core Equity ETF",
        "asset_class": "ETF",
        "currency": "CAD",
        "etf_category": "global_equity",
        "sector": "Equity",
        "user_thesis": "Core broad market exposure",
    },
    {
        "ticker": "VFV",
        "company_name": "Vanguard S&P 500 Index ETF",
        "asset_class": "ETF",
        "currency": "CAD",
        "etf_category": "broad_us_equity",
        "sector": "Equity",
        "user_thesis": "Core broad market exposure",
    },
    {
        "ticker": "INDA",
        "company_name": "iShares MSCI India ETF",
        "asset_class": "ETF",
        "currency": "CAD",
        "etf_category": "country_specific_emerging_markets",
        "sector": "Equity",
        "user_thesis": "Bought on a whim as thematic India exposure",
    },
    {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "asset_class": "equity",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Consumer Electronics",
    },
    {
        "ticker": "MSFT",
        "company_name": "Microsoft Corp.",
        "asset_class": "equity",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Software",
    },
    {
        "ticker": "NVDA",
        "company_name": "NVIDIA Corp.",
        "asset_class": "equity",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Semiconductors",
        "user_thesis": "AI and semiconductor growth satellite",
    },
    {
        "ticker": "PLTR",
        "company_name": "Palantir Technologies",
        "asset_class": "equity",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Software",
        "user_thesis": "AI growth satellite",
    },
    {
        "ticker": "RY",
        "company_name": "Royal Bank of Canada",
        "asset_class": "equity",
        "currency": "CAD",
        "sector": "Financial Services",
        "industry": "Banks",
    },
    {
        "ticker": "TD",
        "company_name": "Toronto-Dominion Bank",
        "asset_class": "equity",
        "currency": "CAD",
        "sector": "Financial Services",
        "industry": "Banks",
    },
    {
        "ticker": "BN",
        "company_name": "Brookfield Corporation",
        "asset_class": "equity",
        "currency": "CAD",
        "sector": "Financial Services",
        "industry": "Asset Management",
        "review_needed": True,
    },
    {
        "ticker": "ZGLD",
        "company_name": "Purpose Gold Bullion Fund",
        "asset_class": "ETF",
        "currency": "CAD",
        "etf_category": "gold",
        "sector": "Basic Materials",
        "industry": "Gold",
    },
)


def classify_sample_portfolio(bundle: PortfolioGroupingBundle | None = None) -> list[ClassificationResult]:
    bundle = bundle or load_portfolio_grouping_bundle()
    return [classify_holding(record, bundle) for record in DEFAULT_SAMPLE_HOLDINGS]


def _print_table(results: Sequence[ClassificationResult]) -> None:
    rows = [
        (
            result.ticker,
            result.primary_group,
            ", ".join(result.secondary_tags),
            result.confidence,
            "yes" if result.review_needed else "no",
            result.reasoning,
            ", ".join(result.missing_data) or "-",
        )
        for result in results
    ]
    headers = ("Ticker", "Primary Group", "Secondary Tags", "Confidence", "Review", "Reasoning", "Missing Data")
    widths = [
        max(len(headers[0]), *(len(row[0]) for row in rows)) if rows else len(headers[0]),
        max(len(headers[1]), *(len(row[1]) for row in rows)) if rows else len(headers[1]),
        max(len(headers[2]), *(len(row[2]) for row in rows)) if rows else len(headers[2]),
        max(len(headers[3]), *(len(row[3]) for row in rows)) if rows else len(headers[3]),
        max(len(headers[4]), *(len(row[4]) for row in rows)) if rows else len(headers[4]),
        max(len(headers[5]), *(len(row[5]) for row in rows)) if rows else len(headers[5]),
        max(len(headers[6]), *(len(row[6]) for row in rows)) if rows else len(headers[6]),
    ]
    print(
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]:<{widths[2]}}  "
        f"{headers[3]:<{widths[3]}}  {headers[4]:<{widths[4]}}  {headers[5]:<{widths[5]}}  "
        f"{headers[6]:<{widths[6]}}"
    )
    print(
        f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  "
        f"{'-' * widths[3]}  {'-' * widths[4]}  {'-' * widths[5]}  {'-' * widths[6]}"
    )
    for row in rows:
        print(
            f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  "
            f"{row[3]:<{widths[3]}}  {row[4]:<{widths[4]}}  {row[5]:<{widths[5]}}  "
            f"{row[6]:<{widths[6]}}"
        )


def _result_to_json(result: ClassificationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["secondary_tags"] = list(result.secondary_tags)
    payload["evidence_used"] = list(result.evidence_used)
    payload["missing_data"] = list(result.missing_data)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify holdings into portfolio groups.")
    parser.add_argument("tickers", nargs="*", help="Optional tickers to classify from the sample set.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bundle = load_portfolio_grouping_bundle()
    if args.tickers:
        sample_map = {record["ticker"]: record for record in DEFAULT_SAMPLE_HOLDINGS}
        results = []
        for ticker in args.tickers:
            record = sample_map.get(_normalize_upper(ticker))
            if record is None:
                results.append(
                    ClassificationResult(
                        ticker=_normalize_upper(ticker),
                        company_name="",
                        primary_group="Needs Review",
                        secondary_tags=tuple(),
                        confidence="low",
                        reasoning="No sample metadata available for this ticker.",
                        evidence_used=("missing_sample_metadata",),
                        missing_data=("company_name", "asset_class", "currency", "sector_or_etf_category"),
                        review_needed=True,
                    )
                )
            else:
                results.append(classify_holding(record, bundle))
    else:
        results = classify_sample_portfolio(bundle)

    if args.json:
        print(json.dumps([_result_to_json(result) for result in results], indent=2, sort_keys=True))
    else:
        _print_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
