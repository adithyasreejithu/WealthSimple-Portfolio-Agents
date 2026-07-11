import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-intake-document" / "scripts"))

import kb_pages
import intake_document


INDEX_STUB = (
    "---\ntitle: {title}\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
    "created: '2026-07-01'\nupdated: '2026-07-01'\nsummary: idx\n---\n\n"
    "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n"
)


class IntakeDocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kb_root = Path(self.tmp.name) / "Knowledge-Base"
        for rel in ("sources", "earnings", "dividends", "market-research"):
            (self.kb_root / rel).mkdir(parents=True)
        (self.kb_root / "sources" / "index.md").write_text(INDEX_STUB.format(title="Sources"), encoding="utf-8")
        (self.kb_root / "earnings" / "index.md").write_text(INDEX_STUB.format(title="Earnings"), encoding="utf-8")
        (self.kb_root / "dividends" / "index.md").write_text(INDEX_STUB.format(title="Dividends"), encoding="utf-8")
        (self.kb_root / "index.md").write_text(INDEX_STUB.format(title="Research Wiki"), encoding="utf-8")
        (self.kb_root / "logs").mkdir()
        for name in ("research-log.md", "update-log.md"):
            (self.kb_root / "logs" / name).write_text(
                f"# {name}\n\n| Date | Action | Page | Note |\n|---|---|---|---|\n", encoding="utf-8"
            )

        self.kb_root_patch = patch.object(kb_pages, "KB_ROOT", self.kb_root)
        self.kb_root_patch.start()
        self.addCleanup(self.kb_root_patch.stop)

        # ROOT is a module-level constant computed from __file__ at import
        # time; the sample input must live under it for validate_input to
        # accept it, so use the real repo's scratch-safe tests/ directory.
        self.input_path = ROOT / "tests" / "tmp_intake_sample.pdf"
        self.input_path.write_bytes(b"%PDF-1.4 fake")
        self.addCleanup(self.input_path.unlink)

        self.convert_patch = patch.object(intake_document, "convert_to_markdown", return_value="Converted body text.")
        self.convert_patch.start()
        self.addCleanup(self.convert_patch.stop)

    def _run(self, args):
        with patch.object(sys, "argv", ["intake_document.py", *args]):
            return intake_document.main()

    def test_sources_only_creates_source_page_without_note(self):
        rc = self._run([
            "--input", str(self.input_path), "--title", "Q3 Investor Letter",
            "--dest-folder", "sources-only", "--source-date", "2026-07-01",
        ])
        self.assertEqual(rc, 0)
        source_path = self.kb_root / "sources" / "2026" / "2026-07-01-q3-investor-letter.md"
        self.assertTrue(source_path.exists())
        meta, body = kb_pages.parse_page_file(source_path)
        self.assertEqual(meta["type"], "source-document")
        self.assertEqual(meta["status"], "final")
        self.assertIn("Converted body text.", body)

    def test_dest_folder_creates_companion_note(self):
        rc = self._run([
            "--input", str(self.input_path), "--title", "AAPL Q3 Earnings Call",
            "--tickers", "aapl", "--dest-folder", "earnings", "--source-date", "2026-07-01",
        ])
        self.assertEqual(rc, 0)
        note_path = self.kb_root / "earnings" / "earnings-notes" / "2026-07-01-aapl-q3-earnings-call.md"
        self.assertTrue(note_path.exists())
        meta, body = kb_pages.parse_page_file(note_path)
        self.assertEqual(meta["type"], "earnings-note")
        self.assertEqual(meta["status"], "draft")
        self.assertEqual(meta["tickers"], ["AAPL"])
        self.assertEqual(len(meta["sources"]), 1)

    def test_doc_type_override(self):
        rc = self._run([
            "--input", str(self.input_path), "--title", "Dividend Safety Screen",
            "--dest-folder", "dividends", "--doc-type", "research-note", "--source-date", "2026-07-01",
        ])
        self.assertEqual(rc, 0)
        note_path = self.kb_root / "dividends" / "2026-07-01-dividend-safety-screen.md"
        meta, _ = kb_pages.parse_page_file(note_path)
        self.assertEqual(meta["type"], "research-note")

    def test_rejects_disallowed_extension(self):
        bad_input = ROOT / "tests" / "tmp_intake_bad.exe"
        bad_input.write_bytes(b"MZ")
        self.addCleanup(bad_input.unlink)
        rc = self._run(["--input", str(bad_input), "--title", "x", "--dest-folder", "sources-only"])
        self.assertEqual(rc, 1)

    def test_rejects_input_outside_repo(self):
        with tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / "doc.pdf"
            outside_file.write_bytes(b"%PDF")
            rc = self._run(["--input", str(outside_file), "--title", "x", "--dest-folder", "sources-only"])
            self.assertEqual(rc, 1)

    def test_refuses_to_overwrite_existing_source_page(self):
        args = [
            "--input", str(self.input_path), "--title", "Duplicate Doc",
            "--dest-folder", "sources-only", "--source-date", "2026-07-01",
        ]
        self.assertEqual(self._run(args), 0)
        self.assertEqual(self._run(args), 1)

    def test_indexes_and_logs_updated(self):
        self._run([
            "--input", str(self.input_path), "--title", "AAPL Q3 Earnings Call",
            "--dest-folder", "earnings", "--source-date", "2026-07-01",
        ])
        sources_index = (self.kb_root / "sources" / "index.md").read_text(encoding="utf-8")
        self.assertIn("2026-07-01-aapl-q3-earnings-call.md", sources_index)
        earnings_index = (self.kb_root / "earnings" / "index.md").read_text(encoding="utf-8")
        self.assertIn("earnings-notes/2026-07-01-aapl-q3-earnings-call.md", earnings_index)
        research_log = (self.kb_root / "logs" / "research-log.md").read_text(encoding="utf-8")
        self.assertIn("ingested", research_log)
        update_log = (self.kb_root / "logs" / "update-log.md").read_text(encoding="utf-8")
        self.assertIn("created", update_log)


if __name__ == "__main__":
    unittest.main()
