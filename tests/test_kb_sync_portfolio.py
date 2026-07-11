import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-sync-portfolio" / "scripts"))

import kb_pages
import sync_portfolio_pages


CLASSIFICATION = {
    "schema_version": "1.0",
    "generated_at": "2026-07-11T00:00:00Z",
    "workflow": "classify-my-portfolio",
    "database_mode": "read_only",
    "summary": {"holding_count": 2, "classified_count": 2, "review_count": 1, "enrichment_attempted": 2, "enrichment_failed": 0},
    "holdings": [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "primary_group": "Quality",
            "secondary_tags": ["Equity", "Technology"],
            "confidence": "high",
            "reasoning": "Quality compounder.",
            "evidence_used": ["manual_override:AAPL"],
            "missing_data": [],
            "review_needed": False,
            "fields": {
                "currency": "USD",
                "sector": "Technology",
                "dividend_yield": 0.35,
                "quantity": 0.5058,
                "position_market_value": 157.13,
                "current_weight_percent": 60.0,
            },
            "field_provenance": {},
            "enrichment": {},
        },
        {
            "ticker": "XEQT",
            "company_name": "iShares Core Equity ETF",
            "primary_group": "Core",
            "secondary_tags": ["ETF"],
            "confidence": "medium",
            "reasoning": "Broad market core holding.",
            "evidence_used": [],
            "missing_data": ["expense_ratio"],
            "review_needed": True,
            "fields": {
                "currency": "CAD",
                "sector": None,
                "dividend_yield": None,
                "quantity": 10.0,
                "position_market_value": 300.0,
                "current_weight_percent": 40.0,
            },
            "field_provenance": {},
            "enrichment": {},
        },
    ],
}

POLICY = {
    "primary_groups": ["Core", "Income", "Quality", "Growth", "Alternatives", "Cash", "Needs Review"],
    "allocation_targets": {
        "Core": {"target_percent": 60, "min_percent": 55, "max_percent": 70},
        "Quality": {"target_percent": 15, "min_percent": 10, "max_percent": 20},
    },
}


class SyncPortfolioPagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kb_root = Path(self.tmp.name) / "Knowledge-Base"
        (self.kb_root / "portfolio").mkdir(parents=True)
        (self.kb_root / "stocks").mkdir(parents=True)
        (self.kb_root / "logs").mkdir(parents=True)
        (self.kb_root / "logs" / "update-log.md").write_text(
            "# Update Log\n\n| Date | Action | Page | Note |\n|---|---|---|---|\n", encoding="utf-8"
        )
        for name in ("portfolio", ""):
            pass
        self._write_index(self.kb_root / "portfolio" / "index.md")
        self._write_index(self.kb_root / "index.md", is_main=True)

        self.classification_path = Path(self.tmp.name) / "portfolio-classification.json"
        self.classification_path.write_text(json.dumps(CLASSIFICATION), encoding="utf-8")

        self.policy_path = Path(self.tmp.name) / "policy_v1_1.yaml"
        import yaml

        self.policy_path.write_text(yaml.safe_dump(POLICY), encoding="utf-8")

        self.kb_root_patch = patch.object(kb_pages, "KB_ROOT", self.kb_root)
        self.kb_root_patch.start()
        self.addCleanup(self.kb_root_patch.stop)
        self.policy_patch = patch.object(sync_portfolio_pages, "POLICY_FILE", self.policy_path)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def _write_index(self, path: Path, is_main=False):
        title = "Research Wiki" if is_main else "Portfolio"
        path.write_text(
            f"---\ntitle: {title}\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
            "created: 2026-07-01\nupdated: 2026-07-01\nsummary: idx\n---\n\n"
            "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n",
            encoding="utf-8",
        )

    def _run(self, extra_args=None):
        argv = ["--classification", str(self.classification_path)] + (extra_args or [])
        with patch.object(sys, "argv", ["sync_portfolio_pages.py", *argv]):
            return sync_portfolio_pages.main()

    def test_creates_holdings_and_overview_pages(self):
        rc = self._run()
        self.assertEqual(rc, 0)

        holdings = (self.kb_root / "portfolio" / "holdings.md").read_text(encoding="utf-8")
        meta, body = kb_pages.parse_page(holdings)
        self.assertEqual(meta["status"], "generated")
        self.assertEqual(sorted(meta["tickers"]), ["AAPL", "XEQT"])
        self.assertIn("AAPL", body)
        self.assertIn("XEQT", body)
        self.assertIn("Needs Review", body)

        overview = (self.kb_root / "portfolio" / "portfolio-overview.md").read_text(encoding="utf-8")
        ometa, obody = kb_pages.parse_page(overview)
        self.assertEqual(ometa["status"], "generated")
        self.assertIn("Core", obody)
        self.assertIn("Quality", obody)

    def test_check_mode_writes_nothing_when_up_to_date(self):
        self.assertEqual(self._run(), 0)
        rc = self._run(["--check"])
        self.assertEqual(rc, 0)

    def test_check_mode_reports_out_of_date_before_first_run(self):
        rc = self._run(["--check"])
        self.assertEqual(rc, 1)
        self.assertFalse((self.kb_root / "portfolio" / "holdings.md").exists())

    def test_second_run_with_no_data_change_is_idempotent(self):
        self._run()
        first = (self.kb_root / "portfolio" / "holdings.md").read_text(encoding="utf-8")
        self._run()
        second = (self.kb_root / "portfolio" / "holdings.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_index_and_update_log_are_updated(self):
        self._run()
        index_text = (self.kb_root / "portfolio" / "index.md").read_text(encoding="utf-8")
        self.assertIn("holdings.md", index_text)
        log_text = (self.kb_root / "logs" / "update-log.md").read_text(encoding="utf-8")
        self.assertIn("synced", log_text)

    def test_stock_page_link_included_when_present(self):
        (self.kb_root / "stocks" / "AAPL.md").write_text(
            "---\ntitle: AAPL\ntype: stock-page\ntickers: [AAPL]\ntags: []\nstatus: active\n"
            "created: 2026-07-01\nupdated: 2026-07-01\nsummary: x\n---\n\n# AAPL\n",
            encoding="utf-8",
        )
        self._run()
        holdings = (self.kb_root / "portfolio" / "holdings.md").read_text(encoding="utf-8")
        self.assertIn("(../stocks/AAPL.md)", holdings)

    def test_missing_classification_file_errors(self):
        missing = Path(self.tmp.name) / "nope.json"
        with patch.object(sys, "argv", ["sync_portfolio_pages.py", "--classification", str(missing)]):
            rc = sync_portfolio_pages.main()
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
