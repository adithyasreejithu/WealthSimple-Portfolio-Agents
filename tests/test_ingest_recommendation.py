import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-update-thesis" / "scripts"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb_pages
import rubric as rubric_mod
import thesis_page
import ingest_recommendation as ingest_mod
import _decision_fixtures as fx

# The Decision History note records the version of the rubric the ingest
# validated against (the shipped one), not the artifact's.
SHIPPED_RUBRIC_VERSION = rubric_mod.load_rubric().get("version")


INDEX_STUB = (
    "---\ntitle: {title}\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
    "created: '2026-07-01'\nupdated: '2026-07-01'\nsummary: idx\n---\n\n"
    "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n"
)


def _artifact(base: Path, **overrides) -> dict:
    dim_cite = {
        "valuation": "yfinance:data.valuation.forwardPE",
        "financial_health": "derived:debt_to_equity",
        "growth": "derived:revenue_growth_yoy",
        "earnings_catalysts": "yfinance:data.earnings.calendar",
        "dividend_safety": "yfinance:data.dividends.summary.payoutRatio",
        "market_sentiment": "derived:return_90d",
        "insider_activity": "yfinance:data.insider.purchases",
        "options_activity": "derived:put_call_oi_ratio",
        "portfolio_fit": "classification:holdings.primary_group",
    }
    gate_cite = {
        "solvency": "yfinance:data.valuation.freeCashflow",
        "profitability_or_path": "derived:net_income_latest",
        "data_sufficiency": "derived:groups_ok_count",
    }
    art = {
        "schema": "stock-recommendation.v1",
        "ticker": "AAPL",
        "generated": "2026-07-13",
        "rubric_version": "v1.0",
        "research_sources": {
            "yfinance": {"path": "research.json", "groups_ok": ["overview"] * 11, "groups_failed": {}},
            "classification": {"path": "classification.json"},
        },
        "position": {"held": True, "weight_pct": 3.2, "portfolio_role": "Quality",
                     "dividend_payer": True, "income_role": False, "is_etf": False, "page_exists": False},
        "gates": [{"id": gid, "result": "pass", "evidence": [{"source": f.split(":")[0], "field": f, "value": 1}]} for gid, f in gate_cite.items()],
        "dimensions": [{"id": did, "weight": 0.0, "score": 4, "rationale": "ok", "evidence": [{"source": f.split(":")[0], "field": f, "value": 1}]} for did, f in dim_cite.items()],
        "weighted_score": 4.0,
        "unknown_dimensions": [],
        "proposed": {"action": "Add", "confidence": "High", "time_horizon": "Long-term", "verdict_vs_previous": "unchanged"},
        "narratives": {
            "executive_summary": "Strong compounder; add on fit.",
            "analyst_view": "Rubric says Add; agree.",
            "updated_thesis": "Services keep carrying earnings as hardware matures.\n\nUnchanged and slightly stronger versus the original.",
            "company_overview": "Apple designs and sells consumer electronics and services.",
            "original_thesis": "Bought for durable services-led margin expansion.",
            "bull_case": ["moat"], "bear_case": ["valuation"], "key_risks": ["china"],
            "open_questions": [], "monitoring": ["services"],
            "section_updates": {"Valuation Analysis": "- Forward P/E: 28 — rich\n- Score: 3/5 (fairly valued)"},
        },
        "facts_assumptions_opinions": {"facts": ["fcf 90b"], "assumptions": ["growth"], "opinions": ["add"]},
    }
    art.update(overrides)
    return art


class IngestFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.kb_root = self.base / "Knowledge-Base"
        (self.kb_root / "stocks").mkdir(parents=True)
        (self.kb_root / "stocks" / "index.md").write_text(INDEX_STUB.format(title="Stocks"), encoding="utf-8")
        (self.kb_root / "index.md").write_text(INDEX_STUB.format(title="Research Wiki"), encoding="utf-8")
        for status in ("active", "watchlist", "closed", "rejected"):
            d = self.kb_root / "theses" / status
            d.mkdir(parents=True)
            (d / "index.md").write_text(INDEX_STUB.format(title=f"Theses {status}"), encoding="utf-8")
        (self.kb_root / "logs").mkdir()
        for name, header in (
            ("decision-log.md", "| Date | Tickers | Action | Verdict | Note |\n|---|---|---|---|---|\n"),
            ("update-log.md", "| Date | Action | Page | Note |\n|---|---|---|---|\n"),
        ):
            (self.kb_root / "logs" / name).write_text(f"# {name}\n\n{header}", encoding="utf-8")

        # Source files the artifact cites, next to the artifact.
        (self.base / "research.json").write_text(json.dumps(fx.research_payload()), encoding="utf-8")
        (self.base / "classification.json").write_text(json.dumps(fx.classification_payload()), encoding="utf-8")

        self.kb_patch = patch.object(kb_pages, "KB_ROOT", self.kb_root)
        self.kb_patch.start()
        self.addCleanup(self.kb_patch.stop)

    def _create_page(self, ticker="AAPL", status="active"):
        with patch.object(sys, "argv", ["thesis_page.py", "create", "--ticker", ticker, "--status", status]):
            thesis_page.main()

    def _write_artifact(self, name="AAPL-2026-07-13.json", **overrides):
        path = self.base / name
        path.write_text(json.dumps(_artifact(self.base, **overrides)), encoding="utf-8")
        return path


