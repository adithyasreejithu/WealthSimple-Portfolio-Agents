"""Tests for the per-security technicals module.

Fixtures use small, exactly reproducible price series (either literal values
or a deterministic construction, e.g. "ticker return = 2x benchmark return
every day") so the expected beta/drawdown/MA/volatility values are derivable
independently of the function under test, per Phase 2's gate condition.
"""

import unittest
from datetime import date, timedelta

import pandas as pd

from security_technicals import (
    annualized_volatility,
    beta_alpha,
    compute_security_technicals,
    max_drawdown,
    moving_averages,
    relative_strength,
)


def _rows(closes: list[float], start: date = date(2025, 1, 1)) -> list[dict]:
    return [
        {"record_date": (start + timedelta(days=i)).isoformat(), "close": close}
        for i, close in enumerate(closes)
    ]


class MovingAveragesTest(unittest.TestCase):
    def test_windows_shorter_than_history_compute_normally(self):
        result = moving_averages(_rows([10, 20, 30, 40, 50]), windows=(2, 5))
        self.assertAlmostEqual(result["latest_close"], 50.0)
        self.assertAlmostEqual(result["sma_2d"], (40 + 50) / 2)
        self.assertAlmostEqual(result["sma_5d"], (10 + 20 + 30 + 40 + 50) / 5)

    def test_window_longer_than_history_is_none_not_a_short_average(self):
        result = moving_averages(_rows([10, 20, 30]), windows=(2, 200))
        self.assertAlmostEqual(result["sma_2d"], (20 + 30) / 2)
        self.assertIsNone(result["sma_200d"])

    def test_empty_input_returns_all_none(self):
        result = moving_averages([], windows=(50, 200))
        self.assertIsNone(result["latest_close"])
        self.assertIsNone(result["sma_50d"])
        self.assertIsNone(result["sma_200d"])


class MaxDrawdownTest(unittest.TestCase):
    def test_peak_to_trough_decline(self):
        result = max_drawdown(_rows([100, 80, 90]))
        self.assertAlmostEqual(result["max_drawdown"], -0.2)

    def test_monotonic_rise_has_zero_drawdown(self):
        result = max_drawdown(_rows([100, 110, 120]))
        self.assertAlmostEqual(result["max_drawdown"], 0.0)

    def test_empty_input_is_none(self):
        self.assertIsNone(max_drawdown([])["max_drawdown"])


class VolatilityTest(unittest.TestCase):
    def test_matches_hand_computed_population_stdev(self):
        # Closes 100 -> 110 -> 99: daily returns +0.10, -0.10.
        result = annualized_volatility(_rows([100, 110, 99]), periods_per_year=252)
        # ddof=0 population stdev of [0.10, -0.10] around mean 0 is exactly 0.10.
        self.assertAlmostEqual(result["daily_volatility"], 0.10, places=6)
        self.assertAlmostEqual(result["volatility"], 0.10 * (252**0.5), places=6)

    def test_single_price_point_has_no_return_series(self):
        result = annualized_volatility(_rows([100]))
        self.assertIsNone(result["volatility"])
        self.assertIsNone(result["daily_volatility"])


class RelativeStrengthTest(unittest.TestCase):
    def test_excess_return_over_matching_window(self):
        ticker = _rows([100, 150])  # +50%
        benchmark = _rows([100, 120])  # +20%
        result = relative_strength(ticker, benchmark, window_days=1)
        self.assertAlmostEqual(result["ticker_return"], 0.5)
        self.assertAlmostEqual(result["benchmark_return"], 0.2)
        self.assertAlmostEqual(result["excess_return"], 0.3)

    def test_insufficient_history_is_none_not_a_shorter_window(self):
        # Only 2 days of history; a 365-day window has no point at/before cutoff.
        ticker = _rows([100, 150])
        benchmark = _rows([100, 120])
        result = relative_strength(ticker, benchmark, window_days=365)
        self.assertIsNone(result["ticker_return"])
        self.assertIsNone(result["benchmark_return"])
        self.assertIsNone(result["excess_return"])

    def test_empty_benchmark_is_none(self):
        result = relative_strength(_rows([100, 150]), [])
        self.assertIsNone(result["excess_return"])


class BetaAlphaTest(unittest.TestCase):
    def _correlated_rows(self, beta: float, n_days: int = 26):
        """Benchmark alternates +2%/-1% daily; ticker's daily return is
        exactly `beta` times the benchmark's -- by construction,
        cov(rp,rb)/var(rb) reduces to exactly `beta`, and since
        rp.mean() == beta * rb.mean(), alpha (at zero risk-free rate)
        reduces to exactly 0."""
        start = date(2025, 1, 1)
        benchmark_returns = [0.02 if i % 2 == 0 else -0.01 for i in range(n_days - 1)]
        bench_closes = [100.0]
        ticker_closes = [100.0]
        for r in benchmark_returns:
            bench_closes.append(bench_closes[-1] * (1 + r))
            ticker_closes.append(ticker_closes[-1] * (1 + beta * r))
        return _rows(ticker_closes, start), _rows(bench_closes, start)

    def test_beta_and_alpha_match_construction(self):
        # Not exactly 2.0: `calculate_benchmark_stats` (imported unmodified,
        # per design-decisions.md Decision 1) takes `rp.cov(rb) / rb.var(ddof=0)`
        # -- pandas' `.cov()` defaults to ddof=1, while `.var(ddof=0)` is
        # population variance, so the two denominators disagree by n/(n-1).
        # Reproduced here independently of `beta_alpha`/`calculate_benchmark_stats`
        # to prove the delegation is exact, not to paper over the asymmetry.
        beta_construction = 2.0
        benchmark_returns = pd.Series([0.02 if i % 2 == 0 else -0.01 for i in range(25)])
        ticker_returns = benchmark_returns * beta_construction
        expected_beta = float(ticker_returns.cov(benchmark_returns) / benchmark_returns.var(ddof=0))
        expected_alpha = float(
            (ticker_returns.mean() - expected_beta * benchmark_returns.mean()) * 252
        )

        ticker_rows, benchmark_rows = self._correlated_rows(beta=beta_construction)
        result = beta_alpha(ticker_rows, benchmark_rows, minimum_overlap=20)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["beta"], expected_beta, places=6)
        self.assertAlmostEqual(result["alpha"], expected_alpha, places=6)

    def test_below_minimum_overlap_is_none(self):
        ticker_rows, benchmark_rows = self._correlated_rows(beta=1.0, n_days=10)
        result = beta_alpha(ticker_rows, benchmark_rows, minimum_overlap=20)
        self.assertIsNone(result)

    def test_empty_input_is_none(self):
        self.assertIsNone(beta_alpha([], []))


class ComputeSecurityTechnicalsTest(unittest.TestCase):
    def test_aggregates_every_section(self):
        rows = _rows([100, 105, 95, 110, 120])
        benchmark_rows = _rows([100, 102, 101, 103, 104])
        result = compute_security_technicals(rows, benchmark_rows)
        self.assertIn("moving_averages", result)
        self.assertIn("max_drawdown", result)
        self.assertIn("volatility", result)
        self.assertIn("relative_strength", result)
        self.assertIn("beta_alpha", result)

    def test_never_raises_on_empty_input(self):
        result = compute_security_technicals([], [])
        self.assertIsNone(result["max_drawdown"]["max_drawdown"])
        self.assertIsNone(result["beta_alpha"])


if __name__ == "__main__":
    unittest.main()
