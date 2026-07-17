import contextlib
import copy
import io
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
        "position": {"held": True, "weight_pct": 3.2, "portfolio_role": "Quality",
                     "dividend_payer": True, "income_role": False, "is_etf": False, "page_exists": True},
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
            "updated_thesis": "Services margins keep carrying earnings as hardware matures.\n\nThe thesis is unchanged and slightly stronger versus the original.",
            "bull_case": ["Durable franchise"],
            "bear_case": ["Valuation rich"],
            "key_risks": ["China demand"],
            "open_questions": [],
            "monitoring": ["Services growth"],
            "section_updates": {"Valuation Analysis": "- Forward P/E: 28 — above history\n- Score: 3/5 (fairly valued)"},
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

    def test_new_page_requires_overview_and_original_thesis(self):
        art = _valid_artifact()
        art["position"]["page_exists"] = False
        errors = self._check(art)
        self.assertTrue(any("company_overview" in e for e in errors), errors)
        self.assertTrue(any("original_thesis" in e for e in errors), errors)

    def test_new_page_with_overview_and_thesis_passes(self):
        art = _valid_artifact()
        art["position"]["page_exists"] = False
        art["narratives"]["company_overview"] = "Apple designs and sells consumer electronics and services."
        art["narratives"]["original_thesis"] = "Bought for durable services-led margin expansion."
        self.assertEqual(self._check(art), [])

    def test_section_without_score_line_rejected(self):
        art = _valid_artifact()
        art["narratives"]["section_updates"]["Valuation Analysis"] = "- Forward P/E: 28 — rich"
        self.assertTrue(any("- Score: X/5" in e for e in self._check(art)))

    def test_single_paragraph_updated_thesis_rejected(self):
        art = _valid_artifact()
        art["narratives"]["updated_thesis"] = "One paragraph only."
        self.assertTrue(any("at least two paragraphs" in e for e in self._check(art)))

    def test_new_derived_revision_citations_resolve(self):
        art = _valid_artifact()
        sentiment = next(d for d in art["dimensions"] if d["id"] == "market_sentiment")
        sentiment["evidence"] = [
            {"source": "derived", "field": "derived:upgrades_90d", "value": 2, "note": "2 upgrades"},
            {"source": "derived", "field": "derived:net_revisions_365d", "value": 1, "note": "net positive"},
        ]
        self.assertEqual(self._check(art), [])

    def test_narrative_word_ceilings_rejected(self):
        art = _valid_artifact()
        art["narratives"]["analyst_view"] = "word " * (vr.MAX_NARRATIVE_WORDS["analyst_view"] + 1)
        art["narratives"]["executive_summary"] = "word " * (vr.MAX_NARRATIVE_WORDS["executive_summary"] + 1)
        errors = self._check(art)
        self.assertTrue(any("analyst_view exceeds" in e for e in errors), errors)
        self.assertTrue(any("executive_summary exceeds" in e for e in errors), errors)

    def test_updated_thesis_paragraph_ceiling_rejected(self):
        art = _valid_artifact()
        art["narratives"]["updated_thesis"] = "\n\n".join(
            f"Paragraph {i}: still unchanged." for i in range(vr.MAX_UPDATED_THESIS_PARAGRAPHS + 1)
        )
        self.assertTrue(any("updated_thesis exceeds" in e for e in self._check(art)))

    def test_section_bullet_ceiling_rejected(self):
        art = _valid_artifact()
        bullets = "\n".join(f"- Metric {i}: value" for i in range(vr.MAX_SECTION_BULLETS)) + "\n- Score: 3/5 (ok)"
        art["narratives"]["section_updates"]["Valuation Analysis"] = bullets
        self.assertTrue(any("over the" in e and "ceiling" in e for e in self._check(art)))


