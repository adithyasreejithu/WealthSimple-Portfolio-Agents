"""Tests for owner-approved classifier overrides written from the dashboard.

The file under management is an approved reference document, so these cover
both correctness (validation, round-trip) and the property that matters for a
hand-curated file: editing one entry must not reformat any other.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from manual_overrides import (  # noqa: E402
    OverrideError,
    assignable_groups,
    load_approved_groups,
    load_overrides,
    upsert_override,
)

REAL_OVERRIDES = REPO_ROOT / "Knowledge-Base" / "ref" / "manual_overrides_v1_1.yaml"
REAL_RULES = REPO_ROOT / "Knowledge-Base" / "ref" / "classification_rules_v1_1.yaml"


class ManualOverridesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Work on a copy of the real reference files so the tests exercise the
        # actual document shape without ever writing to the repository copy.
        self.path = Path(self.temp_dir.name) / "manual_overrides_v1_1.yaml"
        self.rules = Path(self.temp_dir.name) / "classification_rules_v1_1.yaml"
        shutil.copyfile(REAL_OVERRIDES, self.path)
        shutil.copyfile(REAL_RULES, self.rules)

    def _upsert(self, ticker, group, **kwargs):
        kwargs.setdefault("rationale", "Set from the dashboard during review.")
        return upsert_override(ticker, group, path=self.path, rules_path=self.rules, **kwargs)

    def test_approved_groups_exclude_bookkeeping_buckets_when_assignable(self):
        self.assertIn("Needs Review", load_approved_groups(self.rules))
        assignable = assignable_groups(self.rules)
        self.assertIn("Quality", assignable)
        self.assertNotIn("Needs Review", assignable)
        self.assertNotIn("Cash", assignable)

    def test_new_ticker_is_appended_and_readable(self):
        before = load_overrides(self.path)

        stored = self._upsert("ZZZZ", "Quality", secondary_tags=["Equity", "Test"])

        after = load_overrides(self.path)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(stored["ticker"], "ZZZZ")
        self.assertEqual(stored["primary_group"], "Quality")
        self.assertEqual(stored["secondary_tags"], ["Equity", "Test"])
        self.assertTrue(stored["active"])
        self.assertEqual(after[-1]["ticker"], "ZZZZ")

    def test_existing_ticker_is_replaced_not_duplicated(self):
        original = load_overrides(self.path)
        existing = original[0]["ticker"]

        self._upsert(existing, "Alternatives", rationale="Role changed.")

        after = load_overrides(self.path)
        matches = [row for row in after if row["ticker"] == existing]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["primary_group"], "Alternatives")
        self.assertEqual(matches[0]["rationale"], "Role changed.")
        self.assertEqual(len(after), len(original))

    def test_editing_one_entry_leaves_the_rest_of_the_file_untouched(self):
        before_text = self.path.read_text(encoding="utf-8")
        before_entries = {row["ticker"]: row for row in load_overrides(self.path)}
        target = list(before_entries)[1]

        self._upsert(target, "Growth", rationale="Reclassified.")

        after_text = self.path.read_text(encoding="utf-8")
        # The document preamble (folded purpose block, policy notes) survives
        # verbatim -- this is why entries are spliced instead of re-dumped.
        self.assertIn("purpose: >", after_text)
        self.assertEqual(
            before_text.split("overrides:")[0],
            after_text.split("overrides:")[0],
        )
        after_entries = {row["ticker"]: row for row in load_overrides(self.path)}
        self.assertEqual(set(before_entries), set(after_entries))
        for ticker, entry in after_entries.items():
            if ticker != target:
                self.assertEqual(entry, before_entries[ticker])

    def test_rationale_with_yaml_punctuation_round_trips(self):
        stored = self._upsert("ZZZZ", "Income", rationale="Yield: high, per 2026 review #2")

        self.assertEqual(stored["rationale"], "Yield: high, per 2026 review #2")
        reloaded = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        match = [row for row in reloaded["overrides"] if row["ticker"] == "ZZZZ"][0]
        self.assertEqual(match["rationale"], "Yield: high, per 2026 review #2")

    def test_ticker_is_normalized_to_uppercase(self):
        stored = self._upsert("  zzzz  ", "Core")
        self.assertEqual(stored["ticker"], "ZZZZ")

    def test_unapproved_group_is_rejected_without_touching_the_file(self):
        before = self.path.read_text(encoding="utf-8")

        with self.assertRaises(OverrideError):
            self._upsert("ZZZZ", "Speculative")

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_fallback_and_cash_groups_are_not_assignable(self):
        for group in ("Needs Review", "Cash"):
            with self.subTest(group=group), self.assertRaises(OverrideError):
                self._upsert("ZZZZ", group)

    def test_blank_ticker_and_missing_rationale_are_rejected(self):
        with self.assertRaises(OverrideError):
            self._upsert("   ", "Core")
        with self.assertRaises(OverrideError):
            self._upsert("ZZZZ", "Core", rationale="   ")


if __name__ == "__main__":
    unittest.main()
