import unittest

from portfolio_metrics import (
    calculate_adjusted_daily_returns,
    calculate_adjusted_sharpe_ratio,
    calculate_adjusted_volatility,
    calculate_benchmark_stats,
    calculate_concentration,
    calculate_drawdown_details,
    calculate_max_drawdown,
    calculate_position_weights,
    calculate_rebalance_drift,
    calculate_sortino_ratio,
    calculate_total_return,
    calculate_twr_total_return,
    calculate_weighted_mer,
    build_wealth_index,
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


class AdjustedReturnsTest(unittest.TestCase):
    def test_contribution_is_excluded_from_return(self):
        # Day 2: value jumps from 100 to 150 purely because of a $50 deposit.
        historical_values = [
            {"date": "2025-01-01", "portfolio_value": 100},
            {"date": "2025-01-02", "portfolio_value": 150},
        ]
        flows = [{"date": "2025-01-02", "amount": 50}]
        result = calculate_adjusted_daily_returns(historical_values, flows)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["return"], 0.0)

    def test_return_without_flow_reflects_price_move(self):
        historical_values = [
            {"date": "2025-01-01", "portfolio_value": 100},
            {"date": "2025-01-02", "portfolio_value": 110},
        ]
        result = calculate_adjusted_daily_returns(historical_values, [])
        self.assertAlmostEqual(result[0]["return"], 0.1)

    def test_skips_rows_after_zero_or_negative_prior_value(self):
        historical_values = [
            {"date": "2025-01-01", "portfolio_value": 0},
            {"date": "2025-01-02", "portfolio_value": 100},
        ]
        result = calculate_adjusted_daily_returns(historical_values, [])
        self.assertEqual(result, [])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(calculate_adjusted_daily_returns([], []), [])
        self.assertEqual(calculate_adjusted_daily_returns([{"date": "2025-01-01", "portfolio_value": 100}], []), [])


class WealthIndexAndTwrTest(unittest.TestCase):
    def test_wealth_index_compounds_returns(self):
        returns = [
            {"date": "2025-01-02", "return": 0.1},
            {"date": "2025-01-03", "return": -0.05},
        ]
        index = build_wealth_index(returns)
        self.assertAlmostEqual(index[0]["index"], 1.1)
        self.assertAlmostEqual(index[1]["index"], 1.1 * 0.95)

    def test_twr_total_return_unavailable_when_empty(self):
        result = calculate_twr_total_return([])
        self.assertFalse(result["available"])

    def test_twr_total_return_links_geometrically(self):
        returns = [
            {"date": "2025-01-02", "return": 0.1},
            {"date": "2025-01-03", "return": 0.1},
        ]
        result = calculate_twr_total_return(returns)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["total_return"], 1.1 * 1.1 - 1.0)
        self.assertIsNone(result["annualized_return"])  # span < 365 days


class AdjustedVolatilitySharpeSortinoTest(unittest.TestCase):
    def test_volatility_unavailable_when_empty(self):
        result = calculate_adjusted_volatility([])
        self.assertFalse(result["available"])

    def test_sharpe_unavailable_on_zero_volatility(self):
        returns = [{"date": "2025-01-02", "return": 0.0}, {"date": "2025-01-03", "return": 0.0}]
        result = calculate_adjusted_sharpe_ratio(returns)
        self.assertFalse(result["available"])

    def test_sortino_unavailable_when_no_downside(self):
        returns = [{"date": "2025-01-02", "return": 0.05}, {"date": "2025-01-03", "return": 0.03}]
        result = calculate_sortino_ratio(returns)
        self.assertFalse(result["available"])

    def test_sortino_computes_with_downside_periods(self):
        returns = [
            {"date": "2025-01-02", "return": 0.05},
            {"date": "2025-01-03", "return": -0.03},
            {"date": "2025-01-04", "return": 0.02},
        ]
        result = calculate_sortino_ratio(returns)
        self.assertTrue(result["available"])
        self.assertIsInstance(result["sortino_ratio"], float)


