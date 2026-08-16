"""Tests for Phase 4: the three new `run` CLI stages
(`build-worksheet`/`check-thesis`/`save-thesis`) and their underlying
`src/workspace/thesis_validation.py` functions
(`check_worksheet_ref`/`scope_from_worksheet_ref`/`check_thesis_draft`/
`save_thesis`), plus `validation.py`'s new schema-aware branch for
`investment-thesis.v1` artifacts under `agent_outputs/`.

Offline, fixture-based, no network/DB -- mirrors
`tests/test_investment_worksheet.py` and
`tests/test_investment_thesis_validation.py`'s style exactly. Most tests call
the library functions directly against a bare temp directory used as
`run_dir` (the `RunDirCase`/`BuildWorksheetForRunTest` pattern already used by
those two files); `CliWiringTest` additionally drives a real run through
`cli.main()` to prove the argparse wiring itself.
"""

from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from workspace import audit as audit_module  # noqa: E402
from workspace import cli  # noqa: E402
from workspace import evidence as evidence_module  # noqa: E402
from workspace import investment_worksheet as iw  # noqa: E402
from workspace import run as run_module  # noqa: E402
from workspace import thesis_validation as tv  # noqa: E402
from workspace import validation as validation_module  # noqa: E402
from workspace.analysis_models import POLICY_VERSION, SECTION_IDS  # noqa: E402
from workspace.models import Request, RequestBody, Subject, SubjectIdentifiers  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "investment_analyst"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _placeholder_scenario(fair_value: float, probability: float) -> dict:
    return {
        "probability": probability, "horizon": "Long-term",
        "revenue_assumption": "n/a", "margin_assumption": "n/a", "dilution_assumption": "n/a",
        "valuation_method": "ev_revenue", "fair_value_per_share": fair_value,
        "expected_return_pct": 0.0, "conditions": [],
    }


def _section_entry(section_id: str, evidence_id: str) -> dict:
    label = None
    if section_id == "expectations_and_results":
        label = "current_snapshot_only"
    elif section_id == "options_and_positioning":
        label = "snapshot_only"
    return {
        "narrative": f"Synthetic narrative for {section_id}.",
        "evidence_ids": [evidence_id],
        "capability_label": label,
    }


