"""Tests for the market-analyst-resources skill.

Builds a real temp `market.duckdb` fixture via `macro_store.connect_read_write`
so `macro_store`/`market_freshness_gate`/`market_analyst_resources` are
exercised against actual schema and constraints, not mocks. No live network
calls anywhere -- `sources.fetch` is always monkeypatched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "market-analyst-resources" / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))

import macro_store  # noqa: E402
import market_analyst_resources as mar  # noqa: E402
import market_freshness_gate as gate_module  # noqa: E402
import sources  # noqa: E402
import transforms  # noqa: E402
from registry import Derived, EventEntry, Indicator, Registry, RegistryError, load_registry  # noqa: E402

TODAY = date(2026, 8, 6)  # a Thursday


def make_indicator(**overrides) -> Indicator:
    defaults = dict(
        id="TEST_IND", output_id="TEST_IND", domain="rates", kind="statistical",
        source="fake_source", series_ref="FAKE", transform="none", cadence="monthly",
        publication_lag_days=5, backfill_years=2, units="percent",
    )
    defaults.update(overrides)
    return Indicator(**defaults)


def make_registry(indicators=(), derived=(), events=(), *, updated=TODAY) -> Registry:
    return Registry(
        version=1, updated=updated, sources={"fake_source": {"adapter": "fake"}},
        indicators=tuple(indicators), derived=tuple(derived), events=tuple(events),
        _by_id={indicator.id: indicator for indicator in indicators},
    )


class FixtureDatabaseTest(unittest.TestCase):
    """Base class: builds one temp market.duckdb per test."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "market.duckdb"
        self.connection = macro_store.connect_read_write(self.db_path)
        self.addCleanup(self.connection.close)

    def _reopen_read_only(self):
        self.connection.close()
        return macro_store.connect_read_only(self.db_path)


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------

class RegistryValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "registry.yml"

    def _write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_minimal_valid_registry_loads(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - id: BOC_RATE
    domain: policy
    kind: statistical
    source: boc_valet
    series_ref: V39079
    transform: none
    cadence: daily
    publication_lag_days: 0
    backfill_years: 2
    units: percent
"""
        )
        registry = load_registry(self.path)
        self.assertEqual(len(registry.indicators), 1)
        self.assertTrue(registry.indicator("BOC_RATE").is_configured)

    def test_duplicate_id_rejected(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: DUP, domain: policy, kind: statistical, source: boc_valet, series_ref: A, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
  - {id: DUP, domain: policy, kind: statistical, source: boc_valet, series_ref: B, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_derived_referencing_unknown_input_rejected(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: A, domain: rates, kind: statistical, source: boc_valet, series_ref: A, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
derived:
  - {id: SPREAD_A_B, domain: rates, op: spread, inputs: [A, B], align: locf, max_carry_days: 5, units: percent}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_derived_cannot_reference_another_derived(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: A, domain: rates, kind: statistical, source: boc_valet, series_ref: A, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
  - {id: B, domain: rates, kind: statistical, source: boc_valet, series_ref: B, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
derived:
  - {id: SPREAD_AB, domain: rates, op: spread, inputs: [A, B], align: locf, max_carry_days: 5, units: percent}
  - {id: SPREAD_OF_SPREAD, domain: rates, op: spread, inputs: [SPREAD_AB, A], align: locf, max_carry_days: 5, units: percent}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_priced_indicator_requires_value_field(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  yfinance: {adapter: yfinance}
indicators:
  - {id: VIX, domain: volatility, kind: priced, source: yfinance, series_ref: "^VIX", transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: index}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_unknown_transform_rejected(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: A, domain: rates, kind: statistical, source: boc_valet, series_ref: A, transform: nonsense, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_unconfigured_tbd_indicator_skips_source_membership_check(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: US_CPI, domain: inflation, kind: statistical, source: "<TBD>", series_ref: "<TBD>", transform: none, cadence: monthly, publication_lag_days: 14, backfill_years: 5, units: percent}
"""
        )
        registry = load_registry(self.path)
        self.assertFalse(registry.indicator("US_CPI").is_configured)

    def test_undeclared_source_rejected_for_configured_indicator(self):
        self._write(
            """
version: 1
updated: 2026-08-06
sources:
  boc_valet: {adapter: boc_valet}
indicators:
  - {id: A, domain: rates, kind: statistical, source: fred, series_ref: A, transform: none, cadence: daily, publication_lag_days: 0, backfill_years: 1, units: percent}
"""
        )
        with self.assertRaises(RegistryError):
            load_registry(self.path)

    def test_real_shipped_registry_loads(self):
        real_path = (
            Path(__file__).resolve().parents[1] / "Knowledge-Base" / "taxonomy" / "market-indicators.yml"
        )
        registry = load_registry(real_path)
        self.assertGreater(len(registry.configured_indicators()), 0)
        leaf_ids = {indicator.id for indicator in registry.indicators}
        for entry in registry.derived:
            for input_id in entry.inputs:
                self.assertIn(input_id, leaf_ids)


