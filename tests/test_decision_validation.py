"""Tests for `src/workspace/decision_validation.py` (Phase 11).

Every fixture is synthetic (`ticker: TEST`), mirroring
`test_investment_thesis_validation.py`'s convention: no real positions,
prices, or account data.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from workspace import evidence as evidence_module  # noqa: E402
from workspace.models import DecisionProposal  # noqa: E402
from workspace.decision_validation import (  # noqa: E402
    check_action_consistent_with_policy,
    check_action_matches_ownership_vocabulary,
    check_action_requires_ownership,
    check_decision_draft,
    check_order_guidance_present,
    check_order_guidance_prices_are_grounded,
    check_policy_worksheet_ref,
    check_thesis_ref,
    save_decision,
    validate_decision,
)
from workspace.models import OrderGuidance, PriceLevel  # noqa: E402
from workspace.validation import find_execution_keys  # noqa: E402

RUN_ID = "2026-08-09T140000Z-test"


def build_valid_decision_payload(**overrides) -> dict:
    payload = {
        "proposal_id": "dp_test0000001",
        "run_id": RUN_ID,
        "generated_at": "2026-08-09T14:32:10Z",
        "subject": {"type": "security", "identifiers": {"ticker": "TEST"}},
        "proposed_action": "Add",
        "sizing": {"current_weight_pct": 4.0, "proposed_weight_pct": 6.0, "rationale": "Synthetic sizing rationale."},
        "confidence": "Medium",
        "summary": "Synthetic decision summary citing the thesis and policy checks.",
        "thesis_ref": {"path": "agent_outputs/TEST-thesis.json", "hash": "sha256:" + "a" * 64},
        "policy_worksheet_ref": {"path": "calculations/TEST-policy-worksheet.json", "hash": "sha256:" + "b" * 64},
        "policy_version": "v1.1",
        "supporting_evidence_ids": [],
        "uncertainties": [],
        "policy_checks": [
            {"name": "single_name_cap", "result": "pass", "detail": None},
            {"name": "group_allocation_target", "result": "pass", "detail": None},
        ],
    }
    payload.update(overrides)
    return payload


def _policy_worksheet(*, single_name_result="pass", group_result="pass", currently_held=True) -> dict:
    return {
        "schema": "portfolio-policy-worksheet.v1",
        "subject": {"ticker": "TEST", "ticker_id": 1, "primary_group": "Core", "currently_held": currently_held},
        "policy_checks": [
            {"name": "single_name_cap", "result": single_name_result, "detail": None},
            {"name": "group_allocation_target", "result": group_result, "detail": None},
        ],
        "price_and_market_context": {"week52_low": 40.0, "week52_high": 60.0},
    }


ORDER_GUIDANCE_SOURCE = "policy_worksheet.price_and_market_context.week52_low"
ORDER_GUIDANCE_PRICE = 40.0


def _order_guidance_payload(**overrides) -> dict:
    """A valid `order_guidance` block whose `reference_price` matches
    `_policy_worksheet()`'s `price_and_market_context.week52_low` exactly --
    the fixture pair every Buy/Add/Trim/Sell test in this file cites."""
    payload = {
        "order_type": "Limit",
        "reference_price": {"price": ORDER_GUIDANCE_PRICE, "source": ORDER_GUIDANCE_SOURCE, "rationale": "test"},
        "limit_price": {"price": ORDER_GUIDANCE_PRICE, "source": ORDER_GUIDANCE_SOURCE, "rationale": "test"},
    }
    payload.update(overrides)
    return payload


# --- schema / enum enforcement ---------------------------------------------


class SchemaEnforcementTest(unittest.TestCase):
    def test_valid_payload_parses(self):
        DecisionProposal.model_validate(build_valid_decision_payload())

    def test_locked_action_enum_rejects_lowercase(self):
        with self.assertRaises(ValidationError):
            DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="add"))

    def test_locked_action_enum_rejects_watchlist_and_avoid(self):
        # Watchlist/Avoid are not-held research outcomes, reserved for the
        # legacy stock-analyst / kb-intake path, never a portfolio action.
        for action in ("Watchlist", "Avoid"):
            with self.assertRaises(ValidationError):
                DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))

    def test_all_five_portfolio_actions_are_accepted(self):
        for action in ("Buy", "Hold", "Trim", "Sell", "Add"):
            DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))

    def test_all_four_wishlist_actions_are_accepted_by_the_schema(self):
        for action in ("Buy", "Watch", "Wait", "Pass"):
            DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))


# --- policy-consistency gate -------------------------------------------


class PolicyConsistencyTest(unittest.TestCase):
    def test_buy_is_blocked_when_single_name_cap_fails(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Buy"))
        problems = check_action_consistent_with_policy(decision, _policy_worksheet(single_name_result="fail"))
        self.assertTrue(any("single_name_cap" in p for p in problems))

    def test_add_is_blocked_when_group_allocation_target_fails(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Add"))
        problems = check_action_consistent_with_policy(decision, _policy_worksheet(group_result="fail"))
        self.assertTrue(any("group_allocation_target" in p for p in problems))

    def test_trim_is_allowed_when_single_name_cap_fails(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Trim"))
        problems = check_action_consistent_with_policy(decision, _policy_worksheet(single_name_result="fail"))
        self.assertEqual(problems, [])

    def test_hold_is_allowed_when_single_name_cap_fails(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Hold"))
        problems = check_action_consistent_with_policy(decision, _policy_worksheet(single_name_result="fail"))
        self.assertEqual(problems, [])

    def test_add_is_allowed_when_all_checks_pass(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Add"))
        problems = check_action_consistent_with_policy(decision, _policy_worksheet())
        self.assertEqual(problems, [])

    def test_watch_is_required_when_not_owned_and_group_allocation_fails(self):
        # A not-owned security whose group is over cap cannot be `Buy` -- that
        # is exactly the `Watch` semantics (thesis attractive, policy blocks
        # entry), not `Add`'s owned-vocabulary equivalent.
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Buy"))
        problems = check_action_consistent_with_policy(
            decision, _policy_worksheet(group_result="fail", currently_held=False)
        )
        self.assertTrue(any("group_allocation_target" in p for p in problems))

    def test_watch_wait_pass_are_allowed_when_not_owned_and_a_check_fails(self):
        for action in ("Watch", "Wait", "Pass"):
            decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))
            problems = check_action_consistent_with_policy(
                decision, _policy_worksheet(group_result="fail", currently_held=False)
            )
            self.assertEqual(problems, [], msg=f"{action} should be allowed on a failing check")


# --- ownership-vocabulary gate: owned vs. not-owned action sets ------------


class OwnershipVocabularyTest(unittest.TestCase):
    def test_owned_vocabulary_is_accepted_when_currently_held(self):
        for action in ("Buy", "Hold", "Trim", "Sell", "Add"):
            decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))
            problems = check_action_matches_ownership_vocabulary(decision, _policy_worksheet(currently_held=True))
            self.assertEqual(problems, [], msg=f"{action} should be valid when owned")

    def test_wishlist_vocabulary_is_accepted_when_not_currently_held(self):
        for action in ("Buy", "Watch", "Wait", "Pass"):
            decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))
            problems = check_action_matches_ownership_vocabulary(decision, _policy_worksheet(currently_held=False))
            self.assertEqual(problems, [], msg=f"{action} should be valid when not owned")

    def test_wishlist_vocabulary_rejected_when_currently_held(self):
        for action in ("Watch", "Wait", "Pass"):
            decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))
            problems = check_action_matches_ownership_vocabulary(decision, _policy_worksheet(currently_held=True))
            self.assertTrue(problems, msg=f"{action} should be rejected when owned")

    def test_owned_vocabulary_rejected_when_not_currently_held(self):
        for action in ("Hold", "Trim", "Sell", "Add"):
            decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action=action))
            problems = check_action_matches_ownership_vocabulary(decision, _policy_worksheet(currently_held=False))
            self.assertTrue(problems, msg=f"{action} should be rejected when not owned")


# --- ownership gate: Trim/Sell require an existing position ----------------


class OwnershipGateTest(unittest.TestCase):
    def test_trim_is_blocked_when_not_currently_held(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Trim"))
        problems = check_action_requires_ownership(decision, _policy_worksheet(currently_held=False))
        self.assertTrue(any("currently held" in p for p in problems))

    def test_sell_is_blocked_when_not_currently_held(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Sell"))
        problems = check_action_requires_ownership(decision, _policy_worksheet(currently_held=False))
        self.assertTrue(any("currently held" in p for p in problems))

    def test_trim_is_allowed_when_currently_held(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Trim"))
        problems = check_action_requires_ownership(decision, _policy_worksheet(currently_held=True))
        self.assertEqual(problems, [])

    def test_buy_is_allowed_when_not_currently_held(self):
        # Buy/Add/Hold never require ownership -- the gate only constrains
        # actions that presuppose an existing position.
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Buy"))
        problems = check_action_requires_ownership(decision, _policy_worksheet(currently_held=False))
        self.assertEqual(problems, [])

    def test_missing_subject_is_treated_as_not_held(self):
        # A worksheet with no `subject` key at all (should not happen from
        # `build_policy_worksheet`, but defend against it) must not default
        # to permitting Trim/Sell.
        decision = DecisionProposal.model_validate(build_valid_decision_payload(proposed_action="Sell"))
        problems = check_action_requires_ownership(decision, {"policy_checks": []})
        self.assertTrue(problems)


# --- ref hash-verification ------------------------------------------------


class RunDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.run_dir = Path(self.temp_dir.name)
        self.run_id = RUN_ID

    def _write_ref(self, relpath: str, content: bytes) -> dict:
        path = self.run_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"path": relpath, "hash": "sha256:" + evidence_module.content_hash(path)}

    def _register_thesis_and_policy_worksheet(self, *, policy_checks_result="pass", currently_held=True) -> dict:
        """Writes real files for both refs and returns the ref overrides a
        payload built from `build_valid_decision_payload()` needs, mirroring
        `test_investment_thesis_validation.py`'s `_register_worksheet`."""
        thesis_ref = self._write_ref("agent_outputs/TEST-thesis.json", b'{"schema": "investment-thesis.v1"}')
        policy_worksheet = _policy_worksheet(single_name_result=policy_checks_result, currently_held=currently_held)
        policy_worksheet_ref = self._write_ref(
            "calculations/TEST-policy-worksheet.json",
            json.dumps(policy_worksheet).encode("utf-8"),
        )
        return {"thesis_ref": thesis_ref, "policy_worksheet_ref": policy_worksheet_ref}


