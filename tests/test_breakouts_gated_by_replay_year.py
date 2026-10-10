from unittest import TestCase

import numpy as np

import scripts.breakouts_gated_by_replay_year as experiment

TS = np.datetime64("2026-01-01T00")


def trades(strategy, r):
    return [{"strat": strategy, "pair": "GOLD", "ts": TS + np.timedelta64(10 * i, "h"),
             "exit_ts": TS + np.timedelta64(10 * i + 5, "h"), "r": r} for i in range(5)]


class BreakoutsGatedByReplayYearTest(TestCase):
    def test_only_a_vetoed_breakout_with_a_positive_year_is_readmitted(self):
        training = trades("donchian_breakout", 0.2) + trades("turtle_breakout", -0.1)
        vetoed = {"donchian_breakout", "turtle_breakout", "rsi_mr"}
        self.assertEqual({"donchian_breakout"}, experiment.readmitted(training, vetoed))

    def test_a_breakout_that_is_not_vetoed_is_left_alone(self):
        self.assertEqual(set(), experiment.readmitted(trades("donchian_breakout", 0.2), {"turtle_breakout"}))
