"""Tests for `src/workspace/analysis_models.py` and `thesis_validation.py`.

Every fixture is synthetic (`ticker: TEST`), mirroring `test_run_workspace.py`'s
convention: no real positions, prices, or account data, and no test asserts a
financial value.

The `docs/architecture/investment_analyst_examples/*.yaml` example artifacts
are intentionally trimmed for readability (several `changed` sections have no
matching `sections` entry) and are not loaded here as pass/fail fixtures for
that reason -- they illustrate the schema shape, not a literal payload this
validator must accept. This file builds its own complete fixtures instead.
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from workspace import evidence as evidence_module  # noqa: E402
from workspace.analysis_models import (  # noqa: E402
    AnalysisScope,
    InvestmentThesis,
    SCENARIO_PROBABILITY_TOLERANCE,
    SECTION_IDS,
    find_forbidden_fields,
)
from workspace.thesis_validation import (  # noqa: E402
    check_confidence_cap,
    check_evidence_citations,
    check_section_scope,
    confidence_cap,
    validate_thesis,
)

# --- fixture builders ------------------------------------------------------

_ALL_SECTIONS = list(SECTION_IDS)


def _section_entry(section_id: str) -> dict:
    label = None
    if section_id == "expectations_and_results":
        label = "current_snapshot_only"
    elif section_id == "options_and_positioning":
        label = "snapshot_only"
    return {
        "narrative": f"Synthetic narrative for {section_id}.",
        "evidence_ids": ["ev_1"],
        "capability_label": label,
    }


def _valid_scenario(probability: float) -> dict:
    return {
        "probability": probability,
        "horizon": "Long-term",
        "revenue_assumption": "n/a",
        "margin_assumption": "n/a",
        "dilution_assumption": "n/a",
        "valuation_method": "ev_revenue",
        "fair_value_per_share": 25.0,
        "expected_return_pct": 0.05,
        "conditions": [],
    }


def build_valid_thesis_payload(**overrides) -> dict:
    payload = {
        "schema": "investment-thesis.v1",
        "run_id": "2026-08-09T140000Z-test",
        "artifact_id": "th_test0000001",
        "generated_at": "2026-08-09T14:32:10Z",
        "security": {"ticker": "TEST", "provider_symbol": "TEST", "asset_track": "equity"},
        "analysis_mode": "initial_research",
        "policy_version": "v1.0",
        "worksheet_ref": {"path": "calculations/TEST-worksheet.json", "hash": "sha256:" + "a" * 64},
        "prior_thesis": {
            "exists": False, "artifact_id": None, "version": None,
            "content_hash": None, "approved_at": None,
        },
        "section_states": {section_id: "changed" for section_id in _ALL_SECTIONS},
        "conclusion": {
            "fundamental_rating": "neutral",
            "valuation_stance": "reasonable",
            "thesis_direction": "initial",
            "thesis_confidence": "medium",
            "evidence_completeness_pct": 90.0,
            "investment_case": "Synthetic investment case.",
            "case_against": "Synthetic case against.",
            "most_important_catalyst": "Synthetic catalyst.",
            "most_important_risk": "Synthetic risk.",
            "most_important_unknown": "Synthetic unknown.",
            "analysis_horizon": "Long-term",
            "as_of": "2026-08-09T14:00:00Z",
        },
        "key_claims": [
            {
                "claim_id": "thesis_1",
                "statement": "Synthetic critical claim.",
                "claim_type": "fact",
                "importance": "critical",
                "evidence_ids": ["ev_1"],
                "status_vs_prior": "new",
                "confidence": "high",
            },
            {
                "claim_id": "thesis_2",
                "statement": "Synthetic supporting claim.",
                "claim_type": "inference",
                "importance": "supporting",
                "evidence_ids": ["ev_2"],
                "status_vs_prior": "new",
                "confidence": "medium",
            },
        ],
        "sections": {section_id: _section_entry(section_id) for section_id in _ALL_SECTIONS},
        "valuation": {
            "methods": [
                {
                    "method": "ev_revenue",
                    "base_metric": "NTM revenue",
                    "base_period": "FY2026E",
                    "normalization_adjustments": [],
                    "assumptions": {"growth": "10%", "margin": None, "discount_rate": None, "terminal": None},
                    "currency": "USD",
                    "resulting_equity_value_per_share": {"low": 20.0, "mid": 25.0, "high": 30.0},
                    "sensitivity_note": "Synthetic sensitivity note.",
                }
            ],
            "interpretation": "Synthetic interpretation of the valuation range.",
        },
        "scenarios": {
            "bull": _valid_scenario(0.30),
            "base": _valid_scenario(0.45),
            "bear": _valid_scenario(0.25),
        },
        "conditions": {
            "upgrade_conditions": [
                {
                    "trigger_id": "up_1", "metric": "revenue_growth_yoy", "operator": "gte",
                    "threshold": 0.20, "confirmation_periods": 1, "affected_rating": "attractive",
                    "qualitative_description": None,
                }
            ],
            "downgrade_conditions": [],
            "invalidation_conditions": [
                {
                    "trigger_id": "inv_1", "metric": None, "operator": "qualitative",
                    "threshold": None, "confirmation_periods": None, "affected_rating": "unattractive",
                    "qualitative_description": "Synthetic invalidation description.",
                }
            ],
            "monitoring_items": ["Synthetic monitoring item."],
        },
        "known_conflicts": [],
        "unknowns": ["Synthetic unknown item."],
        "evidence_ids_used": ["ev_1", "ev_2"],
        "challenger": {"required": True, "completed": True, "material_objections": []},
        "validation": {"status": "pending", "errors": [], "warnings": []},
        "human_review_required": True,
    }
    payload.update(overrides)
    return payload


def build_valid_scope_payload(**overrides) -> dict:
    payload = {
        "schema": "analysis-scope.v1",
        "run_id": "2026-08-09T140000Z-test",
        "subject": "TEST",
        "asset_track": "equity",
        "mode": "initial_research",
        "trigger": {"type": "user_request", "occurred_at": "2026-08-09T14:00:00Z", "detail": "Synthetic."},
        "decision_horizon": "Long-term",
        "evaluate_sections": list(_ALL_SECTIONS),
        "preserve_sections": [],
        "not_applicable_sections": [],
        "required_evidence_domains": ["financials", "earnings", "valuation"],
        "optional_evidence_domains": ["options"],
        "critical_gap_policy": "stop",
    }
    payload.update(overrides)
    return payload


def deep(payload: dict) -> dict:
    return copy.deepcopy(payload)


# --- 1. shape: valid fixtures parse -----------------------------------


class ValidFixtureTest(unittest.TestCase):
    def test_valid_thesis_parses(self):
        thesis = InvestmentThesis.model_validate(build_valid_thesis_payload())
        self.assertEqual(thesis.security.ticker, "TEST")
        self.assertEqual(len(thesis.section_states), 16)

    def test_valid_scope_parses(self):
        scope = AnalysisScope.model_validate(build_valid_scope_payload())
        self.assertEqual(scope.subject, "TEST")

    def test_warning_thesis_with_capped_confidence_is_still_valid_shape(self):
        # Some unknowns, evidence_completeness_pct in the warning band -- the
        # document itself still parses; the *cap* is thesis_validation's job.
        payload = build_valid_thesis_payload()
        payload["conclusion"]["evidence_completeness_pct"] = 65.0
        payload["conclusion"]["thesis_confidence"] = "medium"
        thesis = InvestmentThesis.model_validate(payload)
        self.assertEqual(thesis.conclusion.evidence_completeness_pct, 65.0)


# --- 2. forbidden fields ------------------------------------------------


class ForbiddenFieldTest(unittest.TestCase):
    def test_target_weight_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["target_weight"] = 0.05
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_shares_to_buy_is_rejected_when_nested_deep(self):
        payload = build_valid_thesis_payload()
        payload["conditions"]["upgrade_conditions"][0]["shares_to_buy"] = 100
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_broker_order_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["broker_order"] = {"id": "abc"}
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_portfolio_action_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["portfolio_action"] = "Buy"
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_find_forbidden_fields_reports_every_violation(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["target_weight"] = 0.05
        payload["conclusion"]["portfolio_action"] = "Buy"
        problems = find_forbidden_fields(payload)
        joined = " ".join(problems)
        self.assertIn("target_weight", joined)
        self.assertIn("portfolio_action", joined)

    def test_execution_price_and_broker_are_rejected(self):
        for field_name in ("execution_price", "broker", "fill_price", "order_type"):
            with self.subTest(field=field_name):
                payload = build_valid_thesis_payload()
                payload["conclusion"][field_name] = "irrelevant"
                with self.assertRaises(ValidationError):
                    InvestmentThesis.model_validate(payload)


# --- 3. scenario math -----------------------------------------------------


class ScenarioMathTest(unittest.TestCase):
    def test_probabilities_summing_to_1_0_pass(self):
        InvestmentThesis.model_validate(build_valid_thesis_payload())

    def test_probabilities_totaling_95_percent_fail(self):
        payload = build_valid_thesis_payload()
        payload["scenarios"]["bear"] = _valid_scenario(0.20)
        with self.assertRaises(ValidationError) as ctx:
            InvestmentThesis.model_validate(payload)
        self.assertIn("scenario probabilities", str(ctx.exception))

    def test_probabilities_totaling_90_percent_fail(self):
        payload = build_valid_thesis_payload()
        payload["scenarios"]["bull"] = _valid_scenario(0.30)
        payload["scenarios"]["base"] = _valid_scenario(0.40)
        payload["scenarios"]["bear"] = _valid_scenario(0.20)
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_sum_just_inside_policy_tolerance_passes(self):
        payload = build_valid_thesis_payload()
        nudge = SCENARIO_PROBABILITY_TOLERANCE * 0.5
        payload["scenarios"]["bear"] = _valid_scenario(0.25 + nudge)
        InvestmentThesis.model_validate(payload)

    def test_sum_just_outside_policy_tolerance_fails(self):
        payload = build_valid_thesis_payload()
        nudge = SCENARIO_PROBABILITY_TOLERANCE * 2
        payload["scenarios"]["bear"] = _valid_scenario(0.25 + nudge)
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)


# --- 4. section / evidence internal consistency ----------------------


class SectionConsistencyTest(unittest.TestCase):
    def test_content_for_a_not_evaluated_section_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["section_states"]["company_profile"] = "unchanged"
        # sections still has an entry for it -- preserved sections must carry
        # no replacement content.
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_changed_section_missing_its_sections_entry_is_rejected(self):
        payload = build_valid_thesis_payload()
        del payload["sections"]["company_profile"]
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)

    def test_missing_section_state_is_rejected(self):
        payload = build_valid_thesis_payload()
        del payload["section_states"]["catalysts"]
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)


class EvidenceDeclarationTest(unittest.TestCase):
    def test_citation_missing_from_evidence_ids_used_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["key_claims"][0]["evidence_ids"] = ["ev_undeclared"]
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)


class HumanReviewPinnedTest(unittest.TestCase):
    def test_human_review_required_false_is_rejected(self):
        payload = build_valid_thesis_payload()
        payload["human_review_required"] = False
        with self.assertRaises(ValidationError):
            InvestmentThesis.model_validate(payload)


# --- 5. analysis-scope.v1 --------------------------------------------


class AnalysisScopeTest(unittest.TestCase):
    def test_section_in_two_buckets_is_rejected(self):
        payload = build_valid_scope_payload()
        payload["preserve_sections"] = ["company_profile"]
        # company_profile is already in evaluate_sections (full list) above.
        with self.assertRaises(ValidationError):
            AnalysisScope.model_validate(payload)

    def test_section_missing_from_every_bucket_is_rejected(self):
        payload = build_valid_scope_payload()
        payload["evaluate_sections"] = [s for s in _ALL_SECTIONS if s != "catalysts"]
        with self.assertRaises(ValidationError):
            AnalysisScope.model_validate(payload)

    def test_unknown_evidence_domain_is_rejected(self):
        payload = build_valid_scope_payload()
        payload["required_evidence_domains"] = ["not_a_real_domain"]
        with self.assertRaises(ValidationError):
            AnalysisScope.model_validate(payload)


# --- 6. thesis_validation: needs a run_dir + evidence registry --------


class RunDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.run_dir = Path(self.temp_dir.name)
        self.run_id = "2026-08-09T140000Z-test"

    def _register(self, evidence_id: str, *, status: str = "available", mutate_after: bool = False) -> None:
        artifact = self.run_dir / "evidence" / f"{evidence_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"value": "original"}', encoding="utf-8")
        evidence_module.register(
            self.run_dir,
            run_id=self.run_id,
            evidence_type="test_fixture",
            source_name="synthetic",
            status=status,
            artifact=artifact if status != "missing" else None,
            evidence_id=evidence_id,
        )
        if mutate_after:
            artifact.write_text('{"value": "mutated after registration"}', encoding="utf-8")


class EvidenceCitationResolutionTest(RunDirTestCase):
    def test_fabricated_evidence_id_fails(self):
        self._register("ev_1")
        self._register("ev_2")
        payload = build_valid_thesis_payload()
        payload["key_claims"][0]["evidence_ids"] = ["ev_1", "ev_does_not_exist"]
        payload["evidence_ids_used"] = ["ev_1", "ev_2", "ev_does_not_exist"]
        thesis = InvestmentThesis.model_validate(payload)
        problems = check_evidence_citations(thesis, self.run_dir)
        self.assertTrue(any("unregistered evidence_id: ev_does_not_exist" in p for p in problems))

    def test_mutated_evidence_artifact_hash_fails(self):
        self._register("ev_1", mutate_after=True)
        self._register("ev_2")
        thesis = InvestmentThesis.model_validate(build_valid_thesis_payload())
        problems = check_evidence_citations(thesis, self.run_dir)
        self.assertTrue(any("no longer matches the registry" in p for p in problems))

    def test_citing_evidence_registered_as_missing_fails(self):
        self._register("ev_1", status="missing")
        self._register("ev_2")
        thesis = InvestmentThesis.model_validate(build_valid_thesis_payload())
        problems = check_evidence_citations(thesis, self.run_dir)
        self.assertTrue(any("registered as missing" in p for p in problems))

    def test_intact_registered_evidence_passes(self):
        self._register("ev_1")
        self._register("ev_2")
        thesis = InvestmentThesis.model_validate(build_valid_thesis_payload())
        problems = check_evidence_citations(thesis, self.run_dir)
        self.assertEqual(problems, [])


class ValidateThesisEndToEndTest(RunDirTestCase):
    def test_valid_thesis_with_registered_evidence_and_scope_is_valid(self):
        self._register("ev_1")
        self._register("ev_2")
        result = validate_thesis(
            build_valid_thesis_payload(),
            run_dir=self.run_dir,
            scope=build_valid_scope_payload(),
            domain_status={"financials": "ok", "earnings": "ok", "valuation": "ok"},
        )
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["status"], "valid")

    def test_fabricated_evidence_id_fails_full_validation(self):
        self._register("ev_1")
        self._register("ev_2")
        payload = build_valid_thesis_payload()
        payload["key_claims"][0]["evidence_ids"] = ["ev_1", "ev_ghost"]
        payload["evidence_ids_used"] = ["ev_1", "ev_2", "ev_ghost"]
        result = validate_thesis(payload, run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("ev_ghost" in e for e in result["errors"]))

    def test_target_weight_field_fails_full_validation(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["target_weight"] = 0.05
        result = validate_thesis(payload, run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["thesis"], None)

    def test_missing_run_dir_and_scope_produce_warnings_not_errors(self):
        result = validate_thesis(build_valid_thesis_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "valid_with_warnings")
        self.assertTrue(any("run_dir" in w for w in result["warnings"]))
        self.assertTrue(any("scope" in w for w in result["warnings"]))

    def test_section_scope_mismatch_fails(self):
        self._register("ev_1")
        self._register("ev_2")
        scope_payload = build_valid_scope_payload(
            evaluate_sections=[s for s in _ALL_SECTIONS if s != "catalysts"],
            preserve_sections=["catalysts"],
        )
        result = validate_thesis(build_valid_thesis_payload(), run_dir=self.run_dir, scope=scope_payload)
        self.assertFalse(result["ok"])
        self.assertTrue(any("catalysts" in e for e in result["errors"]))


# --- 7. confidence cap ---------------------------------------------------


class ConfidenceCapTest(unittest.TestCase):
    def test_trace_completeness_60_caps_at_medium(self):
        self.assertEqual(confidence_cap(60.0), "medium")

    def test_trace_completeness_below_blocking_threshold_caps_at_low(self):
        self.assertEqual(confidence_cap(30.0), "low")

    def test_trace_completeness_above_warning_threshold_has_no_cap(self):
        self.assertIsNone(confidence_cap(95.0))

    def test_unknown_gate_caps_at_low_even_with_high_completeness(self):
        cap = confidence_cap(95.0, required_domains=["financials"], domain_status={})
        self.assertEqual(cap, "low")

    def test_failed_gate_caps_at_low(self):
        cap = confidence_cap(
            95.0, required_domains=["financials"], domain_status={"financials": "missing"}
        )
        self.assertEqual(cap, "low")

    def test_all_gates_ok_defers_to_completeness_threshold(self):
        cap = confidence_cap(
            95.0, required_domains=["financials"], domain_status={"financials": "ok"}
        )
        self.assertIsNone(cap)

    def test_high_confidence_exceeding_medium_cap_is_flagged(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["evidence_completeness_pct"] = 60.0
        payload["conclusion"]["thesis_confidence"] = "high"
        thesis = InvestmentThesis.model_validate(payload)
        problems = check_confidence_cap(thesis)
        self.assertTrue(any("exceeds the policy cap" in p for p in problems))

    def test_medium_confidence_at_the_cap_is_not_flagged(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["evidence_completeness_pct"] = 60.0
        payload["conclusion"]["thesis_confidence"] = "medium"
        thesis = InvestmentThesis.model_validate(payload)
        self.assertEqual(check_confidence_cap(thesis), [])

    def test_unknown_gate_flags_even_medium_confidence(self):
        payload = build_valid_thesis_payload()
        payload["conclusion"]["evidence_completeness_pct"] = 95.0
        payload["conclusion"]["thesis_confidence"] = "medium"
        thesis = InvestmentThesis.model_validate(payload)
        problems = check_confidence_cap(
            thesis, required_domains=["financials"], domain_status={}
        )
        self.assertTrue(any("exceeds the policy cap" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
