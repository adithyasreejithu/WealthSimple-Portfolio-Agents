import unittest
from unittest.mock import patch

import ticker_mapping


class TickerMappingInteractionTest(unittest.TestCase):
    def test_interactive_pending_mapping_requires_confirmation(self):
        pending = [{
            "source_symbol": "XNDU", "detected_currency": "CAD",
            "trade_count": 1, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "", "TSX", "n"])
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping") as add_mapping,
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        add_mapping.assert_not_called()
        self.assertEqual(result, [{"source_symbol": "XNDU", "status": "skipped"}])

    def test_interactive_pending_mapping_uses_detected_defaults(self):
        pending = [{
            "source_symbol": "MDA", "detected_currency": "CAD",
            "trade_count": 2, "first_seen": None, "last_seen": None,
        }]
        answers = iter(["", "", "", "TSX", "yes"])
        expected = {"source_symbol": "MDA", "status": "verified"}
        with (
            patch.object(ticker_mapping, "list_pending", return_value=pending),
            patch.object(ticker_mapping, "add_mapping", return_value=expected) as add_mapping,
            patch("builtins.input", side_effect=lambda *_: next(answers)),
        ):
            result = ticker_mapping.resolve_pending_interactively("portfolio.duckdb")

        add_mapping.assert_called_once_with(
            "MDA", "MDA", "MDA.TO", "CAD", "TSX",
            reason="resolved pending email ticker", created_by="interactive-cli",
            db_path="portfolio.duckdb",
        )
        self.assertEqual(result, [expected])


if __name__ == "__main__":
    unittest.main()
