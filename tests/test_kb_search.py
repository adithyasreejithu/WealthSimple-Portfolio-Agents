import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-search" / "scripts"))

import kb_pages
import kb_search
import validate_kb


def _page(title, ptype, tickers, tags, status, updated, summary, body_extra="", related=None):
    related_yaml = ""
    if related:
        related_yaml = "related:\n" + "".join(f"  - {r}\n" for r in related)
    return (
        "---\n"
        f'title: "{title}"\n'
        f"type: {ptype}\n"
        f"tickers: {tickers}\n"
        f"tags: {tags}\n"
        f"status: {status}\n"
        "created: '2026-07-01'\n"
        f"updated: '{updated}'\n"
        f'summary: "{summary}"\n'
        f"{related_yaml}"
        "---\n\n"
        f"# {title}\n\n{body_extra}\n"
    )


class KBFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kb_root = Path(self.tmp.name)
        (self.kb_root / "stocks").mkdir()
        (self.kb_root / "ref").mkdir()
        (self.kb_root / "ref" / "policy_v1_1.yaml").write_text("name: policy\n", encoding="utf-8")
        (self.kb_root / "README.md").write_text("readme\n", encoding="utf-8")

        (self.kb_root / "stocks" / "AAPL.md").write_text(
            _page(
                "Apple Inc. (AAPL) — Stock Page",
                "stock-page",
                "[AAPL]",
                "[quality, technology]",
                "active",
                "2026-07-10",
                "Quality compounder; hold.",
                body_extra="Buybacks remain aggressive this quarter.",
            ),
            encoding="utf-8",
        )
        (self.kb_root / "stocks" / "NVDA.md").write_text(
            _page(
                "NVIDIA Corporation (NVDA) — Stock Page",
                "stock-page",
                "[NVDA]",
                "[growth, semiconductors]",
                "watchlist",
                "2026-07-09",
                "AI compute leader; watching valuation.",
                body_extra="Datacenter demand remains strong.",
            ),
            encoding="utf-8",
        )
        index_stub = (
            "---\ntitle: {title}\ntype: index\ntickers: []\ntags: []\nstatus: generated\n"
            "created: '2026-07-01'\nupdated: '2026-07-01'\nsummary: idx\n---\n\n"
            "<!-- kb-index:begin -->\n<!-- kb-index:end -->\n"
        )
        (self.kb_root / "stocks" / "index.md").write_text(index_stub.format(title="Stocks"), encoding="utf-8")
        (self.kb_root / "index.md").write_text(index_stub.format(title="Research Wiki"), encoding="utf-8")
        for status in ("active", "watchlist", "closed", "rejected"):
            status_dir = self.kb_root / "theses" / status
            status_dir.mkdir(parents=True)
            (status_dir / "index.md").write_text(index_stub.format(title=f"Theses {status}"), encoding="utf-8")

        self.kb_root_patch = patch.object(kb_pages, "KB_ROOT", self.kb_root)
        self.kb_root_patch.start()
        self.addCleanup(self.kb_root_patch.stop)


class KBSearchTest(KBFixture):
    def test_query_matches_front_matter_and_ranks_first(self):
        results = kb_search.search(
            self.kb_root, query="compounder", ticker=None, ptype=None, tag=None, status=None, limit=20
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "stocks/AAPL.md")
        self.assertTrue(results[0]["front_matter_hit"])

    def test_query_matches_body_only(self):
        results = kb_search.search(
            self.kb_root, query="datacenter", ticker=None, ptype=None, tag=None, status=None, limit=20
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "stocks/NVDA.md")
        self.assertFalse(results[0]["front_matter_hit"])
        self.assertEqual(results[0]["matched_lines"][0]["text"], "Datacenter demand remains strong.")

    def test_ticker_filter(self):
        results = kb_search.search(
            self.kb_root, query=None, ticker="nvda", ptype=None, tag=None, status=None, limit=20
        )
        self.assertEqual([r["path"] for r in results], ["stocks/NVDA.md"])

    def test_type_and_status_filters_combine_with_and(self):
        results = kb_search.search(
            self.kb_root, query=None, ticker=None, ptype="stock-page", tag=None, status="active", limit=20
        )
        self.assertEqual([r["path"] for r in results], ["stocks/AAPL.md"])

    def test_tag_filter(self):
        results = kb_search.search(
            self.kb_root, query=None, ticker=None, ptype=None, tag="growth", status=None, limit=20
        )
        self.assertEqual([r["path"] for r in results], ["stocks/NVDA.md"])

    def test_no_matches_returns_empty_list_not_error(self):
        results = kb_search.search(
            self.kb_root, query="nonexistent-term-xyz", ticker=None, ptype=None, tag=None, status=None, limit=20
        )
        self.assertEqual(results, [])

    def test_index_pages_excluded_from_results(self):
        results = kb_search.search(
            self.kb_root, query=None, ticker=None, ptype="index", tag=None, status=None, limit=20
        )
        self.assertEqual(results, [])

    def test_limit_is_respected(self):
        results = kb_search.search(
            self.kb_root, query=None, ticker=None, ptype="stock-page", tag=None, status=None, limit=1
        )
        self.assertEqual(len(results), 1)

    def test_render_text_no_matches(self):
        self.assertEqual(kb_search.render_text([]), "No matches.")


class ValidateKBTest(KBFixture):
    def test_valid_kb_reports_no_errors(self):
        errors = validate_kb.validate_front_matter(self.kb_root)
        self.assertEqual(errors, [])

    def test_broken_front_matter_detected(self):
        (self.kb_root / "stocks" / "BROKEN.md").write_text("no front matter here\n", encoding="utf-8")
        errors = validate_kb.validate_front_matter(self.kb_root)
        self.assertEqual(len(errors), 1)

    def test_bad_status_detected(self):
        (self.kb_root / "stocks" / "BAD.md").write_text(
            _page("Bad", "stock-page", "[BAD]", "[]", "draft", "2026-07-10", "invalid status for stock-page"),
            encoding="utf-8",
        )
        errors = validate_kb.validate_front_matter(self.kb_root)
        self.assertTrue(any("status" in e for e in errors))

    def test_related_link_resolution(self):
        (self.kb_root / "stocks" / "LINKED.md").write_text(
            _page(
                "Linked",
                "stock-page",
                "[LINKED]",
                "[]",
                "active",
                "2026-07-10",
                "has a related link",
                related=["missing-page.md"],
            ),
            encoding="utf-8",
        )
        errors = validate_kb.validate_related_links(self.kb_root)
        self.assertTrue(any("missing-page.md" in e for e in errors))

    def test_fix_indexes_regenerates_stocks_index(self):
        validate_kb.fix_indexes(self.kb_root)
        text = (self.kb_root / "stocks" / "index.md").read_text(encoding="utf-8")
        self.assertIn("AAPL.md", text)
        self.assertIn("NVDA.md", text)


if __name__ == "__main__":
    unittest.main()
