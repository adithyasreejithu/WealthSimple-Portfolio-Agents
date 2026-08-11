"""Tests for `src/workspace/investment_worksheet.py` and its three
supporting modules (`financial_metrics.py`, `valuation.py`, `scenarios.py`).

Fixtures live in `tests/fixtures/investment_analyst/`: a trimmed,
synthetic-but-realistic PLTR `investment-analyst-resources` bundle and a
matching `security-technicals` artifact, each carrying a hand-authored
`trace` block with genuine `missing` and `not_applicable` entries (not
derived by running the real skills -- this file is the roadmap's Phase 3
gate, exercised entirely offline, no DuckDB, no network).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from workspace import evidence as evidence_module  # noqa: E402
from workspace import investment_worksheet as iw  # noqa: E402
from workspace.paths import WorkspaceError  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "investment_analyst"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _scope(mode="initial_research", asset_track="equity"):
    return iw.build_analysis_scope(
        run_id="test-run",
        subject="PLTR",
        asset_track=asset_track,
        mode=mode,
        trigger={"type": "user_request", "occurred_at": None, "detail": None},
        decision_horizon="Long-term",
    )


class BuildAnalysisScopeTest(unittest.TestCase):
    def test_initial_research_covers_all_sections(self):
        scope = _scope()
        from workspace.analysis_models import SECTION_IDS

        union = set(scope.evaluate_sections) | set(scope.preserve_sections) | set(scope.not_applicable_sections)
        self.assertEqual(union, set(SECTION_IDS))

    def test_every_policy_mode_produces_a_valid_scope(self):
        # Regression for the two modes (material_event, price_move_review)
        # found missing a section during Phase 3 design -- see
        # design-decisions.md. A ValidationError here means the policy file
        # regressed to an incomplete partition.
        from workspace.analysis_models import get_mode_section_map

        for mode in get_mode_section_map():
            with self.subTest(mode=mode):
                scope = _scope(mode=mode)
                self.assertEqual(scope.mode, mode)

    def test_etf_asset_track_moves_sections_to_not_applicable(self):
        scope = _scope(mode="initial_research", asset_track="etf")
        self.assertIn("expectations_and_results", scope.not_applicable_sections)
        self.assertIn("insider_institutional_capital_allocation", scope.not_applicable_sections)
        self.assertNotIn("expectations_and_results", scope.evaluate_sections)

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            iw.build_analysis_scope(
                run_id="r", subject="PLTR", asset_track="equity", mode="not_a_real_mode",
                trigger={"type": "user_request", "occurred_at": None, "detail": None},
                decision_horizon="Long-term",
            )


class BuildWorksheetDeterminismTest(unittest.TestCase):
    def setUp(self):
        self.bundle = _load("pltr_bundle.json")
        self.technicals = _load("pltr_technicals.json")
        self.scope = _scope()

    def _build(self):
        return iw.build_worksheet(
            run_id="test-run", scope=self.scope, bundle=self.bundle,
            bundle_evidence_id="ev_bundle", technicals=self.technicals,
            technicals_evidence_id="ev_technicals", as_of="2026-08-10",
        )

    def test_repeated_builds_are_byte_identical(self):
        first = json.dumps(self._build(), sort_keys=False)
        second = json.dumps(self._build(), sort_keys=False)
        self.assertEqual(first, second)

    def test_identity_and_schema(self):
        worksheet = self._build()
        self.assertEqual(worksheet["schema"], "investment-worksheet.v1")
        self.assertEqual(worksheet["identity"]["ticker"], "PLTR")
        self.assertEqual(worksheet["identity"]["asset_track"], "equity")

    def test_valuation_methods_all_compute_from_the_fixture(self):
        worksheet = self._build()
        methods = {m["method"] for m in worksheet["valuation_methods"]}
        self.assertEqual(methods, {"earnings_multiple", "fcf_yield", "ev_revenue", "ev_ebitda"})
        for method in worksheet["valuation_methods"]:
            value_range = method["resulting_equity_value_per_share"]
            self.assertLessEqual(value_range["low"], value_range["mid"])
            self.assertLessEqual(value_range["mid"], value_range["high"])

    def test_scenarios_scaffold_from_valuation_range(self):
        worksheet = self._build()
        scenarios = worksheet["scenario_inputs"]
        self.assertIsNotNone(scenarios)
        total_probability = sum(scenarios[name]["probability"] for name in ("bull", "base", "bear"))
        self.assertAlmostEqual(total_probability, 1.0)
        self.assertLessEqual(scenarios["bear"]["fair_value_per_share"], scenarios["bull"]["fair_value_per_share"])

    def test_no_valuation_methods_means_no_scenarios(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["live"]["valuation"] = {}
        bundle["derived"]["fcf_yield"] = None
        worksheet = iw.build_worksheet(run_id="test-run", scope=self.scope, bundle=bundle, as_of="2026-08-10")
        self.assertEqual(worksheet["valuation_methods"], [])
        self.assertIsNone(worksheet["scenario_inputs"])
        self.assertIn(
            "no valuation method could be computed from available inputs", worksheet["unknowns"]
        )


class TracePreservationTest(unittest.TestCase):
    """The gate's central requirement: a bundle field graded `not_applicable`
    must never surface as a worksheet gap, and one graded `missing` always
    must -- the worksheet reads the bundle's own TRACE, it does not
    recompute it with different arithmetic."""

    def setUp(self):
        self.bundle = _load("pltr_bundle.json")
        self.scope = _scope()

    def _unknowns(self, bundle=None):
        worksheet = iw.build_worksheet(
            run_id="test-run", scope=self.scope, bundle=bundle or self.bundle, as_of="2026-08-10"
        )
        return worksheet["unknowns"], worksheet["evidence_health"]["domain_status"]

    def test_not_applicable_domain_is_not_a_gap(self):
        unknowns, domain_status = self._unknowns()
        # `options` is not_applicable in the fixture's live trace (empty chain).
        self.assertEqual(domain_status["options"], "not_applicable")
        self.assertFalse(any("options" in item for item in unknowns))

    def test_missing_domain_is_a_gap(self):
        unknowns, domain_status = self._unknowns()
        self.assertEqual(domain_status["insider"], "missing")
        self.assertTrue(any("evidence domain missing: insider" in item for item in unknowns))

    def test_scoped_completeness_ignores_optional_domains(self):
        # Only initial_research's required domains (all "ok" in the fixture)
        # count -- the optional domains' not_applicable/missing entries must
        # not drag scoped completeness below 100.
        worksheet = iw.build_worksheet(run_id="test-run", scope=self.scope, bundle=self.bundle, as_of="2026-08-10")
        self.assertEqual(worksheet["evidence_health"]["scoped_completeness_pct"], 100.0)
        self.assertFalse(worksheet["evidence_health"]["blocking"])


class CriticalGapBlockingTest(unittest.TestCase):
    def setUp(self):
        self.bundle = _load("pltr_bundle.json")

    def test_required_domain_failure_blocks_under_stop_policy(self):
        bundle = copy.deepcopy(self.bundle)
        # Fail a required domain outright: financials comes back with nothing.
        bundle["trace"]["domains"]["financials"] = {"ok": [], "missing": ["latest_period_end"], "not_applicable": []}
        scope = _scope(mode="initial_research")  # critical_gap_policy: stop
        worksheet = iw.build_worksheet(run_id="test-run", scope=scope, bundle=bundle, as_of="2026-08-10")
        self.assertTrue(worksheet["evidence_health"]["blocking"])
        self.assertTrue(
            any("financials" in reason for reason in worksheet["evidence_health"]["blocking_reasons"])
        )

    def test_same_failure_does_not_block_under_proceed_with_gap_disclosure(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["trace"]["domains"]["financials"] = {"ok": [], "missing": ["latest_period_end"], "not_applicable": []}
        scope = _scope(mode="scheduled_review")  # critical_gap_policy: proceed_with_gap_disclosure
        worksheet = iw.build_worksheet(run_id="test-run", scope=scope, bundle=bundle, as_of="2026-08-10")
        self.assertFalse(worksheet["evidence_health"]["blocking"])


class MissingTechnicalsTest(unittest.TestCase):
    def test_absent_technicals_artifact_is_a_gap_not_a_crash(self):
        bundle = _load("pltr_bundle.json")
        scope = _scope()
        worksheet = iw.build_worksheet(run_id="test-run", scope=scope, bundle=bundle, technicals=None, as_of="2026-08-10")
        self.assertIsNone(worksheet["price_and_market_context"]["technicals"])
        self.assertEqual(
            worksheet["price_and_market_context"]["technicals_gap"],
            "no security-technicals artifact registered in this run",
        )
        self.assertIn(
            "no security-technicals artifact registered in this run", worksheet["unknowns"]
        )


class ContextSizeCeilingTest(unittest.TestCase):
    def setUp(self):
        self.worksheet = iw.build_worksheet(
            run_id="test-run", scope=_scope(), bundle=_load("pltr_bundle.json"),
            technicals=_load("pltr_technicals.json"), as_of="2026-08-10",
        )
        self.context = iw.render_analyst_context(self.worksheet)

    def test_context_stays_under_the_ceiling(self):
        self.assertLess(len(self.context), iw.CONTEXT_SIZE_CEILING_CHARS)

    def test_no_raw_price_series_in_context(self):
        # The full OHLCV/beta series lives only in the sibling .json; the
        # context is bullets. A future regression dumping a raw series in
        # would blow well past a generous per-section line cap.
        for section in self.context.split("\n## "):
            lines = [line for line in section.splitlines() if line.strip().startswith("-")]
            self.assertLessEqual(len(lines), 20, msg=f"section too long: {section.splitlines()[0]!r}")

    def test_context_mentions_the_ticker(self):
        self.assertIn("PLTR", self.context)


class BuildWorksheetForRunTest(unittest.TestCase):
    """The I/O wrapper: locates registered evidence in a real (temp) run
    directory, writes the worksheet + context, registers both, and appends
    an audit event. Uses the same bare-`TemporaryDirectory`-as-run_dir
    pattern as `test_investment_thesis_validation.py`'s `RunDirTestCase`."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.run_dir = Path(self.temp_dir.name)
        self.run_id = "2026-08-10T140000Z-test"
        self.scope = _scope()

    def _register_bundle(self):
        artifact = self.run_dir / "evidence" / "PLTR-2026-08-10-resources.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text((FIXTURES / "pltr_bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
        return evidence_module.register(
            self.run_dir, run_id=self.run_id, evidence_type="market_data_bundle",
            source_name="investment-analyst-resources", status="partial", artifact=artifact,
        )

    def _register_technicals(self):
        artifact = self.run_dir / "calculations" / "security-technicals-PLTR-2026-08-10.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text((FIXTURES / "pltr_technicals.json").read_text(encoding="utf-8"), encoding="utf-8")
        return evidence_module.register(
            self.run_dir, run_id=self.run_id, evidence_type="derived_calculation",
            source_name="security-technicals", status="available", artifact=artifact,
        )

    def test_raises_without_a_registered_bundle(self):
        with self.assertRaises(WorkspaceError):
            iw.build_worksheet_for_run(self.run_dir, run_id=self.run_id, ticker="PLTR", scope=self.scope)

    def test_writes_worksheet_and_context_and_registers_evidence(self):
        self._register_bundle()
        self._register_technicals()
        worksheet, worksheet_path, worksheet_hash = iw.build_worksheet_for_run(
            self.run_dir, run_id=self.run_id, ticker="PLTR", scope=self.scope
        )
        self.assertTrue(worksheet_path.is_file())
        self.assertTrue(worksheet_hash.startswith("sha256:"))
        context_path = worksheet_path.with_name(worksheet_path.name.replace("-worksheet.json", "-analyst-context.md"))
        self.assertTrue(context_path.is_file())

        records = evidence_module.read_records(self.run_dir)
        types = {record["evidence_type"] for record in records}
        self.assertIn("worksheet", types)
        self.assertIn("analyst_context", types)

        events = json.loads(
            "[" + ",".join((self.run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()) + "]"
        )
        self.assertTrue(any(event["event"] == "worksheet_built" for event in events))
        self.assertIsNotNone(worksheet["identity"]["ticker"])

    def test_missing_technicals_artifact_is_tolerated(self):
        self._register_bundle()
        worksheet, _, _ = iw.build_worksheet_for_run(
            self.run_dir, run_id=self.run_id, ticker="PLTR", scope=self.scope
        )
        self.assertIsNone(worksheet["price_and_market_context"]["technicals"])


if __name__ == "__main__":
    unittest.main()