class RefHashVerificationTest(RunDirTestCase):
    def test_intact_refs_pass(self):
        refs = self._register_thesis_and_policy_worksheet()
        payload = build_valid_decision_payload(**refs)
        decision = DecisionProposal.model_validate(payload)
        self.assertEqual(check_thesis_ref(decision, self.run_dir), [])
        self.assertEqual(check_policy_worksheet_ref(decision, self.run_dir), [])

    def test_mutated_thesis_fails_hash_check(self):
        refs = self._register_thesis_and_policy_worksheet()
        (self.run_dir / refs["thesis_ref"]["path"]).write_bytes(b'{"mutated": true}')
        decision = DecisionProposal.model_validate(build_valid_decision_payload(**refs))
        problems = check_thesis_ref(decision, self.run_dir)
        self.assertTrue(any("no longer matches" in p for p in problems))

    def test_missing_policy_worksheet_file_fails(self):
        decision = DecisionProposal.model_validate(build_valid_decision_payload())
        problems = check_policy_worksheet_ref(decision, self.run_dir)
        self.assertTrue(any("missing on disk" in p for p in problems))


# --- end-to-end validate_decision ------------------------------------------


class ValidateDecisionEndToEndTest(RunDirTestCase):
    def test_consistent_decision_is_valid(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="pass")
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Add", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["status"], "valid")

    def test_buy_against_a_failing_policy_worksheet_is_invalid(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="fail")
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("single_name_cap" in e for e in result["errors"]))

    def test_trim_against_a_failing_policy_worksheet_is_valid(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="fail")
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Trim", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])

    def test_sell_on_a_non_owned_subject_is_invalid(self):
        refs = self._register_thesis_and_policy_worksheet(currently_held=False)
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Sell", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("currently held" in e for e in result["errors"]))

    def test_buy_on_a_non_owned_subject_is_valid(self):
        refs = self._register_thesis_and_policy_worksheet(currently_held=False)
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])

    def test_add_on_a_non_owned_subject_is_invalid(self):
        # Owned-vocabulary action proposed against a not-owned subject: caught
        # by the ownership-vocabulary gate now, not just Trim/Sell.
        refs = self._register_thesis_and_policy_worksheet(currently_held=False)
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Add", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("not valid for a not-owned security" in e for e in result["errors"]))

    def test_watch_on_an_owned_subject_is_invalid(self):
        refs = self._register_thesis_and_policy_worksheet(currently_held=True)
        result = validate_decision(build_valid_decision_payload(proposed_action="Watch", **refs), run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(any("not valid for an owned security" in e for e in result["errors"]))

    def test_watch_is_required_when_not_owned_and_a_policy_check_fails(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="fail", currently_held=False)
        buy_result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=_order_guidance_payload(), **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(buy_result["ok"])
        self.assertTrue(any("single_name_cap" in e for e in buy_result["errors"]))
        watch_result = validate_decision(build_valid_decision_payload(proposed_action="Watch", **refs), run_dir=self.run_dir)
        self.assertTrue(watch_result["ok"], watch_result["errors"])

    def test_pass_and_wait_are_valid_when_not_owned_and_all_checks_pass(self):
        refs = self._register_thesis_and_policy_worksheet(currently_held=False)
        for action in ("Pass", "Wait"):
            result = validate_decision(build_valid_decision_payload(proposed_action=action, **refs), run_dir=self.run_dir)
            self.assertTrue(result["ok"], result["errors"])


# --- advisory order-mechanics guidance --------------------------------------


class OrderGuidanceShapeTest(unittest.TestCase):
    """Pydantic-level shape rules on `OrderGuidance` itself -- no worksheet or
    thesis involved."""

    def test_stop_order_without_trigger_price_is_invalid(self):
        for order_type in ("Stop-Limit", "Stop-Market"):
            with self.assertRaises(ValidationError):
                OrderGuidance(
                    order_type=order_type,
                    reference_price=PriceLevel(price=10.0, source=ORDER_GUIDANCE_SOURCE, rationale="r"),
                )

    def test_limit_order_without_limit_price_is_invalid(self):
        for order_type in ("Limit", "Stop-Limit"):
            with self.assertRaises(ValidationError):
                OrderGuidance(
                    order_type=order_type,
                    reference_price=PriceLevel(price=10.0, source=ORDER_GUIDANCE_SOURCE, rationale="r"),
                )

    def test_market_order_needs_only_a_reference_price(self):
        OrderGuidance(
            order_type="Market",
            reference_price=PriceLevel(price=10.0, source=ORDER_GUIDANCE_SOURCE, rationale="r"),
        )

    def test_stop_limit_order_with_both_prices_is_valid(self):
        level = PriceLevel(price=10.0, source=ORDER_GUIDANCE_SOURCE, rationale="r")
        OrderGuidance(order_type="Stop-Limit", reference_price=level, trigger_price=level, limit_price=level)


class OrderGuidanceValidationTest(RunDirTestCase):
    """End-to-end: `order_guidance` requiredness and the "no invented
    numbers" price-grounding guarantee."""

    THESIS_BODY = {
        "schema": "investment-thesis.v1",
        "valuation": {
            "methods": [
                {"method": "fcf_yield", "resulting_equity_value_per_share": {"low": 35.0, "mid": 40.0, "high": 45.0}},
            ],
        },
        "scenarios": {
            "bull": {"fair_value_per_share": 55.0},
            "base": {"fair_value_per_share": 45.0},
            "bear": {"fair_value_per_share": 30.0},
        },
    }

    def _register(self, *, policy_checks_result="pass", currently_held=True, thesis_body=None):
        thesis_ref = self._write_ref(
            "agent_outputs/TEST-thesis.json",
            json.dumps(thesis_body or self.THESIS_BODY).encode("utf-8"),
        )
        policy_worksheet = _policy_worksheet(single_name_result=policy_checks_result, currently_held=currently_held)
        policy_worksheet_ref = self._write_ref(
            "calculations/TEST-policy-worksheet.json",
            json.dumps(policy_worksheet).encode("utf-8"),
        )
        return {"thesis_ref": thesis_ref, "policy_worksheet_ref": policy_worksheet_ref}

    def test_order_guidance_required_for_buy_add_trim_sell(self):
        refs = self._register()
        for action in ("Buy", "Add", "Trim", "Sell"):
            result = validate_decision(build_valid_decision_payload(proposed_action=action, **refs), run_dir=self.run_dir)
            self.assertFalse(result["ok"], f"{action} should require order_guidance")
            self.assertTrue(any("requires order_guidance" in e for e in result["errors"]))

    def test_order_guidance_forbidden_for_non_trade_actions(self):
        owned_refs = self._register(currently_held=True)
        hold_result = validate_decision(
            build_valid_decision_payload(proposed_action="Hold", order_guidance=_order_guidance_payload(), **owned_refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(hold_result["ok"])
        self.assertTrue(any("must not carry order_guidance" in e for e in hold_result["errors"]))

        not_owned_refs = self._register(currently_held=False)
        for action in ("Watch", "Wait", "Pass"):
            result = validate_decision(
                build_valid_decision_payload(
                    proposed_action=action, order_guidance=_order_guidance_payload(), **not_owned_refs
                ),
                run_dir=self.run_dir,
            )
            self.assertFalse(result["ok"], f"{action} should forbid order_guidance")
            self.assertTrue(any("must not carry order_guidance" in e for e in result["errors"]))

    def test_order_guidance_price_matching_thesis_value_is_valid(self):
        refs = self._register()
        order_guidance = _order_guidance_payload(
            reference_price={
                "price": 30.0, "source": "thesis.scenarios.bear.fair_value_per_share", "rationale": "bear case",
            },
            limit_price={
                "price": 30.0, "source": "thesis.scenarios.bear.fair_value_per_share", "rationale": "bear case",
            },
        )
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=order_guidance, **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])

    def test_order_guidance_price_matching_valuation_method_bracket_citation(self):
        refs = self._register()
        source = "thesis.valuation.methods[fcf_yield].resulting_equity_value_per_share.low"
        order_guidance = _order_guidance_payload(
            reference_price={"price": 35.0, "source": source, "rationale": "low band"},
            limit_price={"price": 35.0, "source": source, "rationale": "low band"},
        )
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=order_guidance, **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])

    def test_order_guidance_price_not_matching_any_cited_source_is_invalid(self):
        refs = self._register()
        order_guidance = _order_guidance_payload(
            reference_price={"price": 999.0, "source": ORDER_GUIDANCE_SOURCE, "rationale": "fabricated"},
        )
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=order_guidance, **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("does not match" in e for e in result["errors"]))

    def test_order_guidance_source_path_that_does_not_resolve_is_invalid(self):
        refs = self._register()
        order_guidance = _order_guidance_payload(
            reference_price={
                "price": 40.0, "source": "policy_worksheet.price_and_market_context.does_not_exist",
                "rationale": "bad path",
            },
        )
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=order_guidance, **refs),
            run_dir=self.run_dir,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("could not be verified" in e for e in result["errors"]))

    def test_order_guidance_price_within_relative_tolerance_is_valid(self):
        refs = self._register()
        # tolerance = max(0.01, 40.0 * 0.001) = 0.04 -- 40.03 is inside it.
        order_guidance = _order_guidance_payload(
            reference_price={"price": 40.03, "source": ORDER_GUIDANCE_SOURCE, "rationale": "within tolerance"},
        )
        result = validate_decision(
            build_valid_decision_payload(proposed_action="Buy", order_guidance=order_guidance, **refs),
            run_dir=self.run_dir,
        )
        self.assertTrue(result["ok"], result["errors"])

    def test_find_execution_keys_does_not_flag_order_guidance_fields(self):
        """The guarantee this whole feature rests on: order_guidance's field
        names never trip the trade-execution scanner that validates every
        final/*.json artifact (`validation.find_execution_keys`)."""
        payload = build_valid_decision_payload(proposed_action="Buy", order_guidance=_order_guidance_payload())
        self.assertEqual(find_execution_keys(payload), [])


