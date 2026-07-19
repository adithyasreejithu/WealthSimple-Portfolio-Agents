import sys
import unittest
from pathlib import Path

# The classifier modules live beside the classify-portfolio skill, not in src/.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "classify-portfolio" / "scripts")
)

import portfolio_classifier


class PortfolioClassifierTest(unittest.TestCase):
    def test_sample_tickers_classify_into_expected_groups(self):
        results = {result.ticker: result for result in portfolio_classifier.classify_sample_portfolio()}

        expectations = {
            "XEQT": ("Core", False),
            "VFV": ("Core", False),
            "INDA": ("Growth", False),
            "AAPL": ("Quality", False),
            "MSFT": ("Quality", False),
            "NVDA": ("Growth", False),
            "PLTR": ("Growth", False),
            "RY": ("Quality", False),
            "TD": ("Quality", False),
            "BN": ("Quality", True),
            "ZGLD": ("Alternatives", False),
        }

        self.assertEqual(set(results), set(expectations))
        for ticker, (expected_group, expected_review) in expectations.items():
            with self.subTest(ticker=ticker):
                result = results[ticker]
                self.assertEqual(result.primary_group, expected_group)
                self.assertEqual(result.review_needed, expected_review)
                self.assertTrue(result.secondary_tags)

    def test_inda_carries_thematic_growth_tags(self):
        inda = next(
            result for result in portfolio_classifier.classify_sample_portfolio()
            if result.ticker == "INDA"
        )

        self.assertIn("Thematic Growth", inda.secondary_tags)
        self.assertIn("Bought On Whim", inda.secondary_tags)
        self.assertEqual(inda.primary_group, "Growth")

    def test_missing_metadata_is_reported_in_needs_review(self):
        result = portfolio_classifier.classify_holding({"ticker": "UNKNOWN"})

        self.assertEqual(result.primary_group, "Needs Review")
        self.assertTrue(result.review_needed)
        self.assertIn("company_name", result.missing_data)
        self.assertIn("asset_class", result.missing_data)
        self.assertIn("currency", result.missing_data)
        self.assertIn("sector_or_etf_category", result.missing_data)


class RuleEngineTest(unittest.TestCase):
    """Exercises classification_rules_v1_1.yaml's decision_rules tiers
    directly, for tickers with no manual_overrides.yaml entry (the sample
    portfolio and DEFAULT overrides tests never reach the rule engine
    itself, since every sample ticker has an override)."""

    def setUp(self):
        self.bundle = portfolio_classifier.load_portfolio_grouping_bundle()

    def test_etf_category_rule_assigns_core_without_override(self):
        result = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZCORE",
                "company_name": "Unlisted Core ETF",
                "asset_class": "ETF",
                "currency": "CAD",
                "etf_category": "global_equity",
                "sector": "Equity",
            },
            self.bundle,
        )
        self.assertEqual(result.primary_group, "Core")
        self.assertEqual(result.evidence_used, ("asset_class_and_etf_type_rules:broad_market_etf_to_core",))

    def test_cash_asset_class_assigns_cash(self):
        result = portfolio_classifier.classify_holding(
            {"ticker": "CASH", "company_name": "Cash Balance", "asset_class": "cash", "currency": "CAD", "sector": "Cash"},
            self.bundle,
        )
        self.assertEqual(result.primary_group, "Cash")

    def test_user_thesis_role_rules_cover_core_quality_and_alternatives(self):
        # These three role_* rules (Core via equity thesis, Quality via
        # "moat"/"compounder" wording, Alternatives via "hedge"/"low
        # correlation" wording) previously had no Python implementation at
        # all -- only role_growth and role_income were hardcoded.
        core = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZTHESISCORE", "company_name": "Test Co", "asset_class": "equity",
                "currency": "USD", "sector": "Industrials", "user_thesis": "core index-like broad market holding",
            },
            self.bundle,
        )
        self.assertEqual(core.primary_group, "Core")

        quality = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZTHESISQUAL", "company_name": "Test Co", "asset_class": "equity",
                "currency": "USD", "sector": "Consumer Cyclical", "user_thesis": "durable moat compounder",
            },
            self.bundle,
        )
        self.assertEqual(quality.primary_group, "Quality")

        alternatives = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZTHESISALT", "company_name": "Test Co", "asset_class": "equity",
                "currency": "USD", "sector": "Basic Materials", "user_thesis": "low correlation hedge position",
            },
            self.bundle,
        )
        self.assertEqual(alternatives.primary_group, "Alternatives")

    def test_sector_bias_tier_covers_every_sector_in_the_taxonomy(self):
        # security_grouping_reference_v1_1.yaml defines default_group_bias
        # for 11 sectors; classification_rules_v1_1.yaml's
        # sector_industry_bias_rules tier must cover the same 11, or a
        # holding in an uncovered sector silently falls through to Needs
        # Review instead of getting a sector-bias default.
        reference_sectors = set(self.bundle.security_reference.get("sectors", {}))
        rule_sectors = {
            bias.get("sector")
            for bias in self.bundle.rules["decision_rules"]["sector_industry_bias_rules"]["rules"]
        }
        self.assertEqual(reference_sectors, rule_sectors)

    def test_sector_bias_tier_assigns_first_listed_group(self):
        result = portfolio_classifier.classify_holding(
            {"ticker": "ZZZENERGY", "company_name": "Test Energy Co", "asset_class": "equity", "currency": "CAD", "sector": "Energy"},
            self.bundle,
        )
        self.assertEqual(result.primary_group, "Income")
        self.assertEqual(result.confidence, "low")

    def test_confidence_capped_at_medium_without_user_thesis(self):
        # asset_class_and_etf_type_rules rules are nominally "high"
        # confidence in the YAML, but required_fields_v1_1.yaml caps
        # rule-only (no stated user_thesis) classification at medium.
        result = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZCORE2", "company_name": "Unlisted Core ETF 2", "asset_class": "ETF",
                "currency": "CAD", "etf_category": "global_equity", "sector": "Equity",
            },
            self.bundle,
        )
        self.assertEqual(result.confidence, "medium")

    def test_confidence_reaches_high_with_matching_user_thesis(self):
        result = portfolio_classifier.classify_holding(
            {
                "ticker": "ZZZTHESISCORE2", "company_name": "Test Co", "asset_class": "equity",
                "currency": "USD", "sector": "Industrials", "user_thesis": "core broad market holding",
            },
            self.bundle,
        )
        self.assertEqual(result.confidence, "high")

    def test_manual_override_still_takes_priority_over_rule_engine(self):
        # AAPL's rule-engine tiers alone (Technology sector, no thesis)
        # would match ticker_or_security_metadata_rules -> Quality anyway,
        # so use RY (Financial Services) which would also land on Quality
        # via the metadata tier, but here we confirm the override's own
        # tags/rationale win, not the rule engine's generic reasoning.
        result = portfolio_classifier.classify_holding(
            {"ticker": "RY", "company_name": "Royal Bank of Canada", "asset_class": "equity", "currency": "CAD", "sector": "Financial Services"},
            self.bundle,
        )
        self.assertEqual(result.primary_group, "Quality")
        self.assertEqual(result.evidence_used, ("manual_override:RY",))
        self.assertIn("Canadian blue-chip bank", result.reasoning)


if __name__ == "__main__":
    unittest.main()