def _valid_etf_artifact() -> dict:
    """A DGRO (US-listed ETF) recommendation: fund track, all 7 dims scored 4."""
    dim_citation = {
        "valuation": {"source": "yfinance", "field": "yfinance:data.valuation.trailingPE"},
        "dividend_safety": {"source": "yfinance", "field": "yfinance:data.dividends.summary.dividendYield"},
        "market_sentiment": {"source": "derived", "field": "derived:return_90d"},
        "options_activity": {"source": "derived", "field": "derived:put_call_oi_ratio"},
        "portfolio_fit": {"source": "classification", "field": "classification:holdings.primary_group"},
        "fund_efficiency": {"source": "classification", "field": "classification:holdings.expense_ratio"},
        "fund_quality": {"source": "classification", "field": "classification:holdings.sector_weights"},
    }
    return {
        "schema": "stock-recommendation.v1",
        "ticker": "DGRO",
        "rubric_version": "v1.2",
        "research_sources": {
            "yfinance": {"path": "research.json", "groups_ok": ["overview"] * 7, "groups_failed": {}},
            "classification": {"path": "classification.json"},
        },
        "position": {"held": True, "weight_pct": 4.0, "portfolio_role": "Income",
                     "dividend_payer": True, "income_role": True, "is_etf": True, "page_exists": True},
        "gates": [{"id": "data_sufficiency", "result": "pass",
                   "evidence": [{"source": "derived", "field": "derived:groups_ok_count", "value": 7, "note": "ok"}]}],
        "dimensions": [
            {"id": did, "weight": 0.0, "score": 4, "rationale": "meets anchor", "evidence": [dict(cite, value=1, note="ok")]}
            for did, cite in dim_citation.items()
        ],
        "weighted_score": 4.0,
        "unknown_dimensions": [],
        "proposed": {"action": "Add", "confidence": "High", "time_horizon": "Long-term", "verdict_vs_previous": "unchanged"},
        "narratives": {
            "executive_summary": "Cheap, diversified dividend-growth fund; add.",
            "analyst_view": "Rubric says Add on the fund track; I agree.",
            "updated_thesis": "The fund does exactly what it is meant to.\n\nThesis unchanged and slightly stronger.",
            "bull_case": ["Low fee"], "bear_case": ["Rate sensitivity"], "key_risks": ["Concentration"],
            "open_questions": [], "monitoring": ["Distribution growth"],
            "section_updates": {"Financial Analysis": "- Expense ratio: 0.08% — cheap\n- Score (fund_efficiency): 4/5\n- Score (fund_quality): 4/5"},
        },
        "facts_assumptions_opinions": {"facts": ["ER 0.08%"], "assumptions": ["Distributions hold"], "opinions": ["Add"]},
    }


class ValidateEtfTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()
        self.framework = rubric_mod.load_framework()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "research.json").write_text(json.dumps(fx.etf_research_payload()), encoding="utf-8")
        (base / "classification.json").write_text(json.dumps(fx.etf_classification_payload()), encoding="utf-8")
        self.base = base

    def _check(self, artifact):
        sources = vr._load_sources(artifact, self.base)
        return vr.validate_recommendation(artifact, self.rubric, self.framework, sources)

    def test_valid_etf_artifact_passes(self):
        self.assertEqual(self._check(_valid_etf_artifact()), [])

    def test_equity_gates_and_dims_not_required_for_etf(self):
        errors = self._check(_valid_etf_artifact())
        self.assertFalse(any("solvency" in e or "financial_health" in e for e in errors), errors)

    def test_missing_fund_quality_rejected(self):
        art = _valid_etf_artifact()
        art["dimensions"] = [d for d in art["dimensions"] if d["id"] != "fund_quality"]
        self.assertTrue(any("missing required dimension 'fund_quality'" in e for e in self._check(art)))

    def test_citation_to_empty_group_rejected(self):
        art = _valid_etf_artifact()
        # income_statement_annual is a structurally-empty table for a fund.
        art["dimensions"][0]["evidence"].append(
            {"source": "yfinance", "field": "yfinance:data.financials.income_statement_annual", "value": 1})
        self.assertTrue(any("does not resolve" in e for e in self._check(art)))

    def test_etf_reaches_high_confidence_at_seven_groups(self):
        # Sanity: the valid ETF artifact is High (7 usable groups >= etf floor 6).
        self.assertEqual(_valid_etf_artifact()["proposed"]["confidence"], "High")
        self.assertEqual(self._check(_valid_etf_artifact()), [])