class DrawdownDetailsTest(unittest.TestCase):
    def test_unavailable_on_empty_wealth_index(self):
        result = calculate_drawdown_details([])
        self.assertFalse(result["available"])

    def test_reports_peak_trough_and_recovery(self):
        wealth_index = [
            {"date": "2025-01-01", "index": 1.0},
            {"date": "2025-01-02", "index": 0.8},
            {"date": "2025-01-03", "index": 0.9},
            {"date": "2025-01-04", "index": 1.05},
        ]
        result = calculate_drawdown_details(wealth_index)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["max_drawdown"], -0.2)
        self.assertEqual(result["trough_date"], "2025-01-02")
        self.assertEqual(result["recovery_date"], "2025-01-04")
        self.assertEqual(result["days_to_recover"], 2)

    def test_no_recovery_when_never_reaches_prior_peak(self):
        wealth_index = [
            {"date": "2025-01-01", "index": 1.0},
            {"date": "2025-01-02", "index": 0.7},
        ]
        result = calculate_drawdown_details(wealth_index)
        self.assertIsNone(result["recovery_date"])
        self.assertIsNone(result["days_to_recover"])


class ConcentrationTest(unittest.TestCase):
    def test_unavailable_when_no_holdings(self):
        result = calculate_concentration({})
        self.assertFalse(result["available"])

    def test_top_n_and_hhi(self):
        weights = {
            "A": {"weight": 0.5},
            "B": {"weight": 0.3},
            "C": {"weight": 0.2},
        }
        result = calculate_concentration(weights)
        self.assertAlmostEqual(result["top_1"], 0.5)
        self.assertAlmostEqual(result["top_5"], 1.0)
        self.assertAlmostEqual(result["hhi"], 0.25 + 0.09 + 0.04)
        self.assertEqual(result["max_single_name_ticker"], "A")
        self.assertAlmostEqual(result["max_single_name_weight"], 0.5)


class BenchmarkStatsTest(unittest.TestCase):
    def test_none_when_overlap_below_minimum(self):
        portfolio_returns = [{"date": f"2025-01-{i:02d}", "return": 0.01} for i in range(1, 5)]
        benchmark_returns = [{"date": f"2025-01-{i:02d}", "return": 0.01} for i in range(1, 5)]
        result = calculate_benchmark_stats(portfolio_returns, benchmark_returns, minimum_overlap=20)
        self.assertIsNone(result)

    def test_computes_stats_with_sufficient_overlap(self):
        dates = [f"2025-01-{i:02d}" for i in range(1, 25)]
        portfolio_returns = [{"date": d, "return": 0.01} for d in dates]
        benchmark_returns = [{"date": d, "return": 0.008} for d in dates]
        result = calculate_benchmark_stats(portfolio_returns, benchmark_returns, minimum_overlap=20)
        self.assertIsNotNone(result)
        self.assertEqual(result["overlap_days"], 24)


class RebalanceDriftTest(unittest.TestCase):
    def test_unavailable_without_targets(self):
        result = calculate_rebalance_drift({"Core": 0.6}, None)
        self.assertFalse(result["available"])

    def test_flags_rebalance_needed_outside_band(self):
        targets = {
            "Core": {"target_percent": 60, "min_percent": 55, "max_percent": 70},
        }
        result = calculate_rebalance_drift({"Core": 0.80}, targets)
        core = next(row for row in result["groups"] if row["group"] == "Core")
        self.assertTrue(core["rebalance_needed"])
        self.assertAlmostEqual(core["drift_pp"], 20.0)

    def test_within_band_does_not_need_rebalance(self):
        targets = {
            "Core": {"target_percent": 60, "min_percent": 55, "max_percent": 70},
        }
        result = calculate_rebalance_drift({"Core": 0.60}, targets)
        core = next(row for row in result["groups"] if row["group"] == "Core")
        self.assertFalse(core["rebalance_needed"])

    def test_null_target_never_needs_rebalance(self):
        targets = {"Cash": {"target_percent": None, "min_percent": 0, "max_percent": None}}
        result = calculate_rebalance_drift({"Cash": 0.5}, targets)
        cash = next(row for row in result["groups"] if row["group"] == "Cash")
        self.assertFalse(cash["rebalance_needed"])
        self.assertIsNone(cash["drift_pp"])


class WeightedMerTest(unittest.TestCase):
    def test_unavailable_without_expense_ratios(self):
        result = calculate_weighted_mer([{"market_value": 100, "expense_ratio": None}])
        self.assertFalse(result["available"])

    def test_computes_weighted_average_with_partial_coverage(self):
        holdings = [
            {"market_value": 100, "expense_ratio": 0.002},
            {"market_value": 100, "expense_ratio": 0.004},
            {"market_value": 50, "expense_ratio": None},
        ]
        result = calculate_weighted_mer(holdings)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["weighted_mer"], 0.003)
        self.assertAlmostEqual(result["coverage_percent"], 200 / 250)


if __name__ == "__main__":
    unittest.main()
