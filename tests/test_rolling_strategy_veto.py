from unittest import TestCase

import numpy as np

import scripts.rolling_strategy_veto as experiment

CUT = np.datetime64("2026-06-01T00")


def trades(strategy, count, usd, days_before):
    exit_ts = CUT - np.timedelta64(days_before, "D")
    return [{"strat": strategy, "exit_ts": exit_ts, "usd": usd, "risk": 3.0}] * count


class RollingStrategyVetoTest(TestCase):
    def test_a_strategy_losing_more_than_a_tenth_of_its_risk_in_the_window_is_retired(self):
        self.assertEqual({"turtle_breakout"}, experiment.trailing_veto(trades("turtle_breakout", 25, -0.4, 10), CUT))

    def test_fewer_than_25_closes_never_retire(self):
        self.assertEqual(set(), experiment.trailing_veto(trades("turtle_breakout", 24, -3.0, 10), CUT))

    def test_losses_older_than_90_days_have_left_the_window(self):
        self.assertEqual(set(), experiment.trailing_veto(trades("turtle_breakout", 40, -3.0, 91), CUT))

    def test_a_small_loss_stays_above_the_threshold(self):
        self.assertEqual(set(), experiment.trailing_veto(trades("donchian_breakout", 30, -0.2, 5), CUT))