class ComputeVerdictTest(unittest.TestCase):
    """compute_verdict() is the shared math validate_recommendation() also uses --
    it must agree with the full validator's recomputed values on the same artifact,
    and work from gates/dimensions alone (no sources, no narratives)."""

    def setUp(self):
        self.rubric = rubric_mod.load_rubric()
        self.framework = rubric_mod.load_framework()

    def test_matches_full_validator_on_valid_artifact(self):
        art = _valid_artifact()
        verdict = vr.compute_verdict(art, self.rubric, self.framework)
        self.assertAlmostEqual(verdict["weighted_score"], 4.0)
        self.assertEqual(verdict["action"], "Add")
        self.assertEqual(verdict["confidence"], "High")
        self.assertEqual(verdict["gate_failed"], False)
        self.assertEqual(verdict["unknown_gates"], 0)
        self.assertEqual(verdict["unknown_dimensions"], 0)
        self.assertEqual(verdict["missing_gates"], [])
        self.assertEqual(verdict["missing_dimensions"], [])
        self.assertEqual(verdict["default_time_horizon"], rubric_mod.default_time_horizon("Quality", self.rubric))

    def test_needs_no_narratives_or_sources(self):
        art = _valid_artifact()
        del art["narratives"]
        del art["facts_assumptions_opinions"]
        verdict = vr.compute_verdict(art, self.rubric, self.framework)
        self.assertEqual(verdict["action"], "Add")

    def test_gate_failure_changes_action(self):
        art = _valid_artifact()
        art["gates"][0]["result"] = "fail"
        verdict = vr.compute_verdict(art, self.rubric, self.framework)
        self.assertTrue(verdict["gate_failed"])
        self.assertNotEqual(verdict["action"], "Add")

    def test_missing_dimension_reported(self):
        art = _valid_artifact()
        art["dimensions"] = [d for d in art["dimensions"] if d["id"] != "growth"]
        verdict = vr.compute_verdict(art, self.rubric, self.framework)
        self.assertEqual(verdict["missing_dimensions"], ["growth"])

    def test_recompute_does_not_alter_full_validation_errors(self):
        # Regression: the refactor that routed validate_recommendation() through
        # compute_verdict() must not change any existing error text/behavior.
        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        (base / "research.json").write_text(json.dumps(fx.research_payload()), encoding="utf-8")
        (base / "classification.json").write_text(json.dumps(fx.classification_payload()), encoding="utf-8")
        art = _valid_artifact()
        art["weighted_score"] = 1.0
        sources = vr._load_sources(art, base)
        errors = vr.validate_recommendation(art, self.rubric, self.framework, sources)
        self.assertTrue(any("does not match recomputed" in e for e in errors), errors)


class PrecomputeCLITest(unittest.TestCase):
    """--precompute-only must work from a partial draft (no citations, no
    narratives, no research source files) and never touch _load_sources."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _write(self, artifact: dict) -> Path:
        path = self.base / "draft.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return path

    def _run(self, path: Path) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vr.main(["--path", str(path), "--precompute-only"])
        return code, buf.getvalue()

    def test_partial_artifact_no_narratives_no_citations(self):
        art = _valid_artifact()
        del art["narratives"]
        del art["facts_assumptions_opinions"]
        for gate in art["gates"]:
            gate["evidence"] = []
        for dim in art["dimensions"]:
            dim["evidence"] = []
        path = self._write(art)
        code, out = self._run(path)
        self.assertEqual(code, 0)
        self.assertIn("weighted_score:", out)
        self.assertIn("action: Add", out)

    def test_skips_load_sources_for_missing_research_file(self):
        art = _valid_artifact()
        art["research_sources"]["yfinance"]["path"] = "does-not-exist.json"
        path = self._write(art)
        code, out = self._run(path)
        self.assertEqual(code, 0)
        self.assertIn("action:", out)
        # Confirm a full validate on the same broken path does NOT error loading
        # sources (missing files are silently skipped by _load_sources), but
        # would fail citation resolution instead -- precompute needs neither.
        sources = vr._load_sources(art, path.resolve().parent)
        self.assertNotIn("yfinance", sources)

    def test_missing_gates_and_dimensions_listed(self):
        art = _valid_artifact()
        art["gates"] = [g for g in art["gates"] if g["id"] != "solvency"]
        art["dimensions"] = [d for d in art["dimensions"] if d["id"] not in ("growth", "insider_activity")]
        path = self._write(art)
        code, out = self._run(path)
        self.assertEqual(code, 0)
        self.assertIn("solvency", out)
        self.assertIn("growth", out)
        self.assertIn("insider_activity", out)


if __name__ == "__main__":
    unittest.main()
