from unittest import TestCase

import numpy as np

import scripts.efficiency_weighted_selection as ews
import scripts.momentum_profit_lock as experiment
import scripts.profit_lock as profit_lock


def path(highs, lows, closes):
    n = len(closes)
    return np.r_[100.0, closes[:-1]], np.array(highs), np.array(lows), np.array(closes), n


class MomentumProfitLockTest(TestCase):
    def test_a_long_that_reached_one_r_and_fell_back_books_the_locked_half_r(self):
        O, H, L, C, n = path([100, 101.1, 100.9, 100.6], [100, 100.2, 100.4, 100.3], [100, 100.8, 100.6, 100.4])
        r, exit_bar = profit_lock.book(O, H, L, C, 0, 1, 100.0, 1.0, 0.0, n, experiment.LOCK)
        self.assertAlmostEqual(0.5, r)
        self.assertEqual(2, exit_bar)

    def test_without_the_lock_the_same_trade_runs_to_the_leash_or_the_end(self):
        O, H, L, C, n = path([100, 101.1, 100.9, 100.6], [100, 100.2, 100.4, 100.3], [100, 100.8, 100.6, 100.4])
        self.assertEqual(ews.book(O, H, L, C, 0, 1, 100.0, 1.0, 0.0, n),
                         profit_lock.book(O, H, L, C, 0, 1, 100.0, 1.0, 0.0, n, None))
