from unittest import TestCase

import numpy as np

import scripts.momentum_trade_floor as experiment
from scripts.burst_cost_priority import active_order

TS = np.datetime64("2026-01-01T00")


def window(count, r):
    return [{"strat": "momentum", "pair": "DE40", "ts": TS + np.timedelta64(30 * i, "h"),
             "exit_ts": TS + np.timedelta64(30 * i + 5, "h"), "r": r} for i in range(count)]


class MomentumTradeFloorTest(TestCase):
    def test_six_trailing_trades_list_a_combination_only_under_the_lower_floor(self):
        rank = lambda: active_order(window(6, 0.3), set(), set(), [])
        self.assertEqual({}, rank())
        self.assertEqual({("momentum", "DE40"): 0}, experiment.with_floor(experiment.FLOOR, rank))

    def test_the_floor_is_restored_after_the_run(self):
        before = experiment.pe.MIN_N
        experiment.with_floor(experiment.FLOOR, lambda: None)
        self.assertEqual(before, experiment.pe.MIN_N)