# ---------------------------------------------------------------------------
# Transform math
# ---------------------------------------------------------------------------

class UnaryTransformTest(unittest.TestCase):
    def _series(self, values: list[float], *, start: date = date(2020, 1, 31), step_days: int = 30):
        return [(start + timedelta(days=step_days * i), v) for i, v in enumerate(values)]

    def test_none_returns_latest(self):
        result = transforms.apply_unary_transform("none", None, self._series([1.0, 2.0, 3.0]), cadence="monthly")
        self.assertEqual(result.value, 3.0)
        self.assertEqual(result.status, "ok")

    def test_yoy_pct_monthly_steps_back_12_observations(self):
        values = [100.0] * 12 + [110.0]
        result = transforms.apply_unary_transform("yoy_pct", None, self._series(values), cadence="monthly")
        self.assertAlmostEqual(result.value, 10.0, places=6)

    def test_yoy_pct_insufficient_history(self):
        result = transforms.apply_unary_transform("yoy_pct", None, self._series([100.0, 101.0]), cadence="monthly")
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "insufficient_history")

    def test_mom_pct_steps_back_one_observation(self):
        result = transforms.apply_unary_transform("mom_pct", None, self._series([100.0, 105.0]), cadence="monthly")
        self.assertAlmostEqual(result.value, 5.0, places=6)

    def test_annualized_3m_compounds_and_annualizes(self):
        # +1% over ~3 months (3 monthly observations back) should annualize above 1%.
        values = [100.0, 100.0, 100.0, 101.0]
        result = transforms.apply_unary_transform("annualized_3m", None, self._series(values), cadence="monthly")
        self.assertGreater(result.value, 1.0)

    def test_pct_change_windowed(self):
        values = [100.0] * 63 + [110.0]
        result = transforms.apply_unary_transform("pct_change", 63, self._series(values), cadence="daily")
        self.assertAlmostEqual(result.value, 10.0, places=6)

    def test_pct_change_requires_window(self):
        with self.assertRaises(ValueError):
            transforms.apply_unary_transform("pct_change", None, self._series([1.0, 2.0]), cadence="daily")

    def test_zscore_of_constant_series_is_zero(self):
        result = transforms.apply_unary_transform("zscore", 10, self._series([5.0] * 12), cadence="daily")
        self.assertEqual(result.value, 0.0)

    def test_zscore_positive_when_above_mean(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
        result = transforms.apply_unary_transform("zscore", 6, self._series(values), cadence="daily")
        self.assertGreater(result.value, 0.0)

    def test_percentile_rank_of_max_is_100(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = transforms.apply_unary_transform("percentile_rank", 5, self._series(values), cadence="daily")
        self.assertEqual(result.value, 100.0)

    def test_null_observations_are_dropped_before_indexing(self):
        series = self._series([100.0, 100.0])
        series.insert(1, (date(2020, 2, 15), None))  # a suppressed observation in between
        result = transforms.apply_unary_transform("mom_pct", None, series, cadence="monthly")
        self.assertAlmostEqual(result.value, 0.0, places=6)

    def test_empty_history_is_insufficient(self):
        result = transforms.apply_unary_transform("none", None, [], cadence="daily")
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "insufficient_history")

    def test_parse_transform(self):
        self.assertEqual(transforms.parse_transform("none"), ("none", None))
        self.assertEqual(transforms.parse_transform("zscore(252)"), ("zscore", 252))


class DerivedOpTest(unittest.TestCase):
    def test_spread_on_matching_dates(self):
        series_a = [(date(2026, 1, 1), 4.5), (date(2026, 1, 2), 4.6)]
        series_b = [(date(2026, 1, 1), 2.0), (date(2026, 1, 2), 2.1)]
        result = transforms.compute_derived("spread", series_a, series_b, max_carry_days=5)
        self.assertAlmostEqual(result.value, 2.5, places=6)
        self.assertEqual(result.status, "ok")

    def test_ratio_computation(self):
        series_a = [(date(2026, 1, 1), 10.0)]
        series_b = [(date(2026, 1, 1), 4.0)]
        result = transforms.compute_derived("ratio", series_a, series_b, max_carry_days=5)
        self.assertAlmostEqual(result.value, 2.5, places=6)

    def test_ratio_divide_by_zero_is_input_missing(self):
        series_a = [(date(2026, 1, 1), 10.0)]
        series_b = [(date(2026, 1, 1), 0.0)]
        result = transforms.compute_derived("ratio", series_a, series_b, max_carry_days=5)
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "input_missing")

    def test_locf_carries_forward_within_max_carry_days(self):
        series_a = [(date(2026, 1, 10), 5.0)]
        series_b = [(date(2026, 1, 8), 2.0)]  # 2 days stale
        result = transforms.compute_derived("spread", series_a, series_b, max_carry_days=5)
        self.assertEqual(result.status, "stale_leg")
        self.assertAlmostEqual(result.value, 3.0, places=6)

    def test_leg_beyond_max_carry_days_is_input_missing(self):
        series_a = [(date(2026, 1, 10), 5.0)]
        series_b = [(date(2025, 1, 1), 2.0)]  # far too stale
        result = transforms.compute_derived("spread", series_a, series_b, max_carry_days=5)
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "input_missing")

    def test_empty_anchor_series_is_input_missing(self):
        result = transforms.compute_derived("spread", [], [(date(2026, 1, 1), 1.0)], max_carry_days=5)
        self.assertEqual(result.status, "input_missing")


