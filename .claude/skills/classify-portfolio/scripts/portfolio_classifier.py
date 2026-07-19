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


def _record_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _record_text(record: Mapping[str, Any], *keys: str) -> str:
    return _normalize_text(_record_value(record, *keys))


def _user_thesis_text(record: Mapping[str, Any]) -> str:
    return _record_text(record, "user_thesis", "reason_for_buying", "notes", "intended_role")


def _keyword_hit(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords if _normalize_text(keyword))


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


def _alternatives_signals(record: Mapping[str, Any]) -> bool:
    """Supporting signal for the Gold/Hedge secondary tags only -- primary
    group assignment comes from decision_rules, not this helper."""
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

    user_thesis = _user_thesis_text(record)
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


def _review_flag(record: Mapping[str, Any], override: Mapping[str, Any] | None, primary_group: str) -> bool:
    if primary_group == "Needs Review":
        return True
    if override and _is_truthy(override.get("review_needed")):
        return True
    return any(
        _is_truthy(record.get(field))
        for field in ("review_needed", "manual_review_flag")
    )


# --- Rule interpreter for classification_rules_v1_1.yaml -------------------
#
# The reference YAML documents a rule engine (`rule_priority` -> ordered
# `decision_rules` tiers, each tier an ordered list of `when`/
# `assign_primary_group`/`confidence` rules). This interpreter reads that
# YAML directly instead of re-encoding the same decisions as hardcoded
# Python, so editing the YAML actually changes classifier behavior.

def _condition_matches(condition: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    for key, expected in condition.items():
        if key == "asset_class":
            actual = _normalize_lower(_record_value(record, "asset_class", "asset"))
            if actual != _normalize_lower(expected):
                return False
        elif key == "asset_class_any":
            actual = _normalize_lower(_record_value(record, "asset_class", "asset"))
            if actual not in {_normalize_lower(item) for item in expected}:
                return False
        elif key == "etf_category_any":
            actual = _normalize_lower(_record_text(record, "etf_category"))
            if actual not in {_normalize_lower(item) for item in expected}:
                return False
        elif key == "sector_any":
            actual = _record_text(record, "sector")
            if actual not in {str(item) for item in expected}:
                return False
        elif key == "user_thesis_contains_any":
            if not _keyword_hit(_user_thesis_text(record), expected):
                return False
        else:
            raise ValueError(f"Unsupported rule condition in classification_rules_v1_1.yaml: {key}")
    return True


def _evaluate_rule_tier(
    tier: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[str, str, str, str] | None:
    """Return (primary_group, rule_id, confidence, reasoning) for the first
    matching rule in a `decision_rules` tier, in YAML declaration order."""
    for rule in tier.get("rules", []):
        if _condition_matches(rule.get("when", {}), record):
            rule_id = str(rule.get("id", "unnamed_rule"))
            return (
                str(rule["assign_primary_group"]),
                rule_id,
                str(rule.get("confidence", "medium")),
                f"Matched rule {rule_id}.",
            )
    return None


def _evaluate_sector_bias_tier(
    tier: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[str, str, str, str] | None:
    """`sector_industry_bias_rules` entries are a default preference order,
    not a `when`/`assign_primary_group` rule -- pick the first group in the
    matching sector's `default_group_bias` list."""
    sector = _record_text(record, "sector")
    if not sector:
        return None
    for bias in tier.get("rules", []):
        if _normalize_text(bias.get("sector")) == sector:
            groups = [str(group) for group in bias.get("default_group_bias", [])]
            if groups:
                bias_id = str(bias.get("id", f"sector_bias:{sector}"))
                return (groups[0], bias_id, "low", f"Default sector bias for {sector}.")
    return None


def _run_rule_engine(bundle: PortfolioGroupingBundle, record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Walk `rule_priority` tiers in order; return the first match, or a
    Needs Review fallback if nothing matches."""
    decision_rules = bundle.rules.get("decision_rules", {})
    for tier_name in bundle.rules.get("rule_priority", []):
        if tier_name in {"manual_overrides", "fallback_needs_review"}:
            continue
        tier = decision_rules.get(tier_name, {})
        if tier_name == "sector_industry_bias_rules":
            match = _evaluate_sector_bias_tier(tier, record)
        else:
            match = _evaluate_rule_tier(tier, record)
        if match:
            primary_group, rule_id, confidence, reasoning = match
            return primary_group, f"{tier_name}:{rule_id}", confidence, reasoning

    fallback = bundle.rules.get("fallback", {})
    return (
        str(fallback.get("group", "Needs Review")),
        "fallback_needs_review",
        str(fallback.get("confidence", "low")),
        "Insufficient evidence or conflicting signals.",
    )


def classify_holding(record: Mapping[str, Any], bundle: PortfolioGroupingBundle | None = None) -> ClassificationResult:
    bundle = bundle or load_portfolio_grouping_bundle()
    ticker = _normalize_upper(_record_value(record, "ticker", "symbol"))
    company_name = _record_text(record, "company_name", "security_name", "name")

    missing_data = _missing_required_fields(record, bundle.required_fields)
    override = _manual_override(bundle, ticker) if ticker else None

    if override:
        primary_group = str(override.get("primary_group", "Needs Review"))
        tags = list(_secondary_tags(record, override.get("secondary_tags")))
        evidence = [f"manual_override:{ticker}"]
        reasoning = str(override.get("rationale", "Manual override applied."))
        review_needed = _review_flag(record, override, primary_group)
        confidence = "high"
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

    # required_fields_v1_1.yaml: missing asset_class or sector_or_etf_category
    # with no override goes straight to Needs Review, without consulting the
    # rule tiers (a rule could otherwise match on user_thesis alone even when
    # the record lacks the metadata those tiers are meant to require).
    if "asset_class" in missing_data or "sector_or_etf_category" in missing_data:
        tags = list(_secondary_tags(record))
        return ClassificationResult(
            ticker=ticker,
            company_name=company_name,
            primary_group="Needs Review",
            secondary_tags=tags,
            confidence="low",
            reasoning="Missing required classification metadata.",
            evidence_used=("missing_required_metadata",),
            missing_data=tuple(missing_data),
            review_needed=True,
        )

    primary_group, rule_ref, confidence, reasoning = _run_rule_engine(bundle, record)

    if primary_group not in bundle.approved_groups:
        primary_group = "Needs Review"
        rule_ref = "fallback_needs_review"
        confidence = "low"
        reasoning = "Matched rule assigns an unapproved group."

    # required_fields_v1_1.yaml: classifying without a stated user_thesis
    # (i.e. by metadata/rules alone, not explicit intent) caps confidence at
    # medium, even for a nominally "high" confidence rule match.
    if not _user_thesis_text(record) and confidence == "high":
        confidence = "medium"

    if primary_group == "Needs Review":
        confidence = "low"

    tags = list(_secondary_tags(record))
    review_needed = _review_flag(record, None, primary_group) or primary_group == "Needs Review" or bool(missing_data)

    return ClassificationResult(
        ticker=ticker,
        company_name=company_name,
        primary_group=primary_group,
        secondary_tags=tags,
        confidence=confidence,
        reasoning=reasoning,
        evidence_used=(rule_ref,),
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
