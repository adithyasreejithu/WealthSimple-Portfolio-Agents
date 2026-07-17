import argparse
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rubric as rubric_mod
import scoring_worksheet as ws
import _decision_fixtures as fx


def _args(**overrides):
    base = dict(role=None, held=None, page_exists=None, thesis_page=None, asset_class=None)
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

    def test_put_call_volume_ratio(self):
        self.assertAlmostEqual(self.metrics["put_call_volume_ratio"], 120 / 240, places=6)

    def test_atm_iv_term_structure(self):
        # ATM strike is 200: near chain call/put IV 0.28/0.29 -> 0.285;
        # far chain 0.24/0.25 -> 0.245 (downward term structure).
        self.assertAlmostEqual(self.metrics["atm_iv_near"], (0.28 + 0.29) / 2, places=6)
        self.assertAlmostEqual(self.metrics["atm_iv_far"], (0.24 + 0.25) / 2, places=6)

    def test_iv_skew(self):
        # OTM put ~186 -> strike 180 IV 0.34; OTM call ~214 -> strike 220 IV 0.26.
        self.assertAlmostEqual(self.metrics["iv_skew"], 0.34 - 0.26, places=6)

    def test_max_oi_strikes(self):
        self.assertEqual(self.metrics["max_oi_call_strike"], 180.0)
        self.assertEqual(self.metrics["max_oi_put_strike"], 180.0)


class EmptinessTest(unittest.TestCase):
    def test_has_data_semantics(self):
        self.assertTrue(ws._has_data(0))
        self.assertTrue(ws._has_data(0.0))
        self.assertTrue(ws._has_data("x"))
        self.assertTrue(ws._has_data({"a": 1}))
        self.assertFalse(ws._has_data(None))
        self.assertFalse(ws._has_data(""))
        self.assertFalse(ws._has_data({}))
        self.assertFalse(ws._has_data([]))
        self.assertFalse(ws._has_data({"calendar": {}}))  # structurally empty
        self.assertFalse(ws._has_data({"expirations": []}))

    def test_etf_groups_ok_excludes_empty_company_groups(self):
        # DGRO-shaped: 5 company groups empty, errors={} -> 7 usable of 12.
        metrics = ws.compute_derived_metrics(fx.etf_research_payload()[0])
        self.assertEqual(metrics["groups_ok_count"], 7)

    def test_ca_etf_groups_ok_excludes_options_and_news(self):
        metrics = ws.compute_derived_metrics(fx.etf_research_payload_ca()[0])
        self.assertEqual(metrics["groups_ok_count"], 5)

    def test_options_metrics_none_on_empty_chain(self):
        metrics = ws.compute_derived_metrics(fx.etf_research_payload_ca()[0])
        for name in ("put_call_oi_ratio", "put_call_volume_ratio", "atm_iv_near",
                     "atm_iv_far", "iv_skew", "max_oi_call_strike", "max_oi_put_strike"):
            self.assertIsNone(metrics[name], msg=name)


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

    def test_empty_evidence_marked_unavailable(self):
        # earnings.calendar is populated for the equity fixture; verify a slot
        # pointing at an empty table (CA ETF options) is not marked available.
        sheet = ws.build_worksheet(
            "ZGLD", self.rubric,
            {"yfinance": ("r.json", fx.etf_research_payload_ca()),
             "classification": ("c.json", fx.etf_classification_payload("ZGLD", "Alternatives"))},
            _args())
        options = next(d for d in sheet["dimensions"] if d["id"] == "options_activity")
        self.assertTrue(all(not e["available"] for e in options["evidence"]))
        self.assertTrue(options["unknown"])


