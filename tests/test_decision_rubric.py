import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))

import rubric as rubric_mod


FRAMEWORK = {
    "actions": ["Buy", "Sell", "Hold", "Trim", "Add", "Watchlist", "Avoid"],
    "confidence_levels": ["Low", "Medium", "High"],
    "time_horizons": ["Short-term", "Medium-term", "Long-term"],
}


class ShippedRubricTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_shipped_rubric_is_valid(self):
        errors = rubric_mod.validate_rubric(self.rubric, rubric_mod.load_framework())
        self.assertEqual(errors, [], msg=f"shipped rubric has errors: {errors}")

    def test_dimension_weights_sum_to_one(self):
        total = sum(d["weight"] for d in self.rubric["dimensions"])
        self.assertAlmostEqual(total, 1.0, places=6)


class ValidationFailureTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_bad_weight_sum_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["dimensions"][0]["weight"] = 0.9
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("weights must sum" in e for e in errors), errors)

    def test_unregistered_source_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["dimensions"][0]["evidence_fields"].append("bloomberg:data.foo")
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("bloomberg" in e for e in errors), errors)

    def test_band_gap_rejected(self):
        broken = copy.deepcopy(self.rubric)
        # Raise the lowest band min above the score floor so the range is uncovered.
        for band in broken["verdict_bands"]["not_held"]:
            band["min"] = max(band["min"], 2.0)
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("cover the range" in e for e in errors), errors)

    def test_unknown_action_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["verdict_bands"]["not_held"][0]["action"] = "Yolo"
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("not in decision-framework actions" in e for e in errors), errors)

    def test_missing_confidence_level_rejected(self):
        broken = copy.deepcopy(self.rubric)
        del broken["confidence_rules"]["High"]
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("missing level 'High'" in e for e in errors), errors)


class RenormalizationTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_dividend_dimension_dropped_for_non_payer(self):
        applicable = rubric_mod.applicable_dimensions(self.rubric, dividend_payer=False, income_role=False)
        ids = {d["id"] for d in applicable}
        self.assertNotIn("dividend_safety", ids)

    def test_weight_renormalized_when_dividend_dropped(self):
        # All remaining dims scored 4 -> weighted average is 4 regardless of the
        # dropped dividend weight (pro-rata renormalization).
        scores = {d["id"]: 4 for d in self.rubric["dimensions"]}
        result = rubric_mod.weighted_score(scores, self.rubric, dividend_payer=False, income_role=False)
        self.assertAlmostEqual(result, 4.0, places=6)

    def test_unknown_dimension_excluded_from_average(self):
        scores = {d["id"]: 2 for d in self.rubric["dimensions"]}
        scores["valuation"] = "unknown"
        result = rubric_mod.weighted_score(scores, self.rubric, dividend_payer=True, income_role=False)
        self.assertAlmostEqual(result, 2.0, places=6)


class BandAndConfidenceTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_held_bands(self):
        self.assertEqual(rubric_mod.lookup_action(4.5, gate_failed=False, held=True, rubric=self.rubric), "Add")
        self.assertEqual(rubric_mod.lookup_action(3.0, gate_failed=False, held=True, rubric=self.rubric), "Hold")
        self.assertEqual(rubric_mod.lookup_action(2.2, gate_failed=False, held=True, rubric=self.rubric), "Trim")
        self.assertEqual(rubric_mod.lookup_action(1.5, gate_failed=False, held=True, rubric=self.rubric), "Sell")

    def test_not_held_bands(self):
        self.assertEqual(rubric_mod.lookup_action(4.5, gate_failed=False, held=False, rubric=self.rubric), "Buy")
        self.assertEqual(rubric_mod.lookup_action(3.2, gate_failed=False, held=False, rubric=self.rubric), "Watchlist")
        self.assertEqual(rubric_mod.lookup_action(2.0, gate_failed=False, held=False, rubric=self.rubric), "Avoid")

    def test_gate_failure_forces_exit(self):
        self.assertEqual(rubric_mod.lookup_action(4.9, gate_failed=True, held=True, rubric=self.rubric), "Sell")
        self.assertEqual(rubric_mod.lookup_action(4.9, gate_failed=True, held=False, rubric=self.rubric), "Avoid")

    def test_confidence_levels(self):
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=0, groups_ok=11, rubric=self.rubric), "High")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=1, unknown_gates=0, groups_ok=9, rubric=self.rubric), "Medium")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=1, groups_ok=11, rubric=self.rubric), "Low")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=3, unknown_gates=0, groups_ok=11, rubric=self.rubric), "Low")


if __name__ == "__main__":
    unittest.main()