def build_draft_from_worksheet(
    worksheet: dict, worksheet_path: str, worksheet_hash: str, *, run_id: str, ticker: str,
    thesis_confidence: str = "medium", evidence_completeness_pct: float = 90.0,
) -> dict:
    """A structurally-valid `investment-thesis.v1` draft built the way the
    `investment-analyst` agent is instructed to build one: `worksheet_ref`
    exactly as returned by `build-worksheet`, `section_states` derived from
    the worksheet's own scope buckets, and `valuation`/`scenarios` copied
    verbatim -- never restated with different numbers."""
    scope = worksheet["request_and_scope"]
    bundle_evidence_id = worksheet["source_evidence"]["bundle_evidence_id"]

    section_states: dict[str, str] = {}
    sections: dict[str, dict] = {}
    for section_id in SECTION_IDS:
        if section_id in scope["evaluate_sections"]:
            section_states[section_id] = "changed"
            sections[section_id] = _section_entry(section_id, bundle_evidence_id)
        elif section_id in scope["preserve_sections"]:
            section_states[section_id] = "not_evaluated"
        else:
            section_states[section_id] = "not_applicable"

    valuation_methods = worksheet["valuation_methods"]
    scenario_inputs = worksheet["scenario_inputs"] or {
        "bull": _placeholder_scenario(3.0, 0.25),
        "base": _placeholder_scenario(2.0, 0.50),
        "bear": _placeholder_scenario(1.0, 0.25),
    }

    return {
        "schema": "investment-thesis.v1",
        "run_id": run_id,
        "artifact_id": "th_placeholder",
        "generated_at": "2026-08-10T15:00:00Z",
        "security": {
            "ticker": ticker,
            "provider_symbol": worksheet["identity"]["provider_symbol"] or ticker,
            "asset_track": worksheet["identity"]["asset_track"],
        },
        "analysis_mode": scope["mode"],
        "policy_version": "placeholder",
        "worksheet_ref": {"path": worksheet_path, "hash": worksheet_hash},
        "prior_thesis": {
            "exists": False, "artifact_id": None, "version": None,
            "content_hash": None, "approved_at": None,
        },
        "section_states": section_states,
        "conclusion": {
            "fundamental_rating": "neutral",
            "valuation_stance": "reasonable",
            "thesis_direction": "initial",
            "thesis_confidence": thesis_confidence,
            "evidence_completeness_pct": evidence_completeness_pct,
            "investment_case": "Synthetic investment case.",
            "case_against": "Synthetic case against.",
            "most_important_catalyst": "Synthetic catalyst.",
            "most_important_risk": "Synthetic risk.",
            "most_important_unknown": "Synthetic unknown.",
            "analysis_horizon": scope["decision_horizon"],
            "as_of": "2026-08-10T15:00:00Z",
        },
        "key_claims": [
            {
                "claim_id": "thesis_1", "statement": "Synthetic critical claim.",
                "claim_type": "fact", "importance": "critical",
                "evidence_ids": [bundle_evidence_id], "status_vs_prior": "new", "confidence": "medium",
            },
        ],
        "sections": sections,
        "valuation": {
            "methods": valuation_methods,
            "interpretation": (
                "Placeholder sensitivity band around today's market-implied multiple, "
                "not an independent DCF or peer-comparison opinion."
            ),
        },
        "scenarios": scenario_inputs,
        "conditions": {
            "upgrade_conditions": [], "downgrade_conditions": [],
            "invalidation_conditions": [], "monitoring_items": [],
        },
        "known_conflicts": [],
        "unknowns": list(worksheet["unknowns"]),
        "evidence_ids_used": [bundle_evidence_id],
        "challenger": {"required": False, "completed": False, "material_objections": []},
        "validation": {"status": "pending", "errors": [], "warnings": []},
        "human_review_required": True,
    }


