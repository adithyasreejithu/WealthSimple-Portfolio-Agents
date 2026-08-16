from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "kb-sync-portfolio" / "scripts")
)

import sync_portfolio_pages as sync_pages


def _holding(ticker, ownership_status="owned", review_needed=False):
    return {
        "ticker": ticker, "company_name": f"{ticker} Inc", "primary_group": "Quality",
        "secondary_tags": [], "confidence": "high", "reasoning": "test",
        "evidence_used": [], "missing_data": [], "review_needed": review_needed,
        "fields": {"ownership_status": ownership_status, "current_weight_percent": 50.0, "position_market_value": 100.0},
        "field_provenance": {}, "enrichment": {},
    }


class LoadClassificationOwnershipFilterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _write(self, holdings, summary_extra=None):
        path = Path(self.temp_dir.name) / "portfolio-classification.json"
        path.write_text(json.dumps({
            "schema_version": "1.0", "generated_at": "2026-01-01T00:00:00+00:00",
            "workflow": "classify-my-portfolio", "database_mode": "read_only",
            "summary": {
                "holding_count": len(holdings), "classified_count": len(holdings),
                "review_count": sum(h["review_needed"] for h in holdings),
                **(summary_extra or {}),
            },
            "holdings": holdings,
        }))
        return path

    def test_wishlist_holdings_are_excluded(self):
        path = self._write([_holding("AAPL", "owned"), _holding("MP", "wishlist")])
        data = sync_pages.load_classification(path)
        self.assertEqual([h["ticker"] for h in data["holdings"]], ["AAPL"])

    def test_summary_counts_are_recomputed_against_owned_only(self):
        path = self._write([
            _holding("AAPL", "owned"), _holding("MSFT", "owned", review_needed=True),
            _holding("MP", "wishlist", review_needed=True),
        ])
        data = sync_pages.load_classification(path)
        self.assertEqual(data["summary"]["holding_count"], 2)
        self.assertEqual(data["summary"]["review_count"], 1)

    def test_holdings_with_no_ownership_status_field_default_to_owned(self):
        """Backward compatibility with a pre-Feature-A export that never set
        `ownership_status` at all."""
        holding = _holding("AAPL")
        del holding["fields"]["ownership_status"]
        path = self._write([holding])
        data = sync_pages.load_classification(path)
        self.assertEqual([h["ticker"] for h in data["holdings"]], ["AAPL"])


if __name__ == "__main__":
    unittest.main()