class IngestTest(IngestFixture):
    def test_status_block_updated_but_portfolio_status_untouched(self):
        self._create_page(status="active")
        result = ingest_mod.ingest(self._write_artifact())
        self.assertEqual(result["action"], "Add")
        _, body = kb_pages.parse_page_file(self.kb_root / "stocks" / "AAPL.md")
        self.assertIn("- Decision: Add", body)
        self.assertIn("- Confidence: High", body)
        self.assertIn("- Time Horizon: Long-term", body)
        self.assertIn("- Portfolio Status: active", body)  # unchanged

    def test_decision_history_row_appended(self):
        self._create_page()
        ingest_mod.ingest(self._write_artifact())
        _, body = kb_pages.parse_page_file(self.kb_root / "stocks" / "AAPL.md")
        self.assertIn("| Add | unchanged |", body)
        self.assertIn(f"Rubric {SHIPPED_RUBRIC_VERSION} score 4.00", body)

    def test_logs_appended(self):
        self._create_page()
        ingest_mod.ingest(self._write_artifact())
        decision_log = (self.kb_root / "logs" / "decision-log.md").read_text(encoding="utf-8")
        self.assertIn("| AAPL | Add | unchanged |", decision_log)
        update_log = (self.kb_root / "logs" / "update-log.md").read_text(encoding="utf-8")
        self.assertIn("recommendation-ingested", update_log)
        self.assertIn("stocks/AAPL.md", update_log)

    def test_original_thesis_untouched(self):
        self._create_page()
        page = self.kb_root / "stocks" / "AAPL.md"
        text = page.read_text(encoding="utf-8").replace(
            "## Original Thesis\n\n", "## Original Thesis\n\nBought for durable services growth.\n"
        )
        page.write_text(text, encoding="utf-8")
        ingest_mod.ingest(self._write_artifact())
        after = page.read_text(encoding="utf-8")
        self.assertIn("Bought for durable services growth.", after)

    def test_missing_page_auto_created(self):
        # No _create_page() call -- ingest must create stocks/AAPL.md itself,
        # seeded from the artifact's position context (held=True -> active).
        result = ingest_mod.ingest(self._write_artifact())
        self.assertEqual(result["action"], "Add")
        page = self.kb_root / "stocks" / "AAPL.md"
        self.assertTrue(page.exists())
        meta, body = kb_pages.parse_page_file(page)
        self.assertEqual(meta["status"], "active")
        self.assertIn("- Portfolio Role: Quality", body)
        self.assertIn("- Forward P/E: 28", body)  # point-form section_updates transcribed
        # On creation, Company Overview and Original Thesis are seeded too.
        self.assertIn("Apple designs and sells consumer electronics", body)
        self.assertIn("durable services-led margin expansion", body)

    def test_invalid_artifact_refused(self):
        self._create_page()
        with self.assertRaises(ingest_mod.IngestError):
            ingest_mod.ingest(self._write_artifact(name="bad.json", schema="wrong"))

    def test_stale_artifact_refused(self):
        self._create_page()
        ingest_mod.ingest(self._write_artifact(name="new.json", generated="2026-07-13"))
        with self.assertRaises(ingest_mod.IngestError):
            ingest_mod.ingest(self._write_artifact(name="old.json", generated="2026-07-01"))

    def test_bad_verdict_refused(self):
        self._create_page()
        art = _artifact(self.base)
        art["proposed"]["verdict_vs_previous"] = "banana"
        path = self.base / "bad-verdict.json"
        path.write_text(json.dumps(art), encoding="utf-8")
        with self.assertRaises(ingest_mod.IngestError):
            ingest_mod.ingest(path)

    def test_page_still_validates_after_ingest(self):
        self._create_page()
        ingest_mod.ingest(self._write_artifact())
        with patch.object(sys, "argv", ["thesis_page.py", "validate", "--path", str(self.kb_root / "stocks" / "AAPL.md")]):
            self.assertEqual(thesis_page.main(), 0)

    def test_creation_seeds_company_overview_and_original_thesis(self):
        # No _create_page(): ingest creates the page and seeds both sections.
        ingest_mod.ingest(self._write_artifact())
        _, body = kb_pages.parse_page_file(self.kb_root / "stocks" / "AAPL.md")
        self.assertIn("Apple designs and sells consumer electronics", body)
        self.assertIn("durable services-led margin expansion", body)

    def test_update_does_not_touch_overview_or_original_thesis(self):
        self._create_page()
        page = self.kb_root / "stocks" / "AAPL.md"
        text = page.read_text(encoding="utf-8")
        text = text.replace("## Company Overview\n\n", "## Company Overview\n\nHand-written overview.\n")
        text = text.replace("## Original Thesis\n\n", "## Original Thesis\n\nHand-written original thesis.\n")
        page.write_text(text, encoding="utf-8")
        ingest_mod.ingest(self._write_artifact())
        after = page.read_text(encoding="utf-8")
        # Page already existed -> ingest never overwrites these, even though the
        # artifact carries company_overview / original_thesis.
        self.assertIn("Hand-written overview.", after)
        self.assertIn("Hand-written original thesis.", after)
        self.assertNotIn("Apple designs and sells consumer electronics", after)

    def test_section_updated_stamped_for_rewritten_gated_sections(self):
        # On create+ingest this artifact rewrites Status, Updated Thesis,
        # Valuation Analysis, Bull/Bear/Key Risks, Monitoring, Analyst View, and
        # (on creation) Company Overview -- each must land in section_updated
        # keyed by its section key, dated today. Non-rewritten gated sections
        # (Market Sentiment, Options Activity, Insider Activity, Portfolio Fit)
        # and non-gated sections (Original Thesis) must be absent.
        ingest_mod.ingest(self._write_artifact())
        meta, _ = kb_pages.parse_page_file(self.kb_root / "stocks" / "AAPL.md")
        section_updated = meta.get("section_updated") or {}
        today = kb_pages.today()
        expected_keys = {
            "status", "updated_thesis", "valuation_analysis", "bull_case",
            "bear_case", "key_risks", "monitoring_checklist", "analyst_view",
            "company_overview",
        }
        self.assertEqual(set(section_updated), expected_keys)
        self.assertTrue(all(v == today for v in section_updated.values()))
        # Not rewritten this run:
        self.assertNotIn("market_sentiment", section_updated)
        self.assertNotIn("options_activity", section_updated)

    def test_same_date_duplicate_refused(self):
        self._create_page()
        ingest_mod.ingest(self._write_artifact(name="first.json", generated="2026-07-13"))
        with self.assertRaises(ingest_mod.IngestError):
            ingest_mod.ingest(self._write_artifact(name="second.json", generated="2026-07-13"))


if __name__ == "__main__":
    unittest.main()
