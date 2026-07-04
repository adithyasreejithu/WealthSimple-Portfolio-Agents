import unittest

from portfolio_metrics import (
    calculate_max_drawdown,
    calculate_position_weights,
    calculate_total_return,
)


class PortfolioMetricsTest(unittest.TestCase):
    def test_position_weights_use_invested_holdings(self):
        result = calculate_position_weights(
            [
                {"ticker_symbol": "A", "market_value": 75},
                {"ticker_symbol": "B", "market_value": 25},
            ]
        )
        self.assertEqual(result["A"]["weight"], 0.75)
        self.assertEqual(result["B"]["weight"], 0.25)

    def test_total_return_sorts_history(self):
        result = calculate_total_return(
            [
                {"date": "2025-01-02", "portfolio_value": 110},
                {"date": "2025-01-01", "portfolio_value": 100},
            ]
        )
        self.assertAlmostEqual(result["total_return"], 0.1)

    def test_max_drawdown_uses_peak_to_trough_decline(self):
        result = calculate_max_drawdown(
            [
                {"date": "2025-01-01", "portfolio_value": 100},
                {"date": "2025-01-02", "portfolio_value": 80},
                {"date": "2025-01-03", "portfolio_value": 90},
            ]
        )
        self.assertAlmostEqual(result["max_drawdown"], -0.2)


if __name__ == "__main__":
    unittest.main()