class RunDirCase(unittest.TestCase):
    """Bare temp directory as `run_dir` -- no `create_from_request`, no
    `config` patching -- mirrors `BuildWorksheetForRunTest` in
    `test_investment_worksheet.py`."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.run_dir = Path(self.temp_dir.name)
        self.run_id = "2026-08-10T140000Z-test"
        for subdir in ("evidence", "calculations", "agent_outputs", "tmp"):
            (self.run_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _register_bundle(self, bundle: dict | None = None):
        artifact = self.run_dir / "evidence" / "PLTR-2026-08-10-resources.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(bundle or _load("pltr_bundle.json")), encoding="utf-8")
        return evidence_module.register(
            self.run_dir, run_id=self.run_id, evidence_type="market_data_bundle",
            source_name="investment-analyst-resources", status="partial", artifact=artifact,
        )

    def _build_worksheet(self, *, mode: str = "initial_research", bundle: dict | None = None):
        self._register_bundle(bundle)
        scope = iw.build_analysis_scope(
            run_id=self.run_id, subject="PLTR", asset_track="equity", mode=mode,
            trigger={"type": "user_request", "occurred_at": None, "detail": None},
            decision_horizon="Long-term",
        )
        worksheet, worksheet_path, worksheet_hash = iw.build_worksheet_for_run(
            self.run_dir, run_id=self.run_id, ticker="PLTR", scope=scope,
        )
        return worksheet, worksheet_path.relative_to(self.run_dir).as_posix(), worksheet_hash

    def _draft(self, **kwargs) -> dict:
        worksheet, worksheet_rel_path, worksheet_hash = self._build_worksheet(
            mode=kwargs.pop("mode", "initial_research"), bundle=kwargs.pop("bundle", None)
        )
        return worksheet, build_draft_from_worksheet(
            worksheet, worksheet_rel_path, worksheet_hash, run_id=self.run_id, ticker="PLTR", **kwargs
        )


# --- 1. happy path ----------------------------------------------------------


class HappyPathTest(RunDirCase):
    def test_check_thesis_accepts_a_worksheet_faithful_draft(self):
        _, draft = self._draft()
        result = tv.check_thesis_draft(draft, run_dir=self.run_dir)
        self.assertTrue(result["ok"], result["errors"])
        self.assertNotIn("thesis", result)

    def test_save_thesis_writes_registers_and_overwrites_authoritative_fields(self):
        _, draft = self._draft()
        result = tv.save_thesis(draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertTrue(result["ok"], result["errors"])

        artifact_path = self.run_dir / result["artifact_path"]
        self.assertTrue(artifact_path.is_file())
        saved = json.loads(artifact_path.read_text(encoding="utf-8"))

        # Authoritatively overwritten, not the draft's placeholders.
        self.assertEqual(saved["policy_version"], POLICY_VERSION)
        self.assertNotEqual(saved["artifact_id"], "th_placeholder")
        self.assertEqual(saved["artifact_id"], result["artifact_id"])
        self.assertEqual(saved["validation"], {"status": result["status"], "errors": [], "warnings": result["warnings"]})

        records = evidence_module.read_records(self.run_dir)
        thesis_records = [r for r in records if r["evidence_type"] == "investment_thesis"]
        self.assertEqual(len(thesis_records), 1)
        self.assertEqual(thesis_records[0]["evidence_id"], result["evidence_id"])
        self.assertEqual(thesis_records[0]["source_name"], "investment_analyst")
        self.assertEqual(thesis_records[0]["collection_method"], "llm_judgment")

        events = audit_module.read_events(self.run_dir)
        names = [event["event"] for event in events]
        self.assertIn("analyst_drafted", names)
        self.assertIn("validated", names)
        validated_events = [event for event in events if event["event"] == "validated"]
        self.assertEqual(validated_events[-1]["status"], "success")


# --- 2. rejection surfaces via check-thesis, not silently -------------------


class RejectionTest(RunDirCase):
    def test_fabricated_evidence_id_is_reported_by_name(self):
        _, draft = self._draft()
        # Augment, don't replace -- the real citation must stay declared or
        # this fails pydantic's own internal-consistency check before it ever
        # reaches the run-dir-aware check this test targets.
        draft["key_claims"][0]["evidence_ids"].append("ev_does_not_exist")
        draft["evidence_ids_used"].append("ev_does_not_exist")
        result = tv.check_thesis_draft(draft, run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(any("unregistered evidence_id: ev_does_not_exist" in e for e in result["errors"]))

    def test_worksheet_ref_hash_mismatch_is_reported(self):
        _, draft = self._draft()
        draft["worksheet_ref"]["hash"] = "sha256:" + "0" * 64
        result = tv.check_thesis_draft(draft, run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(any("worksheet_ref.hash" in e for e in result["errors"]))

    def test_save_thesis_writes_nothing_on_a_bad_draft(self):
        _, draft = self._draft()
        draft["key_claims"][0]["evidence_ids"] = ["ev_ghost"]
        draft["evidence_ids_used"] = ["ev_ghost"]
        result = tv.save_thesis(draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertFalse(result["ok"])
        self.assertEqual(list((self.run_dir / "agent_outputs").iterdir()), [])
        events = audit_module.read_events(self.run_dir)
        self.assertFalse(any(event["event"] == "analyst_drafted" for event in events))
        validated_events = [event for event in events if event["event"] == "validated"]
        self.assertEqual(len(validated_events), 1)
        self.assertEqual(validated_events[0]["status"], "failure")

    def test_ticker_mismatch_is_rejected(self):
        _, draft = self._draft()
        result = tv.save_thesis(draft, run_dir=self.run_dir, run_id=self.run_id, ticker="SOMETHING_ELSE")
        self.assertFalse(result["ok"])
        self.assertTrue(any("does not match --ticker" in e for e in result["errors"]))


# --- 3. blocking scenario: a required domain fully failed TRACE -------------


class BlockingScenarioTest(RunDirCase):
    def _mutated_bundle(self) -> dict:
        bundle = copy.deepcopy(_load("pltr_bundle.json"))
        bundle["trace"]["domains"]["financials"] = {
            "ok": [], "missing": ["latest_period_end"], "not_applicable": [],
        }
        return bundle

    def test_worksheet_reports_blocking_under_initial_research_stop_policy(self):
        worksheet, _, _ = self._build_worksheet(mode="initial_research", bundle=self._mutated_bundle())
        self.assertTrue(worksheet["evidence_health"]["blocking"])
        self.assertTrue(
            any("financials" in reason for reason in worksheet["evidence_health"]["blocking_reasons"])
        )

    def test_a_missing_required_domain_caps_confidence_to_low_regardless_of_stated_completeness(self):
        worksheet, worksheet_rel_path, worksheet_hash = self._build_worksheet(
            mode="initial_research", bundle=self._mutated_bundle()
        )
        high_confidence_draft = build_draft_from_worksheet(
            worksheet, worksheet_rel_path, worksheet_hash, run_id=self.run_id, ticker="PLTR",
            thesis_confidence="high", evidence_completeness_pct=95.0,
        )
        result = tv.save_thesis(high_confidence_draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertFalse(result["ok"])
        self.assertTrue(any("exceeds the policy cap" in e for e in result["errors"]))
        self.assertEqual(list((self.run_dir / "agent_outputs").iterdir()), [])

    def test_a_low_confidence_draft_still_passes_despite_the_missing_domain(self):
        worksheet, worksheet_rel_path, worksheet_hash = self._build_worksheet(
            mode="initial_research", bundle=self._mutated_bundle()
        )
        low_confidence_draft = build_draft_from_worksheet(
            worksheet, worksheet_rel_path, worksheet_hash, run_id=self.run_id, ticker="PLTR",
            thesis_confidence="low", evidence_completeness_pct=95.0,
        )
        result = tv.save_thesis(low_confidence_draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertTrue(result["ok"], result["errors"])


# --- 4. warning-threshold scenario: no domain missing, but completeness low -


class WarningThresholdScenarioTest(RunDirCase):
    def test_high_confidence_in_the_warning_band_is_rejected(self):
        _, draft = self._draft(thesis_confidence="high", evidence_completeness_pct=65.0)
        result = tv.check_thesis_draft(draft, run_dir=self.run_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(any("exceeds the policy cap" in e for e in result["errors"]))

    def test_medium_confidence_at_the_cap_passes(self):
        _, draft = self._draft(thesis_confidence="medium", evidence_completeness_pct=65.0)
        result = tv.check_thesis_draft(draft, run_dir=self.run_dir)
        self.assertTrue(result["ok"], result["errors"])


# --- 5. placeholder-labeling regression guard --------------------------------


class PlaceholderLabelingTest(RunDirCase):
    def test_valuation_and_scenarios_are_copied_verbatim_never_restated(self):
        worksheet, draft = self._draft()
        self.assertEqual(draft["valuation"]["methods"], worksheet["valuation_methods"])
        self.assertEqual(draft["scenarios"], worksheet["scenario_inputs"])
        # The prose-labeling requirement itself -- that valuation.interpretation
        # states plainly these are placeholder sensitivity bands, not
        # independent research -- is a human/agent guardrail
        # (.claude/agents/investment-analyst.md), not a pydantic rule; there
        # is no schema field for "is this labeled a placeholder". This test
        # only proves the numbers themselves are never restated.


# --- 6. `run validate` picks up the thesis artifact --------------------------


class RunValidateIntegrationTest(RunDirCase):
    def test_run_validate_passes_after_a_successful_save(self):
        _, draft = self._draft()
        result = tv.save_thesis(draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertTrue(result["ok"], result["errors"])
        run_result = validation_module.validate_run(self.run_dir)
        thesis_errors = [e for e in run_result["errors"] if "agent_outputs" in e]
        self.assertEqual(thesis_errors, [])

    def test_run_validate_catches_a_post_hoc_corrupted_worksheet_ref(self):
        _, draft = self._draft()
        result = tv.save_thesis(draft, run_dir=self.run_dir, run_id=self.run_id, ticker="PLTR")
        self.assertTrue(result["ok"], result["errors"])

        worksheet_path = self.run_dir / draft["worksheet_ref"]["path"]
        worksheet_path.write_bytes(worksheet_path.read_bytes() + b"\ncorrupted\n")

        run_result = validation_module.validate_run(self.run_dir)
        self.assertTrue(any("worksheet_ref.hash" in e for e in run_result["errors"]), run_result["errors"])


# --- 7. CLI wiring: build-worksheet -> check-thesis -> save-thesis via cli.main() -


class CliWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.runs_root = base / "runs"
        self.archive_root = base / "archive"
        self.runs_root.mkdir()
        self.archive_root.mkdir()
        patcher = patch.multiple(
            config, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=self.runs_root,
            WORKSPACE_ARCHIVE_FOLDER=self.archive_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        request = Request(
            mode="initial_research",
            subject=Subject(type="security", identifiers=SubjectIdentifiers(ticker="PLTR")),
            request=RequestBody(question="Is PLTR fundamentally attractive?"),
        )
        self.run_id, self.run_dir = run_module.create_from_request(request)

        artifact = self.run_dir / "evidence" / "PLTR-2026-08-10-resources.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text((FIXTURES / "pltr_bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
        evidence_module.register(
            self.run_dir, run_id=self.run_id, evidence_type="market_data_bundle",
            source_name="investment-analyst-resources", status="partial", artifact=artifact,
        )

    def _run_cli(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(argv)
        return code, json.loads(buf.getvalue())

    def test_full_sequence_via_cli_main(self):
        code, build_result = self._run_cli([
            "build-worksheet", "--run-id", self.run_id, "--ticker", "PLTR",
            "--mode", "initial_research", "--horizon", "Long-term",
        ])
        self.assertEqual(code, 0, build_result)
        self.assertFalse(build_result["evidence_health"]["blocking"])

        worksheet = json.loads((self.run_dir / build_result["worksheet_path"]).read_text(encoding="utf-8"))
        draft = build_draft_from_worksheet(
            worksheet, build_result["worksheet_path"], build_result["worksheet_hash"],
            run_id=self.run_id, ticker="PLTR",
        )
        draft_path = self.run_dir / "tmp" / "PLTR-thesis-draft.json"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")

        code, check_result = self._run_cli([
            "check-thesis", "--run-id", self.run_id, "--path", "tmp/PLTR-thesis-draft.json",
        ])
        self.assertEqual(code, 0, check_result)
        self.assertTrue(check_result["ok"])

        code, save_result = self._run_cli([
            "save-thesis", "--run-id", self.run_id, "--path", "tmp/PLTR-thesis-draft.json", "--ticker", "PLTR",
        ])
        self.assertEqual(code, 0, save_result)
        self.assertTrue((self.run_dir / save_result["artifact_path"]).is_file())

        run_result = validation_module.validate_run(self.run_dir)
        thesis_errors = [e for e in run_result["errors"] if "agent_outputs" in e]
        self.assertEqual(thesis_errors, [])

    def test_check_thesis_exits_1_on_a_bad_draft(self):
        self._run_cli([
            "build-worksheet", "--run-id", self.run_id, "--ticker", "PLTR",
            "--mode", "initial_research", "--horizon", "Long-term",
        ])
        draft_path = self.run_dir / "tmp" / "bad-draft.json"
        draft_path.write_text(json.dumps({"schema": "investment-thesis.v1"}), encoding="utf-8")
        code, result = self._run_cli([
            "check-thesis", "--run-id", self.run_id, "--path", "tmp/bad-draft.json",
        ])
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