class AnalystRevisionMetricsTest(unittest.TestCase):
    """The derived revision counts that replaced the raw upgrades_downgrades
    table as market_sentiment / earnings_catalysts evidence (rubric v1.3)."""

    def setUp(self):
        self.metrics = ws.compute_derived_metrics(fx.research_payload()[0])

    def test_upgrades_90d(self):
        self.assertEqual(self.metrics["upgrades_90d"], 2)

    def test_downgrades_90d(self):
        self.assertEqual(self.metrics["downgrades_90d"], 1)

    def test_net_revisions_365d(self):
        self.assertEqual(self.metrics["net_revisions_365d"], 1)

    def test_non_revision_actions_ignored(self):
        # The fixture's "main" row (5 days ago) counts toward neither side.
        self.assertEqual(self.metrics["upgrades_90d"] + self.metrics["downgrades_90d"], 3)

    def test_missing_table_is_unknown(self):
        metrics = ws.compute_derived_metrics(fx.research_payload_sparse()[0])
        for name in ("upgrades_90d", "downgrades_90d", "net_revisions_365d"):
            self.assertIsNone(metrics[name], msg=name)

    def test_empty_etf_table_is_unknown(self):
        metrics = ws.compute_derived_metrics(fx.etf_research_payload()[0])
        self.assertIsNone(metrics["upgrades_90d"])


class EvidenceCapTest(unittest.TestCase):
    """The embed guard that keeps any single evidence value bounded."""

    def test_small_values_pass_through(self):
        self.assertEqual(ws._cap_evidence_value(28.0), 28.0)
        self.assertIsNone(ws._cap_evidence_value(None))
        small = {"a": 1, "b": 2}
        self.assertEqual(ws._cap_evidence_value(small), small)

    def test_long_list_truncated_to_row_cap(self):
        capped = ws._cap_evidence_value([{"i": i} for i in range(50)])
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["total_rows"], 50)
        self.assertEqual(len(capped["rows"]), ws.MAX_EVIDENCE_ROWS)

    def test_date_table_keeps_newest_rows(self):
        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        table = {"Action": {d: "up" for d in days}, "Firm": {d: "F" for d in days}}
        capped = ws._cap_evidence_value(table)
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["total_rows"], 30)
        self.assertIn(days[-1], capped["rows"]["Action"])  # newest kept
        self.assertNotIn(days[0], capped["rows"]["Action"])  # oldest dropped
        self.assertEqual(len(capped["rows"]["Action"]), ws.MAX_EVIDENCE_ROWS)

    def test_statement_shape_not_row_truncated(self):
        # {period: {line_item: value}} nests the other way; inner keys are not
        # dates, so the row cap must not apply.
        statement = {
            "2024-09-30T00:00:00": {f"Line {i}": i for i in range(30)},
            "2023-09-30T00:00:00": {f"Line {i}": i for i in range(30)},
        }
        self.assertEqual(ws._cap_evidence_value(statement), statement)

    def test_char_ceiling_produces_preview_stub(self):
        capped = ws._cap_evidence_value({"blob": "x" * (ws.MAX_EVIDENCE_CHARS + 100)})
        self.assertTrue(capped["truncated"])
        self.assertIn("total_chars", capped)
        self.assertLessEqual(len(capped["preview"]), ws.MAX_EVIDENCE_CHARS)


class ClassificationFreshnessTest(unittest.TestCase):
    def test_fresh_and_stale_flags(self):
        fresh = {"generated_at": date.today().isoformat()}
        self.assertEqual(ws._classification_freshness(fresh)[1], False)
        old = {"generated_at": (date.today() - timedelta(days=30)).isoformat()}
        self.assertEqual(ws._classification_freshness(old)[1], True)
        self.assertEqual(ws._classification_freshness({}), (None, None))
        self.assertEqual(ws._classification_freshness({"generated_at": "not-a-date"}), ("not-a-date", None))

    def test_worksheet_position_carries_freshness(self):
        rubric = rubric_mod.load_rubric()
        payload = dict(fx.classification_payload(), generated_at=date.today().isoformat())
        sources = {
            "yfinance": ("research.json", fx.research_payload()),
            "classification": ("classification.json", payload),
        }
        sheet = ws.build_worksheet("AAPL", rubric, sources, _args())
        self.assertEqual(sheet["position"]["classification_stale"], False)
        self.assertEqual(sheet["position"]["classification_generated_at"], date.today().isoformat())


