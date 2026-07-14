import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rubric as rubric_mod
import validate_recommendation as vr
import _decision_fixtures as fx


def _valid_artifact() -> dict:
    """A recommendation that should pass: all dims scored 4 -> weighted 4.0,
    held, no gate failures -> Add / High / Long-term."""
    dim_citation = {
        "valuation": {"source": "yfinance", "field": "yfinance:data.valuation.forwardPE"},
        "financial_health": {"source": "derived", "field": "derived:debt_to_equity"},
        "growth": {"source": "derived", "field": "derived:revenue_growth_yoy"},
        "earnings_catalysts": {"source": "yfinance", "field": "yfinance:data.earnings.calendar"},
        "dividend_safety": {"source": "yfinance", "field": "yfinance:data.dividends.summary.payoutRatio"},
        "market_sentiment": {"source": "derived", "field": "derived:return_90d"},
        "insider_activity": {"source": "yfinance", "field": "yfinance:data.insider.purchases"},
        "options_activity": {"source": "derived", "field": "derived:put_call_oi_ratio"},
        "portfolio_fit": {"source": "classification", "field": "classification:holdings.primary_group"},
    }
    gate_citation = {
        "solvency": {"source": "yfinance", "field": "yfinance:data.valuation.freeCashflow"},
        "profitability_or_path": {"source": "derived", "field": "derived:net_income_latest"},
        "data_sufficiency": {"source": "derived", "field": "derived:groups_ok_count"},
    }
    return {
        "schema": "stock-recommendation.v1",
        "ticker": "AAPL",
        "rubric_version": "v1.0",
        "research_sources": {
            "yfinance": {"path": "research.json", "groups_ok": ["overview"] * 11, "groups_failed": {}},
            "classification": {"path": "classification.json"},
        },
        "position": {"held": True, "weight_pct": 3.2, "portfolio_role": "Quality", "dividend_payer": True, "income_role": False},
        "gates": [
            {"id": gid, "result": "pass", "evidence": [dict(cite, value=1, note="ok")]}
            for gid, cite in gate_citation.items()
        ],
        "dimensions": [
            {"id": did, "weight": 0.0, "score": 4, "rationale": "meets anchor", "evidence": [dict(cite, value=1, note="ok")]}
            for did, cite in dim_citation.items()
        ],
        "weighted_score": 4.0,
        "unknown_dimensions": [],
        "proposed": {"action": "Add", "confidence": "High", "time_horizon": "Long-term", "verdict_vs_previous": "unchanged"},
        "narratives": {
            "executive_summary": "Strong compounder, add on fit.",
            "analyst_view": "The rubric says Add; I agree but note valuation is full.",
            "updated_thesis": "Thesis unchanged and slightly stronger.",
            "bull_case": ["Durable franchise"],
            "bear_case": ["Valuation rich"],
            "key_risks": ["China demand"],
            "open_questions": [],
            "monitoring": ["Services growth"],
            "section_updates": {"Valuation Analysis": "Forward P/E 28, above history."},
        },
        "facts_assumptions_opinions": {"facts": ["FCF 90B"], "assumptions": ["Growth persists"], "opinions": ["Add"]},
    }


class ValidateRecommendationTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()
        self.framework = rubric_mod.load_framework()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "research.json").write_text(json.dumps(fx.research_payload()), encoding="utf-8")
        (base / "classification.json").write_text(json.dumps(fx.classification_payload()), encoding="utf-8")
        self.base = base

    def _check(self, artifact):
        sources = vr._load_sources(artifact, self.base)
        return vr.validate_recommendation(artifact, self.rubric, self.framework, sources)

    def test_valid_artifact_passes(self):
        errors = self._check(_valid_artifact())
        self.assertEqual(errors, [], msg=f"expected clean pass: {errors}")

    def test_missing_dimension_rejected(self):
        art = _valid_artifact()
        art["dimensions"] = [d for d in art["dimensions"] if d["id"] != "growth"]
        self.assertTrue(any("missing required dimension 'growth'" in e for e in self._check(art)))

    def test_out_of_range_score_rejected(self):
        art = _valid_artifact()
        art["dimensions"][0]["score"] = 7
        self.assertTrue(any("score must be an integer" in e for e in self._check(art)))

    def test_citation_to_nonexistent_field_rejected(self):
        art = _valid_artifact()
        art["dimensions"][0]["evidence"].append({"source": "yfinance", "field": "yfinance:data.valuation.doesNotExist", "value": 1})
        self.assertTrue(any("does not resolve" in e for e in self._check(art)))

    def test_weighted_score_mismatch_rejected(self):
        art = _valid_artifact()
        art["weighted_score"] = 1.0
        self.assertTrue(any("does not match recomputed" in e for e in self._check(art)))

    def test_action_band_inconsistency_rejected(self):
        art = _valid_artifact()
        art["proposed"]["action"] = "Buy"  # not-held action while held
        self.assertTrue(any("does not match rubric verdict" in e for e in self._check(art)))

    def test_confidence_too_high_for_unknowns_rejected(self):
        art = _valid_artifact()
        art["dimensions"][0]["score"] = "unknown"  # 1 unknown dim -> expected Medium
        art["dimensions"][0]["evidence"] = []
        # weighted score stays 4.0 (unknown excluded), action stays Add; only confidence is wrong.
        errors = self._check(art)
        self.assertTrue(any("does not match rubric confidence" in e for e in errors), errors)

    def test_gate_fail_with_wrong_action_rejected(self):
        art = _valid_artifact()
        art["gates"][0]["result"] = "fail"  # forces Sell, but proposed is Add
        self.assertTrue(any("does not match rubric verdict" in e for e in self._check(art)))

    def test_missing_analyst_view_rejected(self):
        art = _valid_artifact()
        art["narratives"]["analyst_view"] = ""
        self.assertTrue(any("narratives.analyst_view" in e for e in self._check(art)))

    def test_bad_schema_rejected(self):
        art = _valid_artifact()
        art["schema"] = "wrong"
        self.assertTrue(any("schema must be" in e for e in self._check(art)))


if __name__ == "__main__":
    unittest.main()