# ---------------------------------------------------------------------------
# macro_store: insert-on-change, revisions, point-in-time reads
# ---------------------------------------------------------------------------

class MacroStoreTest(FixtureDatabaseTest):
    def test_new_observation_inserts_one_row(self):
        now = datetime(2026, 8, 1, 12, 0, 0)
        outcome = macro_store.upsert_observation(
            self.connection, series_id="X", obs_date=date(2026, 7, 1), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        self.assertEqual(outcome["action"], "new")
        rows = self.connection.execute("SELECT COUNT(*) FROM macro_observations").fetchone()
        self.assertEqual(rows[0], 1)

    def test_unchanged_refetch_confirms_without_new_row(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 2, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="X", obs_date=date(2026, 7, 1), value=1.0,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        outcome = macro_store.upsert_observation(
            self.connection, series_id="X", obs_date=date(2026, 7, 1), value=1.0,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        self.assertEqual(outcome["action"], "confirmed")
        count = self.connection.execute("SELECT COUNT(*) FROM macro_observations").fetchone()[0]
        self.assertEqual(count, 1)
        confirmed_at = self.connection.execute(
            "SELECT last_confirmed_at FROM macro_observations WHERE series_id = 'X'"
        ).fetchone()[0]
        self.assertEqual(confirmed_at, now2)

    def test_revision_inserts_new_row_and_current_view_shows_latest(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 2, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.5,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        outcome = macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.8,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        self.assertEqual(outcome["action"], "revised")
        self.assertEqual(outcome["prior_value"], 1.5)
        count = self.connection.execute("SELECT COUNT(*) FROM macro_observations").fetchone()[0]
        self.assertEqual(count, 2)
        current = macro_store.get_latest(self.connection, "GDP")
        self.assertEqual(current["value"], 1.8)

    def test_get_revisions_since_finds_the_change(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 2, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.5,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.8,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        revisions = macro_store.get_revisions_since(self.connection, datetime(2026, 8, 1, 18, 0, 0))
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["prior_value"], 1.5)
        self.assertEqual(revisions[0]["new_value"], 1.8)

    def test_get_revisions_since_excludes_changes_before_the_cutoff(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 2, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.5,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.8,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        revisions = macro_store.get_revisions_since(self.connection, datetime(2026, 8, 3, 0, 0, 0))
        self.assertEqual(revisions, [])

    def test_get_new_observation_count_since(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 2, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="X", obs_date=date(2026, 6, 1), value=1.0,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id="X", obs_date=date(2026, 7, 1), value=2.0,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        count = macro_store.get_new_observation_count_since(self.connection, "X", now1)
        self.assertEqual(count, 1)

    def test_history_as_of_reconstructs_pre_revision_state(self):
        now1 = datetime(2026, 8, 1, 12, 0, 0)
        now2 = datetime(2026, 8, 3, 12, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.5,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id="GDP", obs_date=date(2026, 6, 30), value=1.8,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        as_of_before_revision = macro_store.get_history_as_of(self.connection, "GDP", datetime(2026, 8, 2, 0, 0, 0))
        self.assertEqual(as_of_before_revision[0]["value"], 1.5)
        as_of_after_revision = macro_store.get_history_as_of(self.connection, "GDP", datetime(2026, 8, 4, 0, 0, 0))
        self.assertEqual(as_of_after_revision[0]["value"], 1.8)

    def test_coverage_start_is_earliest_obs_date(self):
        now = datetime(2026, 8, 1, 12, 0, 0)
        for offset in (10, 5, 20):
            macro_store.upsert_observation(
                self.connection, series_id="X", obs_date=date(2026, 1, 1) + timedelta(days=offset), value=1.0,
                status="ok", source_id="fake", units="percent", now=now,
            )
        self.assertEqual(macro_store.get_coverage_start(self.connection, "X"), date(2026, 1, 6))

    def test_observed_cadence_days_is_median_gap(self):
        now = datetime(2026, 8, 1, 12, 0, 0)
        for i, gap in enumerate([30, 30, 30, 30]):
            macro_store.upsert_observation(
                self.connection, series_id="X", obs_date=date(2026, 1, 1) + timedelta(days=gap * i), value=float(i),
                status="ok", source_id="fake", units="percent", now=now,
            )
        cadence = macro_store.get_observed_cadence_days(self.connection, "X")
        self.assertEqual(cadence, 30.0)

    def test_record_and_get_last_successful_run(self):
        self.assertIsNone(macro_store.get_last_successful_run(self.connection))
        macro_store.record_run(
            self.connection, run_date=TODAY, registry_version=1, status="ok",
            bundle_path="foo.json", indicator_count=5, created_at=datetime(2026, 8, 1, 9, 0, 0),
        )
        last_run = macro_store.get_last_successful_run(self.connection)
        self.assertEqual(last_run["registry_version"], 1)
        self.assertEqual(last_run["indicator_count"], 5)

    def test_market_store_not_ready_when_file_missing(self):
        missing_path = Path(self.temp_dir.name) / "does-not-exist.duckdb"
        with self.assertRaises(macro_store.MarketStoreNotReady):
            macro_store.connect_read_only(missing_path)


# ---------------------------------------------------------------------------
# Freshness gate
# ---------------------------------------------------------------------------

class FreshnessGateTest(FixtureDatabaseTest):
    def test_unconfigured_indicator_is_never_due(self):
        indicator = make_indicator(source="<TBD>", series_ref="<TBD>")
        registry = make_registry([indicator])
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertFalse(result["due"])
        self.assertEqual(result["due_reason"], "not_configured")

    def test_no_database_reports_backfill_due(self):
        indicator = make_indicator()
        registry = make_registry([indicator])
        result = gate_module.compute_gate(registry, None, today=TODAY)[0]
        self.assertTrue(result["due"])
        self.assertEqual(result["due_reason"], "backfill")

    def test_missing_coverage_is_backfill_due(self):
        indicator = make_indicator(backfill_years=2)
        registry = make_registry([indicator], updated=TODAY)
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertEqual(result["due_reason"], "backfill")

    def test_sufficient_coverage_is_not_backfill_due(self):
        indicator = make_indicator(backfill_years=1, cadence="monthly")
        registry = make_registry([indicator], updated=TODAY)
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=364), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=5), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertNotEqual(result["due_reason"], "backfill")

    def test_backfill_target_anchors_on_registry_updated_not_today(self):
        # Registry authored a year ago with a 2-year backfill target; today
        # moving forward should not perpetually re-demand backfill once the
        # original target was already reached.
        registered_date = TODAY - timedelta(days=365)
        indicator = make_indicator(backfill_years=2, cadence="monthly")
        registry = make_registry([indicator], updated=registered_date)
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=registered_date - timedelta(days=365 * 2), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=10), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertNotEqual(result["due_reason"], "backfill")

    def test_scheduled_due_when_next_expected_has_passed(self):
        indicator = make_indicator(cadence="monthly", publication_lag_days=5, backfill_years=1)
        registry = make_registry([indicator])
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=400), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=40), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertTrue(result["due"])
        self.assertEqual(result["due_reason"], "scheduled")

    def test_not_due_before_next_expected(self):
        indicator = make_indicator(cadence="monthly", publication_lag_days=5, backfill_years=1)
        registry = make_registry([indicator])
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=400), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=1), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertFalse(result["due"])

    def test_priced_daily_uses_business_day_boundary(self):
        indicator = make_indicator(kind="priced", cadence="daily", value_field="close", backfill_years=1)
        registry = make_registry([indicator])
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=400), value=1.0,
            status="ok", source_id="fake", units="usd", now=now,
        )
        # Last observation is "yesterday" -- within the 2-business-day grace.
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=1), value=1.1,
            status="ok", source_id="fake", units="usd", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertFalse(result["due"])

    def test_event_forces_due_the_day_after(self):
        indicator = make_indicator(domain="policy", cadence="monthly", publication_lag_days=5, backfill_years=1)
        event = EventEntry(date=TODAY - timedelta(days=1), label="BoC rate decision", forces_due=("policy",))
        registry = make_registry([indicator], events=[event])
        now = datetime.combine(TODAY, datetime.min.time())
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=400), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=1), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertTrue(result["due"])
        self.assertIn("BoC rate decision", result["forced_by_events"])

    def test_cadence_mismatch_flagged_when_observed_gap_far_exceeds_declared(self):
        indicator = make_indicator(cadence="monthly", backfill_years=1)
        registry = make_registry([indicator])
        now = datetime.combine(TODAY, datetime.min.time())
        # Observations 200 days apart despite a declared monthly cadence.
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=400), value=1.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=TODAY - timedelta(days=200), value=1.1,
            status="ok", source_id="fake", units="percent", now=now,
        )
        result = gate_module.compute_gate(registry, self.connection, today=TODAY)[0]
        self.assertTrue(result["cadence_mismatch"])