class SummaryTest(unittest.TestCase):
    def test_format_summary_reports_without_opening_jsons(self):
        rubric = rubric_mod.load_rubric()
        sources = {
            "yfinance": ("research.json", fx.research_payload()),
            "classification": ("classification.json", fx.classification_payload()),
        }
        sheet = ws.build_worksheet("AAPL", rubric, sources, _args())
        text = ws._format_summary(sheet)
        self.assertIn("ticker: AAPL", text)
        self.assertIn("track: equity", text)
        self.assertIn("held=True", text)
        self.assertIn("groups ok (11)", text)
        self.assertIn("gates: ", text)
        self.assertIn("prior decision: none (new page)", text)

    def test_format_summary_flags_etf_empty_groups(self):
        rubric = rubric_mod.load_rubric()
        sources = {"yfinance": ("research.json", fx.etf_research_payload())}
        sheet = ws.build_worksheet("DGRO", rubric, sources, _args())
        text = ws._format_summary(sheet)
        self.assertIn("track: etf", text)
        self.assertIn("groups empty", text)
        self.assertIn("expected for an ETF", text)


class AssetClassTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()

    def _build(self, ticker, research, classification=None, **arg_overrides):
        sources = {"yfinance": ("r.json", research)}
        if classification is not None:
            sources["classification"] = ("c.json", classification)
        return ws.build_worksheet(ticker, self.rubric, sources, _args(**arg_overrides))

    def test_asset_class_from_classification(self):
        sheet = self._build("DGRO", fx.etf_research_payload(), fx.etf_classification_payload())
        self.assertEqual(sheet["position"]["asset_class"], "etf")
        self.assertTrue(sheet["position"]["is_etf"])

    def test_asset_class_from_quotetype_fallback(self):
        # No classification supplied -> fall back to yfinance overview.quoteType.
        sheet = self._build("DGRO", fx.etf_research_payload())
        self.assertEqual(sheet["position"]["asset_class"], "etf")

    def test_asset_class_cli_override(self):
        sheet = self._build("AAPL", fx.research_payload(), fx.classification_payload(), asset_class="etf")
        self.assertTrue(sheet["position"]["is_etf"])

    def test_equity_defaults_to_stock(self):
        sheet = self._build("AAPL", fx.research_payload(), fx.classification_payload())
        self.assertEqual(sheet["position"]["asset_class"], "stock")
        self.assertFalse(sheet["position"]["is_etf"])

    def test_etf_worksheet_has_fund_dims_and_drops_equity(self):
        sheet = self._build("DGRO", fx.etf_research_payload(), fx.etf_classification_payload())
        dim_ids = {d["id"] for d in sheet["dimensions"]}
        gate_ids = {g["id"] for g in sheet["gates"]}
        self.assertIn("fund_efficiency", dim_ids)
        self.assertIn("fund_quality", dim_ids)
        self.assertNotIn("financial_health", dim_ids)
        self.assertNotIn("insider_activity", dim_ids)
        self.assertEqual(gate_ids, {"data_sufficiency"})

    def test_income_etf_drops_dividend_integrity_gate(self):
        # DGRO is Income (income_role) + a dividend payer, but as an ETF the
        # income-equity dividend_integrity gate does not apply.
        sheet = self._build("DGRO", fx.etf_research_payload(), fx.etf_classification_payload())
        self.assertTrue(sheet["position"]["income_role"])
        self.assertNotIn("dividend_integrity", {g["id"] for g in sheet["gates"]})

    def test_etf_fund_dimensions_resolve_evidence(self):
        sheet = self._build("DGRO", fx.etf_research_payload(), fx.etf_classification_payload())
        eff = next(d for d in sheet["dimensions"] if d["id"] == "fund_efficiency")
        self.assertFalse(eff["unknown"], msg="fund_efficiency should resolve from classification")