# --- CLI-facing entry points ------------------------------------------------


class CheckDecisionDraftTest(RunDirTestCase):
    def test_writes_nothing(self):
        refs = self._register_thesis_and_policy_worksheet()
        before = list(self.run_dir.rglob("*"))
        result = check_decision_draft(
            build_valid_decision_payload(order_guidance=_order_guidance_payload(), **refs), run_dir=self.run_dir
        )
        after = list(self.run_dir.rglob("*"))
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(before, after)
        self.assertNotIn("decision", result)


class SaveDecisionTest(RunDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        # save_decision registers evidence/appends audit events -- both
        # require the run's standard skeleton to exist, same as a real run.
        (self.run_dir / "evidence").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "audit_log.jsonl").touch()

    def test_valid_decision_is_written_to_final(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="pass")
        payload = build_valid_decision_payload(proposed_action="Add", order_guidance=_order_guidance_payload(), **refs)

        result = save_decision(payload, run_dir=self.run_dir, run_id=self.run_id, ticker="TEST")

        self.assertTrue(result["ok"], result["errors"])
        artifact_path = self.run_dir / result["artifact_path"]
        self.assertTrue(artifact_path.is_file())
        self.assertTrue(str(artifact_path.parent).endswith("final"))
        on_disk = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["policy_version"], "v1.1")
        self.assertFalse(on_disk["trade_executed"])
        self.assertTrue(on_disk["human_approval_required"])
        self.assertEqual(on_disk["order_guidance"]["order_type"], "Limit")

        records = list(evidence_module.read_records(self.run_dir))
        self.assertTrue(any(r["evidence_type"] == "decision_proposal" for r in records))

        audit_lines = (self.run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in audit_lines]
        self.assertIn("pm_drafted", events)
        self.assertIn("validated", events)

    def test_invalid_decision_writes_nothing_but_records_the_attempt(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="fail")
        payload = build_valid_decision_payload(proposed_action="Buy", **refs)

        result = save_decision(payload, run_dir=self.run_dir, run_id=self.run_id, ticker="TEST")

        self.assertFalse(result["ok"])
        self.assertFalse((self.run_dir / "final").exists())
        audit_lines = (self.run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in audit_lines]
        self.assertIn("validated", events)
        self.assertNotIn("pm_drafted", events)

    def test_ticker_mismatch_is_rejected(self):
        refs = self._register_thesis_and_policy_worksheet(policy_checks_result="pass")
        payload = build_valid_decision_payload(proposed_action="Add", order_guidance=_order_guidance_payload(), **refs)

        result = save_decision(payload, run_dir=self.run_dir, run_id=self.run_id, ticker="OTHER")

        self.assertFalse(result["ok"])
        self.assertTrue(any("does not match" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
