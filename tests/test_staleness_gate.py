from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

# The staleness-gate module lives beside its skill, not in src/.
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / ".claude"
        / "skills"
        / "kb-staleness-gate"
        / "scripts"
    ),
)

import staleness_gate as gate  # noqa: E402

RUN_DATE = date(2026, 7, 21)


def _page(updated: date, section_updated: dict[str, str] | None = None) -> str:
    lines = [
        "---",
        'title: "Test Co (TST) — Stock Page"',
        "type: stock-page",
        "tickers: [TST]",
        "tags: [test]",
        "status: active",
        "created: 2026-01-01",
        f"updated: {updated.isoformat()}",
        'summary: "Test page."',
    ]
    if section_updated:
        lines.append("section_updated:")
        for key, value in section_updated.items():
            lines.append(f"  {key}: {value}")
    lines += ["---", "", "# TST — Test Co", "", "## Status", "", "- Decision: Hold", ""]
    return "\n".join(lines)


class StalenessGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stocks").mkdir(parents=True)

    def _write_page(self, ticker: str, text: str) -> None:
        (self.root / "stocks" / f"{ticker}.md").write_text(text, encoding="utf-8")

    def _compute(self, owned, force=None):
        return gate.compute_due_tickers(
            owned, force=force, kb_root=self.root, run_date=RUN_DATE
        )

    def test_first_run_detection(self):
        # No page on disk -> due, first_run, every gated section.
        result = self._compute(["NEWCO"])
        self.assertEqual(len(result["due_tickers"]), 1)
        entry = result["due_tickers"][0]
        self.assertEqual(entry["ticker"], "NEWCO")
        self.assertTrue(entry["first_run"])
        self.assertEqual(entry["due_sections"], list(gate.GATED_SECTIONS))
        self.assertEqual(result["skipped_count"], 0)

    def test_weekly_tier_only_due(self):
        # Monthly sections fresh (fall back to today's top-level updated);
        # weekly sections stamped 8 days ago -> only weekly due.
        stale = (RUN_DATE - timedelta(days=8)).isoformat()
        section_updated = {s: stale for s in gate.WEEKLY_SECTIONS}
        self._write_page("TST", _page(RUN_DATE, section_updated))
        result = self._compute(["TST"])
        self.assertEqual(len(result["due_tickers"]), 1)
        entry = result["due_tickers"][0]
        self.assertFalse(entry["first_run"])
        self.assertEqual(set(entry["due_sections"]), set(gate.WEEKLY_SECTIONS))

    def test_monthly_tier_only_due(self):
        # Weekly sections fresh; monthly sections stamped 31 days ago.
        stale = (RUN_DATE - timedelta(days=31)).isoformat()
        section_updated = {s: stale for s in gate.MONTHLY_SECTIONS}
        self._write_page("TST", _page(RUN_DATE, section_updated))
        result = self._compute(["TST"])
        entry = result["due_tickers"][0]
        self.assertEqual(set(entry["due_sections"]), set(gate.MONTHLY_SECTIONS))

    def test_nothing_due(self):
        # Everything fresh via a today top-level updated and no stale keys.
        self._write_page("TST", _page(RUN_DATE))
        result = self._compute(["TST"])
        self.assertEqual(result["due_tickers"], [])
        self.assertEqual(result["skipped_count"], 1)

    def test_missing_section_updated_falls_back_to_top_level(self):
        # No section_updated map at all; stale top-level updated -> all due.
        self._write_page("TST", _page(RUN_DATE - timedelta(days=40)))
        result = self._compute(["TST"])
        entry = result["due_tickers"][0]
        self.assertFalse(entry["first_run"])
        self.assertEqual(set(entry["due_sections"]), set(gate.GATED_SECTIONS))

    def test_force_scopes_to_named_and_marks_all_due(self):
        # A fresh page would normally be skipped; --force overrides its gate and
        # scopes the run to exactly the named tickers.
        self._write_page("TST", _page(RUN_DATE))
        self._write_page("OTHER", _page(RUN_DATE - timedelta(days=40)))
        result = self._compute(["TST", "OTHER"], force=["TST"])
        names = [e["ticker"] for e in result["due_tickers"]]
        self.assertEqual(names, ["TST"])  # OTHER (stale) not pulled in
        entry = result["due_tickers"][0]
        self.assertFalse(entry["first_run"])
        self.assertEqual(entry["due_sections"], list(gate.GATED_SECTIONS))

    def test_force_missing_page_sets_first_run(self):
        result = self._compute(["NEWCO"], force=["NEWCO"])
        entry = result["due_tickers"][0]
        self.assertTrue(entry["first_run"])

    def test_dry_run_performs_no_writes(self):
        self._write_page("TST", _page(RUN_DATE - timedelta(days=40)))
        before = {
            p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        gate.compute_due_tickers(
            ["TST"], dry_run=True, kb_root=self.root, run_date=RUN_DATE
        )
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_output_schema_shape(self):
        self._write_page("TST", _page(RUN_DATE - timedelta(days=40)))
        result = self._compute(["TST"])
        self.assertEqual(result["schema"], "kb-staleness-gate.v1")
        self.assertEqual(result["run_date"], RUN_DATE.isoformat())
        self.assertIn("due_tickers", result)
        self.assertIn("skipped_count", result)

    def test_load_owned_tickers_from_classification_json(self):
        payload = {"holdings": [{"ticker": "AAPL"}, {"ticker": "nvda"}, {"other": 1}]}
        path = self.root / "classification.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        owned = gate.load_owned_tickers(path)
        self.assertEqual(owned, ["AAPL", "NVDA"])


if __name__ == "__main__":
    unittest.main()