FULL_HISTORY_PAGE = """---
title: Test Co
---

# TEST -- Test Co

## Status

- Portfolio Status: active
- Portfolio Role: Quality
- Decision: Hold
- Confidence: High
- Time Horizon: Long-term
- Last Updated: 2026-07-14

## Decision History

| Date | Action | Verdict | Note |
|---|---|---|---|
| 2026-05-01 | Buy | new | Rubric v1.0 score 4.10; initial add. |
| 2026-07-14 | Hold | unchanged | Rubric v1.3 score 3.62; thesis intact. |
"""

EMPTY_HISTORY_PAGE = """---
title: New Co
---

# NEW -- New Co

## Status

- Portfolio Status: watchlist
- Decision: Watchlist
- Confidence: Low
- Time Horizon: Medium-term
- Last Updated: 2026-07-15

## Decision History

| Date | Action | Verdict | Note |
|---|---|---|---|
"""


class PriorDecisionTest(unittest.TestCase):
    def setUp(self):
        self.rubric = rubric_mod.load_rubric()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _write(self, name: str, text: str) -> Path:
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_last_row_plus_status_confidence(self):
        path = self._write("full.md", FULL_HISTORY_PAGE)
        prior = ws._load_prior_decision(path)
        self.assertEqual(prior["date"], "2026-07-14")
        self.assertEqual(prior["action"], "Hold")
        self.assertEqual(prior["verdict"], "unchanged")
        self.assertEqual(prior["confidence"], "High")
        self.assertEqual(prior["time_horizon"], "Long-term")

    def test_empty_history_keeps_status_fields(self):
        path = self._write("empty.md", EMPTY_HISTORY_PAGE)
        prior = ws._load_prior_decision(path)
        self.assertIsNone(prior["date"])
        self.assertIsNone(prior["action"])
        self.assertIsNone(prior["verdict"])
        self.assertEqual(prior["confidence"], "Low")
        self.assertEqual(prior["time_horizon"], "Medium-term")

    def test_missing_path_returns_none(self):
        self.assertIsNone(ws._load_prior_decision(self.base / "does-not-exist.md"))

    def test_none_path_returns_none(self):
        self.assertIsNone(ws._load_prior_decision(None))

    def test_position_context_infers_page_exists_from_thesis_page(self):
        path = self._write("full.md", FULL_HISTORY_PAGE)
        sheet = ws.build_worksheet(
            "AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())},
            _args(thesis_page=path),
        )
        self.assertTrue(sheet["position"]["page_exists"])
        self.assertEqual(sheet["position"]["prior_decision"]["action"], "Hold")

    def test_position_context_page_exists_false_for_missing_thesis_page(self):
        sheet = ws.build_worksheet(
            "AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())},
            _args(thesis_page=self.base / "nope.md"),
        )
        self.assertFalse(sheet["position"]["page_exists"])
        self.assertIsNone(sheet["position"]["prior_decision"])

    def test_explicit_page_exists_overrides_inference(self):
        sheet = ws.build_worksheet(
            "AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())},
            _args(thesis_page=self.base / "nope.md", page_exists=True),
        )
        self.assertTrue(sheet["position"]["page_exists"])

    def test_format_summary_includes_prior_decision_line(self):
        path = self._write("full.md", FULL_HISTORY_PAGE)
        sheet = ws.build_worksheet(
            "AAPL", self.rubric, {"yfinance": ("r.json", fx.research_payload())},
            _args(thesis_page=path),
        )
        text = ws._format_summary(sheet)
        self.assertIn("prior decision: 2026-07-14 Hold (unchanged, High confidence)", text)


if __name__ == "__main__":
    unittest.main()
