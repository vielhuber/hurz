from unittest import TestCase

import numpy as np

import scripts.momentum_short_leash as experiment

ews = experiment.ews


class MomentumShortLeashTest(TestCase):
    def test_a_flat_trade_leaves_at_the_close_of_the_twelfth_bar(self):
        C = np.full(30, 100.0)
        hold = ews.HOLD
        try:
            ews.HOLD = experiment.LEASH
            r, exit_bar = ews.book(C, C + 0.1, C - 0.1, C, 0, 1, 100.0, 1.0, 0.0, len(C))
        finally:
            ews.HOLD = hold
        self.assertEqual((0.0, 12), (r, exit_bar))

    def test_the_leash_and_strategy_list_are_restored(self):
        before = (ews.STRATS, ews.HOLD)
        self.assertEqual([], experiment.with_leash({}, 3.0, {}, "momentum", experiment.LEASH))
        self.assertEqual(before, (ews.STRATS, ews.HOLD))
