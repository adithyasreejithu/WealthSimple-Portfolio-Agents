import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rubric as rubric_mod
import scoring_worksheet as ws
import _decision_fixtures as fx


def _args(**overrides):
    base = dict(role=None, held=None, page_exists=None, current_decision=None)
    base.update(overrides)
    return argparse.Namespace(**base)


class DerivedMetricsTest(unittest.TestCase):
    def setUp(self):
        self.item = fx.research_payload()[0]
        self.metrics = ws.compute_derived_metrics(self.item)

    def test_fcf_yield(self):
        self.assertAlmostEqual(self.metrics["fcf_yield"], 0.03, places=6)

    def test_revenue_growth_yoy(self):
        self.assertAlmostEqual(self.metrics["revenue_growth_yoy"], (391 - 383) / 383, places=6)

    def test_net_income_latest(self):
        self.assertAlmostEqual(self.metrics["net_income_latest"], 94e9, places=1)

    def test_debt_to_equity(self):
        self.assertAlmostEqual(self.metrics["debt_to_equity"], 100e9 / 57e9, places=6)

    def test_current_ratio(self):
        self.assertAlmostEqual(self.metrics["current_ratio"], 152 / 176, places=6)

    def test_price_returns(self):
        self.assertAlmostEqual(self.metrics["return_365d"], 200 / 150 - 1, places=6)
        self.assertAlmostEqual(self.metrics["return_90d"], 200 / 180 - 1, places=6)
        self.assertAlmostEqual(self.metrics["return_30d"], 200 / 190 - 1, places=6)

    def test_put_call_oi_ratio(self):
        self.assertAlmostEqual(self.metrics["put_call_oi_ratio"], 500 / 1500, places=6)

    def test_net_insider_shares(self):
        self.assertEqual(self.metrics["net_insider_shares"], 1200)

    def test_groups_ok_count_full(self):
        self.assertEqual(self.metrics["groups_ok_count"], 11)

    def test_groups_ok_count_sparse(self):
        sparse = fx.research_payload_sparse()[0]
        self.assertEqual(ws.compute_derived_metrics(sparse)["groups_ok_count"], 4)


class WorksheetBuildTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def _build(self, research, classification=None, **arg_overrides):
        sources = {"yfinance": ("research.json", research)}
        if classification is not None:
            sources["classification"] = ("classification.json", classification)
        return ws.build_worksheet("AAPL", self.rubric, sources, _args(**arg_overrides))

    def test_position_from_classification(self):
        sheet = self._build(fx.research_payload(), fx.classification_payload())
        self.assertTrue(sheet["position"]["held"])
        self.assertEqual(sheet["position"]["portfolio_role"], "Quality")
        self.assertEqual(sheet["position"]["weight_pct"], 3.2)
        self.assertTrue(sheet["position"]["dividend_payer"])

    def test_not_held_when_absent_from_classification(self):
        sheet = ws.build_worksheet("AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())}, _args())
        self.assertFalse(sheet["position"]["held"])

    def test_dividend_dimension_present_for_payer(self):
        sheet = self._build(fx.research_payload(), fx.classification_payload())
        dim_ids = {d["id"] for d in sheet["dimensions"]}
        self.assertIn("dividend_safety", dim_ids)

    def test_gate_and_dimension_slots_empty(self):
        sheet = self._build(fx.research_payload(), fx.classification_payload())
        self.assertTrue(all(g["result"] is None for g in sheet["gates"]))
        self.assertTrue(all(d["score"] is None for d in sheet["dimensions"]))

    def test_classification_source_unsupplied_marks_portfolio_fit_unknown(self):
        # No classification file supplied -> portfolio_fit cites classification only,
        # so it should be flagged unknown rather than raising.
        sheet = ws.build_worksheet("AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())}, _args())
        fit = next(d for d in sheet["dimensions"] if d["id"] == "portfolio_fit")
        self.assertTrue(fit["unknown"])

    def test_evidence_values_resolved(self):
        sheet = self._build(fx.research_payload(), fx.classification_payload())
        valuation = next(d for d in sheet["dimensions"] if d["id"] == "valuation")
        by_field = {e["field"]: e for e in valuation["evidence"]}
        self.assertEqual(by_field["yfinance:data.valuation.forwardPE"]["value"], 28.0)
        self.assertAlmostEqual(by_field["derived:fcf_yield"]["value"], 0.03, places=6)
        self.assertTrue(by_field["yfinance:data.valuation.forwardPE"]["available"])

    def test_sparse_pull_marks_financial_dimension_unknown(self):
        sheet = ws.build_worksheet("XYZ", self.rubric, {"yfinance": ("r.json", fx.research_payload_sparse())}, _args())
        growth = next(d for d in sheet["dimensions"] if d["id"] == "growth")
        # growth cites revenue_growth_yoy (needs financials, which errored) +
        # income statement + price targets -> all unavailable.
        self.assertTrue(growth["unknown"])


if __name__ == "__main__":
    unittest.main()
