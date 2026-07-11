import tempfile
import unittest
from pathlib import Path

import kb_pages


STOCK_PAGE = """---
title: "Apple Inc. (AAPL) — Stock Page"
type: stock-page
tickers: [AAPL]
tags: [quality, technology]
status: active
created: 2026-07-01
updated: 2026-07-10
summary: "Quality compounder; hold."
related: []
sources: []
---

# AAPL — Apple Inc.

## Status

- Decision: Hold
"""


class ParsePageTest(unittest.TestCase):
    def test_round_trip(self):
        meta, body = kb_pages.parse_page(STOCK_PAGE)
        self.assertEqual(meta["title"], "Apple Inc. (AAPL) — Stock Page")
        self.assertEqual(meta["tickers"], ["AAPL"])
        self.assertIn("# AAPL", body)

        rendered = kb_pages.serialize_page(meta, body)
        meta2, body2 = kb_pages.parse_page(rendered)
        self.assertEqual(meta, meta2)
        self.assertEqual(body.strip(), body2.strip())

    def test_missing_front_matter_raises(self):
        with self.assertRaises(kb_pages.KBPageError):
            kb_pages.parse_page("# just a heading\n")

    def test_unterminated_front_matter_raises(self):
        with self.assertRaises(kb_pages.KBPageError):
            kb_pages.parse_page("---\ntitle: x\n")


class ValidateMetaTest(unittest.TestCase):
    def test_valid_stock_page_has_no_errors(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        self.assertEqual(kb_pages.validate_meta(meta), [])

    def test_missing_required_field(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        del meta["summary"]
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("summary" in err for err in errors))

    def test_unknown_type_rejected(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["type"] = "not-a-type"
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("unknown type" in err for err in errors))

    def test_status_not_allowed_for_type(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["status"] = "draft"  # not a valid stock-page status
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("status" in err for err in errors))

    def test_lowercase_ticker_rejected(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["tickers"] = ["aapl"]
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("ticker" in err for err in errors))

    def test_non_kebab_tag_rejected(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["tags"] = ["Not_Kebab"]
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("kebab-case" in err for err in errors))

    def test_bad_date_rejected(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["updated"] = "07/10/2026"
        errors = kb_pages.validate_meta(meta)
        self.assertTrue(any("updated" in err for err in errors))


class IndexRowTest(unittest.TestCase):
    def test_row_format(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        row = kb_pages.index_row(meta, "AAPL.md")
        self.assertEqual(
            row,
            "| [Apple Inc. (AAPL) — Stock Page](AAPL.md) | stock-page | AAPL | active | "
            "2026-07-10 | Quality compounder; hold. |",
        )

    def test_pipe_in_summary_is_escaped(self):
        meta, _ = kb_pages.parse_page(STOCK_PAGE)
        meta["summary"] = "Contains | a pipe"
        row = kb_pages.index_row(meta, "AAPL.md")
        self.assertIn("Contains \\| a pipe", row)


class IndexRebuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_rebuild_index_populates_rows(self):
        self._write("stocks/AAPL.md", STOCK_PAGE)
        index = self._write(
            "stocks/index.md",
            "---\ntitle: Stocks\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
            "created: 2026-07-01\nupdated: 2026-07-01\nsummary: idx\n---\n\n"
            "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n",
        )
        errors = kb_pages.rebuild_index(index)
        self.assertEqual(errors, [])
        text = index.read_text(encoding="utf-8")
        self.assertIn("AAPL.md", text)
        self.assertIn("Apple Inc.", text)

    def test_rebuild_index_skips_and_reports_bad_pages(self):
        self._write("stocks/BROKEN.md", "not a valid page\n")
        index = self._write(
            "stocks/index.md",
            "---\ntitle: Stocks\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
            "created: 2026-07-01\nupdated: 2026-07-01\nsummary: idx\n---\n\n"
            "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n",
        )
        errors = kb_pages.rebuild_index(index)
        self.assertEqual(len(errors), 1)


class LogAppendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "logs").mkdir()
        self.log = self.root / "logs" / "update-log.md"
        self.log.write_text("# Update Log\n\n| Date | Action | Page | Note |\n|---|---|---|---|\n", encoding="utf-8")

    def test_append_log_row_appends_without_touching_existing_content(self):
        kb_pages.append_log_row(self.log, ["2026-07-11", "created", "stocks/AAPL.md", "initial thesis"])
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("| 2026-07-11 | created | stocks/AAPL.md | initial thesis |", text)
        self.assertIn("# Update Log", text)

    def test_append_update_log_helper(self):
        kb_pages.append_update_log(self.root, "created", "stocks/AAPL.md", "initial thesis", on="2026-07-11")
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("| 2026-07-11 | created | stocks/AAPL.md | initial thesis |", text)


class SlugifyTest(unittest.TestCase):
    def test_slugify_basic(self):
        self.assertEqual(kb_pages.slugify("Q3 FY2026 10-Q!"), "q3-fy2026-10-q")

    def test_slugify_empty_falls_back(self):
        self.assertEqual(kb_pages.slugify("***"), "untitled")


if __name__ == "__main__":
    unittest.main()
