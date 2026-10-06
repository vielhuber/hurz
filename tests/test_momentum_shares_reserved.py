from unittest import TestCase

import numpy as np

import scripts.momentum_shares_reserved  # noqa: F401  (the experiment's imports must resolve)
from scripts.burst_cost_priority import active_order

TS = np.datetime64("2026-01-01T00")


def window(pair, count, r):
    return [{"strat": "momentum", "pair": pair, "ts": TS + np.timedelta64(30 * i, "h"),
             "exit_ts": TS + np.timedelta64(30 * i + 5, "h"), "r": r} for i in range(count)]


class MomentumSharesReservedTest(TestCase):
    def test_a_reserved_instrument_keeps_its_ranked_momentum_out(self):
        self.assertEqual({("momentum", "US30"): 0},
                         active_order(window("HK50", 19, 0.3) + window("US30", 11, 0.2), set(), {"HK50"}, []))

    def test_without_the_reservation_the_momentum_combination_ranks_first(self):
        self.assertEqual({("momentum", "HK50"): 0, ("momentum", "US30"): 1},
                         active_order(window("HK50", 19, 0.3) + window("US30", 11, 0.2), set(), set(), []))
