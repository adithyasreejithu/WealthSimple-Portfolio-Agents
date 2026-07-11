import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-update-thesis" / "scripts"))

import kb_pages
import thesis_page
import append_log


INDEX_STUB = (
    "---\ntitle: {title}\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
    "created: '2026-07-01'\nupdated: '2026-07-01'\nsummary: idx\n---\n\n"
    "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n"
)


class ThesisScriptsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kb_root = Path(self.tmp.name) / "Knowledge-Base"
        (self.kb_root / "stocks").mkdir(parents=True)
        (self.kb_root / "stocks" / "index.md").write_text(INDEX_STUB.format(title="Stocks"), encoding="utf-8")
        (self.kb_root / "index.md").write_text(INDEX_STUB.format(title="Research Wiki"), encoding="utf-8")
        for status in ("active", "watchlist", "closed", "rejected"):
            status_dir = self.kb_root / "theses" / status
            status_dir.mkdir(parents=True)
            (status_dir / "index.md").write_text(INDEX_STUB.format(title=f"Theses {status}"), encoding="utf-8")
        (self.kb_root / "logs").mkdir()
        for name, header in (
            ("decision-log.md", "| Date | Tickers | Action | Verdict | Note |\n|---|---|---|---|---|\n"),
            ("research-log.md", "| Date | Tickers | Action | Sources | Note |\n|---|---|---|---|---|\n"),
            ("update-log.md", "| Date | Action | Page | Note |\n|---|---|---|---|\n"),
        ):
            (self.kb_root / "logs" / name).write_text(f"# {name}\n\n{header}", encoding="utf-8")

        self.classification_path = Path(self.tmp.name) / "classification.json"
        self.classification_path.write_text(
            json.dumps(
                {
                    "holdings": [
                        {"ticker": "AAPL", "company_name": "Apple Inc.", "primary_group": "Quality"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        self.kb_root_patch = patch.object(kb_pages, "KB_ROOT", self.kb_root)
        self.kb_root_patch.start()
        self.addCleanup(self.kb_root_patch.stop)

    def _run_thesis(self, args):
        with patch.object(sys, "argv", ["thesis_page.py", *args]):
            return thesis_page.main()

    def _run_log(self, args):
        with patch.object(sys, "argv", ["append_log.py", *args]):
            return append_log.main()


class CreateTest(ThesisScriptsFixture):
    def test_create_new_page_prefills_from_classification(self):
        rc = self._run_thesis([
            "create", "--ticker", "aapl", "--status", "watchlist",
            "--classification", str(self.classification_path),
        ])
        self.assertEqual(rc, 0)
        page = self.kb_root / "stocks" / "AAPL.md"
        self.assertTrue(page.exists())
        meta, body = kb_pages.parse_page_file(page)
        self.assertEqual(meta["status"], "watchlist")
        self.assertIn("Apple Inc.", meta["title"])
        self.assertIn("Portfolio Role: Quality", body)
        for section in thesis_page.REQUIRED_SECTIONS:
            self.assertIn(section, body)

    def test_create_refuses_if_page_exists(self):
        self._run_thesis(["create", "--ticker", "AAPL", "--status", "research"])
        rc = self._run_thesis(["create", "--ticker", "AAPL", "--status", "research"])
        self.assertEqual(rc, 1)

    def test_create_updates_indexes_and_log(self):
        self._run_thesis(["create", "--ticker", "NVDA", "--status", "research"])
        stocks_index = (self.kb_root / "stocks" / "index.md").read_text(encoding="utf-8")
        self.assertIn("NVDA.md", stocks_index)
        update_log = (self.kb_root / "logs" / "update-log.md").read_text(encoding="utf-8")
        self.assertIn("stocks/NVDA.md", update_log)

    def test_create_unheld_ticker_defaults_role_unassigned(self):
        self._run_thesis(["create", "--ticker", "ZZZZ", "--status", "research"])
        _, body = kb_pages.parse_page_file(self.kb_root / "stocks" / "ZZZZ.md")
        self.assertIn("Portfolio Role: Unassigned", body)


class ValidateTest(ThesisScriptsFixture):
    def test_valid_page_passes(self):
        self._run_thesis(["create", "--ticker", "AAPL", "--status", "research"])
        rc = self._run_thesis(["validate", "--path", str(self.kb_root / "stocks" / "AAPL.md")])
        self.assertEqual(rc, 0)

    def test_missing_section_fails(self):
        self._run_thesis(["create", "--ticker", "AAPL", "--status", "research"])
        page = self.kb_root / "stocks" / "AAPL.md"
        text = page.read_text(encoding="utf-8").replace("## Bull Case\n", "")
        page.write_text(text, encoding="utf-8")
        rc = self._run_thesis(["validate", "--path", str(page)])
        self.assertEqual(rc, 1)

    def test_wrong_type_fails(self):
        page = self.kb_root / "stocks" / "BAD.md"
        page.write_text(
            "---\ntitle: Bad\ntype: research-note\ntickers: []\ntags: []\nstatus: draft\n"
            "created: '2026-07-01'\nupdated: '2026-07-01'\nsummary: x\n---\n\nbody\n",
            encoding="utf-8",
        )
        rc = self._run_thesis(["validate", "--path", str(page)])
        self.assertEqual(rc, 1)


class SetStatusTest(ThesisScriptsFixture):
    def test_set_status_updates_front_matter_and_status_block(self):
        self._run_thesis(["create", "--ticker", "AAPL", "--status", "watchlist"])
        rc = self._run_thesis(["set-status", "--ticker", "AAPL", "--status", "active"])
        self.assertEqual(rc, 0)
        meta, body = kb_pages.parse_page_file(self.kb_root / "stocks" / "AAPL.md")
        self.assertEqual(meta["status"], "active")
        self.assertIn("- Portfolio Status: active", body)

    def test_set_status_updates_thesis_views(self):
        self._run_thesis(["create", "--ticker", "AAPL", "--status", "watchlist"])
        self._run_thesis(["set-status", "--ticker", "AAPL", "--status", "active"])
        active_view = (self.kb_root / "theses" / "active" / "index.md").read_text(encoding="utf-8")
        self.assertIn("AAPL.md", active_view)
        watchlist_view = (self.kb_root / "theses" / "watchlist" / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("AAPL.md", watchlist_view)

    def test_set_status_missing_page_errors(self):
        rc = self._run_thesis(["set-status", "--ticker", "ZZZZ", "--status", "active"])
        self.assertEqual(rc, 1)


class AppendLogTest(ThesisScriptsFixture):
    def test_decision_log_row(self):
        rc = self._run_log([
            "--log", "decision", "--tickers", "aapl", "--action", "Hold",
            "--verdict", "unchanged", "--note", "Q3 review, no change.",
        ])
        self.assertEqual(rc, 0)
        text = (self.kb_root / "logs" / "decision-log.md").read_text(encoding="utf-8")
        self.assertIn("| AAPL | Hold | unchanged | Q3 review, no change. |", text)

    def test_decision_log_requires_verdict(self):
        rc = self._run_log(["--log", "decision", "--action", "Hold", "--note", "x"])
        self.assertEqual(rc, 1)

    def test_research_log_row(self):
        rc = self._run_log([
            "--log", "research", "--tickers", "AAPL", "--action", "reviewed 10-Q",
            "--sources", "sources/2026/2026-07-01-aapl-10q.md", "--note", "Reviewed quarterly filing.",
        ])
        self.assertEqual(rc, 0)
        text = (self.kb_root / "logs" / "research-log.md").read_text(encoding="utf-8")
        self.assertIn("sources/2026/2026-07-01-aapl-10q.md", text)

    def test_update_log_row(self):
        rc = self._run_log([
            "--log", "update", "--action", "updated", "--page", "stocks/AAPL.md", "--note", "Updated thesis.",
        ])
        self.assertEqual(rc, 0)
        text = (self.kb_root / "logs" / "update-log.md").read_text(encoding="utf-8")
        self.assertIn("| updated | stocks/AAPL.md | Updated thesis. |", text)

    def test_update_log_requires_page(self):
        rc = self._run_log(["--log", "update", "--action", "updated", "--note", "x"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
