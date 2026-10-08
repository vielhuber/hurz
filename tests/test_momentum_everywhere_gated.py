from unittest import TestCase

import numpy as np

import scripts.momentum_everywhere_gated as experiment

TS = np.datetime64("2026-01-01T00")


def trade(pair, start, held, r, strategy="momentum"):
    return {"strat": strategy, "pair": pair, "ts": TS + np.timedelta64(start, "h"),
            "exit_ts": TS + np.timedelta64(start + held, "h"), "r": r}


class MomentumEverywhereGatedTest(TestCase):
    def test_the_gate_pools_one_position_per_combination_across_instruments(self):
        training = [trade("US30", 0, 10, 0.6), trade("US30", 5, 2, -1.0), trade("DE40", 3, 4, -0.2),
                    trade("GOLD", 1, 3, -1.0, "turtle_breakout")]
        self.assertAlmostEqual(0.2, experiment.pooled_expectancy(training, "momentum"))

    def test_no_trailing_momentum_trade_keeps_the_gate_closed(self):
        self.assertIsNone(experiment.pooled_expectancy([trade("GOLD", 0, 3, 1.0, "turtle_breakout")], "momentum"))
