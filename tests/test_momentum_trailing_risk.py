from unittest import TestCase

import numpy as np

import scripts.momentum_trailing_risk as experiment


class MomentumTrailingRiskTest(TestCase):
    def test_the_factor_runs_from_half_at_zero_to_full_at_a_fifth_of_r(self):
        self.assertEqual(0.5, experiment.risk_factor(-0.3))
        self.assertEqual(0.5, experiment.risk_factor(0.0))
        self.assertAlmostEqual(0.75, experiment.risk_factor(0.1))
        self.assertEqual(1.0, experiment.risk_factor(0.5))
        self.assertEqual(1.0, experiment.risk_factor(None))

    def test_only_momentum_combinations_with_ten_trailing_trades_get_an_expectancy(self):
        ts = np.datetime64("2026-01-01T00")
        trades = [{"strat": "momentum", "pair": "US30", "ts": ts + np.timedelta64(30 * i, "h"),
                   "exit_ts": ts + np.timedelta64(30 * i + 5, "h"), "r": 0.1} for i in range(10)]
        trades += [{"strat": "momentum", "pair": "GOLD", "ts": ts + np.timedelta64(30 * i, "h"),
                    "exit_ts": ts + np.timedelta64(30 * i + 5, "h"), "r": 0.1} for i in range(9)]
        self.assertEqual({"US30": 0.1}, {k: round(v, 6) for k, v in experiment.trailing_expectancy(trades).items()})
