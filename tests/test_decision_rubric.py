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

    def test_each_track_weights_sum_to_one(self):
        for is_etf in (False, True):
            dims = rubric_mod.applicable_dimensions(
                self.rubric, dividend_payer=True, income_role=True, is_etf=is_etf
            )
            total = sum(d["weight"] for d in dims)
            self.assertAlmostEqual(total, 1.0, places=6, msg=f"is_etf={is_etf} track sums to {total}")

    def test_etf_track_swaps_company_dims_for_fund_dims(self):
        etf_dims = {d["id"] for d in rubric_mod.applicable_dimensions(
            self.rubric, dividend_payer=True, income_role=True, is_etf=True)}
        self.assertIn("fund_efficiency", etf_dims)
        self.assertIn("fund_quality", etf_dims)
        self.assertNotIn("financial_health", etf_dims)
        self.assertNotIn("growth", etf_dims)
        self.assertNotIn("insider_activity", etf_dims)

    def test_etf_track_drops_company_gates(self):
        etf_gates = {g["id"] for g in rubric_mod.applicable_gates(
            self.rubric, dividend_payer=True, income_role=True, is_etf=True)}
        self.assertEqual(etf_gates, {"data_sufficiency"})

    def test_sentiment_dimensions_cite_derived_revision_metrics(self):
        # v1.3: the raw upgrades_downgrades table was replaced by derived counts
        # so the worksheet no longer embeds a multi-year table twice.
        dims = {d["id"]: d for d in self.rubric.get("dimensions", [])}
        for dim_id in ("market_sentiment", "earnings_catalysts"):
            fields = dims[dim_id]["evidence_fields"]
            self.assertIn("derived:upgrades_90d", fields, msg=dim_id)
            self.assertIn("derived:net_revisions_365d", fields, msg=dim_id)
            self.assertNotIn("yfinance:data.analyst.upgrades_downgrades", fields, msg=dim_id)

    def test_revision_metrics_registered_as_derived_source(self):
        groups = self.rubric["sources"]["derived"]["groups"]
        for name in ("upgrades_90d", "downgrades_90d", "net_revisions_365d"):
            self.assertIn(name, groups)


class ValidationFailureTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_bad_weight_sum_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["dimensions"][0]["weight"] = 0.9  # valuation is 'always' -> breaks both tracks
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("track dimension weights must sum to 1.0" in e for e in errors), errors)

    def test_etf_only_weight_sum_rejected_without_touching_equity(self):
        broken = copy.deepcopy(self.rubric)
        for dim in broken["dimensions"]:
            if dim["id"] == "fund_quality":
                dim["weight"] = 0.5  # breaks etf track only
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("etf-track dimension weights must sum" in e for e in errors), errors)
        self.assertFalse(any("equity-track dimension weights must sum" in e for e in errors), errors)

    def test_list_applies_when_accepted(self):
        ok = copy.deepcopy(self.rubric)
        for gate in ok["gates"]:
            if gate["id"] == "dividend_integrity":
                self.assertEqual(gate["applies_when"], ["income_role", "equity_only"])
        errors = rubric_mod.validate_rubric(ok, FRAMEWORK)
        self.assertEqual(errors, [], errors)

    def test_contradictory_applies_when_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["dimensions"][0]["applies_when"] = ["equity_only", "etf_only"]
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("cannot require both equity_only and etf_only" in e for e in errors), errors)

    def test_unknown_applies_when_element_rejected(self):
        broken = copy.deepcopy(self.rubric)
        broken["dimensions"][0]["applies_when"] = ["always", "bogus"]
        errors = rubric_mod.validate_rubric(broken, FRAMEWORK)
        self.assertTrue(any("unknown applies_when 'bogus'" in e for e in errors), errors)

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


class IsApplicableTest(unittest.TestCase):
    def test_equity_only_matrix(self):
        entry = {"applies_when": "equity_only"}
        self.assertTrue(rubric_mod.is_applicable(entry, dividend_payer=True, income_role=True, is_etf=False))
        self.assertFalse(rubric_mod.is_applicable(entry, dividend_payer=True, income_role=True, is_etf=True))

    def test_etf_only_matrix(self):
        entry = {"applies_when": "etf_only"}
        self.assertTrue(rubric_mod.is_applicable(entry, dividend_payer=True, income_role=True, is_etf=True))
        self.assertFalse(rubric_mod.is_applicable(entry, dividend_payer=True, income_role=True, is_etf=False))

    def test_list_requires_all_conditions(self):
        entry = {"applies_when": ["income_role", "equity_only"]}
        self.assertTrue(rubric_mod.is_applicable(entry, dividend_payer=False, income_role=True, is_etf=False))
        self.assertFalse(rubric_mod.is_applicable(entry, dividend_payer=False, income_role=True, is_etf=True))
        self.assertFalse(rubric_mod.is_applicable(entry, dividend_payer=False, income_role=False, is_etf=False))

    def test_default_always_applies(self):
        self.assertTrue(rubric_mod.is_applicable({}, dividend_payer=False, income_role=False, is_etf=True))


class RenormalizationTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def test_etf_track_weighted_score_renormalizes(self):
        # Score every etf-track dimension 4 -> weighted average 4.0 even though
        # the equity-only dims (with their weights) are excluded, not zero-scored.
        etf_dims = rubric_mod.applicable_dimensions(
            self.rubric, dividend_payer=True, income_role=True, is_etf=True)
        scores = {d["id"]: 4 for d in etf_dims}
        result = rubric_mod.weighted_score(
            scores, self.rubric, dividend_payer=True, income_role=True, is_etf=True)
        self.assertAlmostEqual(result, 4.0, places=6)

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

    def test_confidence_levels_equity(self):
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=0, groups_ok=11, rubric=self.rubric), "High")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=1, unknown_gates=0, groups_ok=9, rubric=self.rubric), "Medium")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=1, groups_ok=11, rubric=self.rubric), "Low")
        self.assertEqual(rubric_mod.evaluate_confidence(unknown_dimensions=3, unknown_gates=0, groups_ok=11, rubric=self.rubric), "Low")

    def test_confidence_uses_etf_threshold_mapping(self):
        # 7 usable groups is High for a fund (etf floor 6) but Low for an equity
        # (equity floor 10) at the same unknown counts.
        self.assertEqual(
            rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=0, groups_ok=7, rubric=self.rubric, is_etf=True), "High")
        self.assertEqual(
            rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=0, groups_ok=7, rubric=self.rubric, is_etf=False), "Low")
        # CA ETF: 5 usable groups, <=2 unknown dims -> Medium (etf medium floor 5).
        self.assertEqual(
            rubric_mod.evaluate_confidence(unknown_dimensions=2, unknown_gates=0, groups_ok=5, rubric=self.rubric, is_etf=True), "Medium")

    def test_confidence_accepts_plain_int_min_groups_ok(self):
        legacy = copy.deepcopy(self.rubric)
        legacy["confidence_rules"]["High"]["min_groups_ok"] = 10
        legacy["confidence_rules"]["Medium"]["min_groups_ok"] = 8
        self.assertEqual(
            rubric_mod.evaluate_confidence(unknown_dimensions=0, unknown_gates=0, groups_ok=10, rubric=legacy), "High")


if __name__ == "__main__":
    unittest.main()