class RefreshDueIndicatorsTest(FixtureDatabaseTest):
    def test_refresh_persists_fetched_points(self):
        indicator = make_indicator(cadence="monthly", backfill_years=1)
        registry = make_registry([indicator], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        with patch.object(sources, "fetch", return_value=[(TODAY - timedelta(days=1), 4.25)]):
            results = gate_module.refresh_due_indicators(registry, self.connection, gate_results, today=TODAY)
        self.assertEqual(results[0]["status"], "ok")
        latest = macro_store.get_latest(self.connection, indicator.id)
        self.assertEqual(latest["value"], 4.25)

    def test_one_indicator_failure_does_not_roll_back_others(self):
        good = make_indicator(id="GOOD", series_ref="GOOD_REF", backfill_years=1)
        bad = make_indicator(id="BAD", series_ref="BAD_REF", backfill_years=1)
        registry = make_registry([good, bad], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)

        def fake_fetch(adapter, series_ref, start, end):
            if series_ref == bad.series_ref:
                raise sources.SourceConfigError("bad series_ref")
            return [(TODAY - timedelta(days=1), 1.0)]

        with patch.object(sources, "fetch", side_effect=fake_fetch):
            results = gate_module.refresh_due_indicators(registry, self.connection, gate_results, today=TODAY)

        by_id = {r["id"]: r for r in results}
        self.assertEqual(by_id["GOOD"]["status"], "ok")
        self.assertEqual(by_id["BAD"]["status"], "config_error")
        self.assertIsNotNone(macro_store.get_latest(self.connection, "GOOD"))
        self.assertIsNone(macro_store.get_latest(self.connection, "BAD"))

    def test_transient_error_is_reported_not_swallowed(self):
        indicator = make_indicator(backfill_years=1)
        registry = make_registry([indicator], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        with patch.object(sources, "fetch", side_effect=sources.SourceTransientError("timeout")):
            results = gate_module.refresh_due_indicators(registry, self.connection, gate_results, today=TODAY)
        self.assertEqual(results[0]["status"], "fetch_failed")

    def test_only_domains_filters_which_indicators_refresh(self):
        rates_ind = make_indicator(id="RATES_IND", domain="rates", backfill_years=1)
        credit_ind = make_indicator(id="CREDIT_IND", domain="credit", backfill_years=1)
        registry = make_registry([rates_ind, credit_ind], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        with patch.object(sources, "fetch", return_value=[(TODAY - timedelta(days=1), 1.0)]):
            results = gate_module.refresh_due_indicators(
                registry, self.connection, gate_results, today=TODAY, only_domains={"rates"}
            )
        self.assertEqual([r["id"] for r in results], ["RATES_IND"])

    def test_max_indicators_caps_batch_size(self):
        indicators = [make_indicator(id=f"IND_{i}", backfill_years=1) for i in range(5)]
        registry = make_registry(indicators, updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        with patch.object(sources, "fetch", return_value=[(TODAY - timedelta(days=1), 1.0)]):
            results = gate_module.refresh_due_indicators(
                registry, self.connection, gate_results, today=TODAY, max_indicators=2
            )
        self.assertEqual(len(results), 2)


# ---------------------------------------------------------------------------
# Digest / bundle building
# ---------------------------------------------------------------------------

class DigestBuildingTest(FixtureDatabaseTest):
    def _seed_monthly_series(self, series_id: str, values: list[float], *, now: datetime):
        for i, value in enumerate(values):
            macro_store.upsert_observation(
                self.connection, series_id=series_id, obs_date=date(2026, 1, 31) + timedelta(days=30 * i),
                value=value, status="ok", source_id="fake", units="percent", now=now,
            )

    def test_primary_delta_uses_prior_obs_for_low_frequency(self):
        indicator = make_indicator(cadence="monthly")
        now = datetime(2026, 8, 1, 9, 0, 0)
        self._seed_monthly_series(indicator.id, [2.0, 2.5], now=now)
        entry = mar.build_indicator_entry(indicator, self.connection, None, None)
        self.assertEqual(entry["cadence_class"], "low_frequency")
        self.assertEqual(entry["primary_delta_basis"], "prior_obs")
        self.assertAlmostEqual(entry["primary_delta"], 0.5, places=6)

    def test_primary_delta_uses_last_report_for_high_frequency(self):
        indicator = make_indicator(kind="priced", cadence="daily", value_field="close")
        now1 = datetime(2026, 7, 1, 9, 0, 0)
        now2 = datetime(2026, 8, 1, 9, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=date(2026, 7, 1), value=10.0,
            status="ok", source_id="fake", units="usd", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=date(2026, 8, 1), value=12.0,
            status="ok", source_id="fake", units="usd", now=now2,
        )
        last_run = {"created_at": datetime(2026, 7, 15, 0, 0, 0)}
        entry = mar.build_indicator_entry(indicator, self.connection, None, last_run)
        self.assertEqual(entry["cadence_class"], "high_frequency")
        self.assertEqual(entry["primary_delta_basis"], "last_report")

    def test_revision_surfaces_in_entry(self):
        indicator = make_indicator(cadence="monthly")
        now1 = datetime(2026, 7, 1, 9, 0, 0)
        now2 = datetime(2026, 8, 1, 9, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=date(2026, 6, 30), value=1.5,
            status="ok", source_id="fake", units="percent", now=now1,
        )
        macro_store.upsert_observation(
            self.connection, series_id=indicator.id, obs_date=date(2026, 6, 30), value=1.8,
            status="ok", source_id="fake", units="percent", now=now2,
        )
        last_run = {"created_at": datetime(2026, 7, 15, 0, 0, 0)}
        entry = mar.build_indicator_entry(indicator, self.connection, None, last_run)
        self.assertEqual(len(entry["revised_since_last_report"]), 1)
        self.assertEqual(entry["revised_since_last_report"][0]["prior_value"], 1.5)

    def test_no_data_indicator_reports_no_data_status(self):
        indicator = make_indicator()
        entry = mar.build_indicator_entry(indicator, self.connection, None, None)
        self.assertEqual(entry["status"], "no_data")
        self.assertIsNone(entry["value"])

    def test_derived_entry_shape(self):
        now = datetime(2026, 8, 1, 9, 0, 0)
        macro_store.upsert_observation(
            self.connection, series_id="A", obs_date=date(2026, 8, 1), value=5.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        macro_store.upsert_observation(
            self.connection, series_id="B", obs_date=date(2026, 8, 1), value=2.0,
            status="ok", source_id="fake", units="percent", now=now,
        )
        entry_def = Derived(id="SPREAD", domain="rates", op="spread", inputs=("A", "B"), align="locf", max_carry_days=5, units="percent")
        entry = mar.build_derived_entry(entry_def, self.connection)
        self.assertAlmostEqual(entry["value"], 3.0, places=6)
        self.assertEqual(entry["status"], "ok")

    def test_changed_unchanged_partition(self):
        stale_ind = make_indicator(id="STALE", cadence="monthly")
        now1 = datetime(2026, 7, 1, 9, 0, 0)
        self._seed_monthly_series(stale_ind.id, [1.0, 1.0], now=now1)
        registry = make_registry([stale_ind], updated=TODAY - timedelta(days=400))
        macro_store.record_run(
            self.connection, run_date=date(2026, 7, 15), registry_version=1, status="ok",
            bundle_path=None, indicator_count=1, created_at=datetime(2026, 7, 15, 0, 0, 0),
        )
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        digest, bundle = mar.build_digest_and_bundle(registry, self.connection, gate_results, today=TODAY, domains_filter=None)
        self.assertIn(stale_ind.output_id, digest["unchanged"])
        self.assertEqual(digest["changed"], [])

    def test_first_ever_report_treats_everything_as_changed(self):
        indicator = make_indicator(cadence="monthly")
        now = datetime(2026, 7, 1, 9, 0, 0)
        self._seed_monthly_series(indicator.id, [1.0, 1.0], now=now)
        registry = make_registry([indicator], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        digest, bundle = mar.build_digest_and_bundle(registry, self.connection, gate_results, today=TODAY, domains_filter=None)
        self.assertEqual(len(digest["changed"]), 1)
        self.assertEqual(digest["unchanged"], {})

    def test_bundle_includes_full_history(self):
        indicator = make_indicator(cadence="monthly")
        now = datetime(2026, 7, 1, 9, 0, 0)
        self._seed_monthly_series(indicator.id, [1.0, 1.1, 1.2], now=now)
        registry = make_registry([indicator], updated=TODAY - timedelta(days=400))
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        digest, bundle = mar.build_digest_and_bundle(registry, self.connection, gate_results, today=TODAY, domains_filter=None)
        self.assertEqual(len(bundle["indicators"][indicator.id]), 3)

    def test_digest_size_stays_under_soft_limit_at_realistic_scale(self):
        now = datetime(2026, 7, 1, 9, 0, 0)
        indicators = []
        for i in range(50):
            indicator = make_indicator(id=f"IND_{i}", cadence="daily", kind="priced", value_field="close")
            indicators.append(indicator)
            for day_offset in range(5):
                macro_store.upsert_observation(
                    self.connection, series_id=indicator.id, obs_date=date(2026, 7, 1) + timedelta(days=day_offset),
                    value=100.0 + day_offset, status="ok", source_id="fake", units="usd", now=now,
                )
        registry = make_registry(indicators, updated=TODAY - timedelta(days=400))
        macro_store.record_run(
            self.connection, run_date=date(2026, 7, 20), registry_version=1, status="ok",
            bundle_path=None, indicator_count=50, created_at=datetime(2026, 7, 20, 0, 0, 0),
        )
        gate_results = gate_module.compute_gate(registry, self.connection, today=TODAY)
        digest, _bundle = mar.build_digest_and_bundle(registry, self.connection, gate_results, today=TODAY, domains_filter=None)
        digest_text = json.dumps(macro_store.to_json_safe(digest))
        self.assertLessEqual(len(digest_text.encode("utf-8")), 12288)


# ---------------------------------------------------------------------------
# CLI-level behavior
# ---------------------------------------------------------------------------

class CliModeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "market.duckdb"
        self.registry_path = Path(self.temp_dir.name) / "registry.yml"
        self.registry_path.write_text(
            """
version: 1
updated: 2026-08-06
sources:
  fake_source: {adapter: boc_valet}
indicators:
  - id: BOC_RATE
    domain: policy
    kind: statistical
    source: fake_source
    series_ref: V39079
    transform: none
    cadence: monthly
    publication_lag_days: 0
    backfill_years: 0
    units: percent
""",
            encoding="utf-8",
        )

    def test_gate_mode_writes_nothing_when_db_missing(self):
        exit_code = mar.main([
            "--mode", "gate", "--registry-path", str(self.registry_path),
            "--db-path", str(self.db_path), "--run-date", "2026-08-06",
        ])
        self.assertEqual(exit_code, 0)
        self.assertFalse(self.db_path.exists())

    def test_refresh_then_read_reports_fresh_on_rerun(self):
        with patch.object(sources, "fetch", return_value=[(date(2026, 8, 5), 4.25)]):
            exit_code = mar.main([
                "--mode", "refresh", "--registry-path", str(self.registry_path),
                "--db-path", str(self.db_path), "--run-date", "2026-08-06",
            ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())

        with patch.object(sources, "fetch") as fake_fetch:
            fake_fetch.side_effect = AssertionError("should not refetch when nothing is due")
            exit_code = mar.main([
                "--mode", "refresh", "--registry-path", str(self.registry_path),
                "--db-path", str(self.db_path), "--run-date", "2026-08-06",
            ])
        self.assertEqual(exit_code, 0)
        fake_fetch.assert_not_called()

    def test_db_path_override_suppresses_run_recording(self):
        # The default sequence still emits a trace even though run recording
        # is suppressed; --trace-log-path keeps it out of the real
        # logs/SkillTrace.txt.
        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        with patch.object(sources, "fetch", return_value=[(date(2026, 8, 5), 4.25)]):
            mar.main([
                "--registry-path", str(self.registry_path), "--db-path", str(self.db_path),
                "--run-date", "2026-08-06", "--no-bundle", "--trace-log-path", str(trace_log_path),
            ])
        connection = macro_store.connect_read_only(self.db_path)
        try:
            self.assertIsNone(macro_store.get_last_successful_run(connection))
        finally:
            connection.close()


class DefaultOnWorkspaceTest(unittest.TestCase):
    """A run must exist without anyone remembering to pass --run-id, the same
    default-on requirement as the sibling investment-analyst-resources skill,
    reusing the existing is_default_db signal so the test suite itself never
    touches the real workspace/runs/. See DefaultOnRunCreationTest in
    tests/test_investment_analyst_resources.py for the parallel coverage."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "market.duckdb"
        self.registry_path = Path(self.temp_dir.name) / "registry.yml"
        self.registry_path.write_text(
            """
version: 1
updated: 2026-08-06
sources:
  fake_source: {adapter: boc_valet}
indicators:
  - id: BOC_RATE
    domain: policy
    kind: statistical
    source: fake_source
    series_ref: V39079
    transform: none
    cadence: monthly
    publication_lag_days: 0
    backfill_years: 0
    units: percent
""",
            encoding="utf-8",
        )

        base = Path(self.temp_dir.name) / "ws"
        self.runs_root = base / "runs"
        self.runs_root.mkdir(parents=True)
        import config as config_module

        ws_patcher = patch.multiple(
            config_module, WORKSPACE_FOLDER=base, WORKSPACE_RUNS_FOLDER=self.runs_root,
            WORKSPACE_ARCHIVE_FOLDER=base / "archive",
        )
        ws_patcher.start()
        self.addCleanup(ws_patcher.stop)

        # Omitting --db-path on the CLI makes args.db_path None, so
        # `is_default_db` is True and `db_path` resolves to this patched
        # constant -- simulating a real (non-test-flag) invocation without
        # touching the actual production market.duckdb.
        db_patcher = patch.object(macro_store, "MARKET_DB_PATH", self.db_path)
        db_patcher.start()
        self.addCleanup(db_patcher.stop)

    def _run_main(self, argv: list[str]) -> int:
        # Every default-on path emits a trace; without an explicit path it
        # falls back to the real logs/SkillTrace.txt, which a test must never
        # write into. Callers that care about trace content pass their own.
        if "--trace-log-path" not in argv:
            argv = argv + ["--trace-log-path", str(Path(self.temp_dir.name) / "default-trace.txt")]
        with patch.object(sources, "fetch", return_value=[(date(2026, 8, 5), 4.25)]):
            return mar.main(["--registry-path", str(self.registry_path)] + argv)

    def test_read_mode_with_no_run_id_still_opens_a_run(self):
        # `read` needs an already-populated DB; `refresh` first, same as a
        # real fan-out (one refresh, N parallel reads).
        self._run_main(["--mode", "refresh", "--run-date", "2026-08-06"])
        exit_code = self._run_main(["--mode", "read", "--run-date", "2026-08-06", "--no-bundle"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(list(self.runs_root.iterdir())), 1)

    def test_default_mode_with_no_run_id_still_opens_a_run(self):
        exit_code = self._run_main(["--run-date", "2026-08-06", "--no-bundle"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(list(self.runs_root.iterdir())), 1)

    def test_gate_mode_opens_no_run(self):
        self._run_main(["--mode", "gate", "--run-date", "2026-08-06"])
        self.assertEqual(list(self.runs_root.iterdir()), [])

    def test_refresh_mode_opens_no_run(self):
        self._run_main(["--mode", "refresh", "--run-date", "2026-08-06"])
        self.assertEqual(list(self.runs_root.iterdir()), [])

    def test_no_run_flag_opts_out(self):
        # Default mode (refresh -> read in one process) so the DB exists by
        # the time `read` runs.
        exit_code = self._run_main(["--run-date", "2026-08-06", "--no-bundle", "--no-run"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(list(self.runs_root.iterdir()), [])

    def test_no_run_opt_out_is_recorded_in_the_trace(self):
        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        self._run_main([
            "--run-date", "2026-08-06", "--no-bundle", "--no-run",
            "--trace-log-path", str(trace_log_path),
        ])
        self.assertIn("run=skipped(--no-run)", trace_log_path.read_text(encoding="utf-8"))
        record = json.loads(trace_log_path.with_suffix(".jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["workspace"]["skip_reason"], "--no-run")

    def test_explicit_db_path_opts_out_and_is_recorded(self):
        """--db-path is the existing test/debug convention; it must suppress
        workspace attachment the same way it already suppresses run
        recording, and the reason must be visible in the trace."""
        other_db = Path(self.temp_dir.name) / "other.duckdb"
        trace_log_path = Path(self.temp_dir.name) / "trace.txt"
        self._run_main([
            "--run-date", "2026-08-06", "--no-bundle",
            "--db-path", str(other_db), "--trace-log-path", str(trace_log_path),
        ])
        self.assertEqual(list(self.runs_root.iterdir()), [])
        record = json.loads(trace_log_path.with_suffix(".jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(record["workspace"]["skip_reason"], "non-default-db")

    def test_run_id_and_no_run_together_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            mar.parse_args(["--registry-path", str(self.registry_path), "--run-id", "x", "--no-run"])

    def test_explicit_run_id_still_names_the_run(self):
        self._run_main(["--run-date", "2026-08-06", "--no-bundle", "--run-id", "named-run"])
        self.assertTrue((self.runs_root / "named-run").is_dir())


class SharedTraceTest(unittest.TestCase):
    """This skill computed a trace but never logged it, in a shape that did not
    match its sibling. Both now emit the same record through `src/skill_trace.py`."""

    ENTRIES = [
        {"id": "BOC_RATE", "domain": "policy", "status": "fetched"},
        {"id": "CA_CPI", "domain": "inflation", "status": "stale"},
        {"id": "CA_PAYROLLS", "domain": "labour", "status": "not_configured"},
        {"id": "US_BREADTH", "domain": "breadth", "status": "not_configured"},
    ]

    def test_unconfigured_stubs_are_not_applicable_not_missing(self):
        trace = mar.build_trace(self.ENTRIES, "2026-08-06")
        self.assertEqual(trace.missing_fields(), ["inflation.CA_CPI"])
        self.assertEqual(
            sorted(trace.not_applicable_fields()),
            ["breadth.US_BREADTH", "labour.CA_PAYROLLS"],
        )
        # Two of four entries are graded, one succeeded.
        self.assertEqual(trace.fields_graded, 2)
        self.assertEqual(trace.completeness_pct, 50.0)

    def test_trace_is_grouped_by_registry_domain(self):
        trace = mar.build_trace(self.ENTRIES, "2026-08-06")
        self.assertEqual(
            sorted(d.name for d in trace.domains),
            ["breadth", "inflation", "labour", "policy"],
        )

    def test_digest_completeness_matches_the_shared_figure(self):
        digest_block = mar._completeness_trace(self.ENTRIES)
        trace = mar.build_trace(self.ENTRIES, "2026-08-06")
        self.assertEqual(digest_block["completeness_pct"], trace.completeness_pct)
        self.assertEqual(digest_block["total"], 4)
        self.assertEqual(digest_block["not_applicable"], 2)
        # The registry's own status vocabulary is retained alongside.
        self.assertEqual(digest_block["by_status"]["not_configured"], 2)

    def test_subject_is_the_run_date_since_this_skill_is_portfolio_wide(self):
        trace = mar.build_trace(self.ENTRIES, "2026-08-06")
        self.assertEqual(trace.subject, "2026-08-06")
        self.assertEqual(trace.kind, "market")


if __name__ == "__main__":
    unittest.main()
