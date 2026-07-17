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


FULL_STATUS_BODY = """# AAPL -- Apple Inc.

## Status

- Portfolio Status: active
- Portfolio Role: Quality
- Decision: Hold
- Confidence: High
- Time Horizon: Long-term
- Last Updated: 2026-07-14

## Decision History

| Date | Action | Verdict | Note |
|---|---|---|---|
| 2026-05-01 | Buy | new | Rubric v1.0 score 4.10; initial add. |
| 2026-07-14 | Hold | unchanged | Rubric v1.3 score 3.62; thesis intact. |

## Company Overview
"""

EMPTY_HISTORY_BODY = """# NEW -- New Co.

## Status

- Portfolio Status: watchlist
- Decision: Watchlist
- Confidence: Low
- Time Horizon: Medium-term
- Last Updated: 2026-07-15

## Decision History

| Date | Action | Verdict | Note |
|---|---|---|---|

## Company Overview
"""


class ParseStatusBlockTest(unittest.TestCase):
    def test_extracts_all_present_keys(self):
        status = kb_pages.parse_status_block(FULL_STATUS_BODY)
        self.assertEqual(status["portfolio_status"], "active")
        self.assertEqual(status["portfolio_role"], "Quality")
        self.assertEqual(status["decision"], "Hold")
        self.assertEqual(status["confidence"], "High")
        self.assertEqual(status["time_horizon"], "Long-term")
        self.assertEqual(status["last_updated"], "2026-07-14")

    def test_absent_keys_omitted(self):
        status = kb_pages.parse_status_block(EMPTY_HISTORY_BODY)
        self.assertNotIn("portfolio_role", status)
        self.assertEqual(status["decision"], "Watchlist")

    def test_stops_at_next_section(self):
        body = "## Status\n\n- Decision: Hold\n\n## Company Overview\n\n- Decision: Should Not Count\n"
        status = kb_pages.parse_status_block(body)
        self.assertEqual(status["decision"], "Hold")

    def test_no_status_section_returns_empty(self):
        self.assertEqual(kb_pages.parse_status_block("# Title\n\nNo status here.\n"), {})


class DecisionHistoryRowsTest(unittest.TestCase):
    def test_all_rows_extracted_in_order(self):
        rows = kb_pages.decision_history_rows(FULL_STATUS_BODY)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"date": "2026-05-01", "action": "Buy", "verdict": "new", "note": "Rubric v1.0 score 4.10; initial add."})
        self.assertEqual(rows[1]["date"], "2026-07-14")

    def test_header_and_separator_rows_skipped(self):
        rows = kb_pages.decision_history_rows(FULL_STATUS_BODY)
        self.assertTrue(all(kb_pages.DATE_PATTERN.match(r["date"]) for r in rows))

    def test_empty_table_returns_no_rows(self):
        self.assertEqual(kb_pages.decision_history_rows(EMPTY_HISTORY_BODY), [])

    def test_last_decision_history_row_returns_most_recent(self):
        row = kb_pages.last_decision_history_row(FULL_STATUS_BODY)
        self.assertEqual(row["date"], "2026-07-14")
        self.assertEqual(row["action"], "Hold")
        self.assertEqual(row["verdict"], "unchanged")

    def test_last_decision_history_row_none_when_empty(self):
        self.assertIsNone(kb_pages.last_decision_history_row(EMPTY_HISTORY_BODY))


if __name__ == "__main__":
    unittest.main()
