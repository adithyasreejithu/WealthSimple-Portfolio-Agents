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


if __name__ == "__main__":
    unittest.main()
