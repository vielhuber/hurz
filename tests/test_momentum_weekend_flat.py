from unittest import TestCase

import numpy as np

import scripts.momentum_weekend_flat as experiment
from scripts.flat_before_weekend import book_flat


class MomentumWeekendFlatTest(TestCase):
    def test_a_trade_is_closed_at_the_last_bar_before_a_weekend_gap(self):
        ts = np.array(["2026-10-02T19", "2026-10-02T20", "2026-10-04T22", "2026-10-04T23"], dtype="datetime64[h]")
        C = np.array([100.0, 100.4, 99.0, 98.0])
        r, exit_bar, forced = book_flat(C, C + 0.1, C - 0.1, C, ts, 0, 1, 100.0, 2.0, 0.0, len(C),
                                        experiment.wff.MAX_GAP)
        self.assertEqual((1, True), (exit_bar, forced))
        self.assertAlmostEqual(0.2, r)

    def test_only_the_chosen_strategy_is_rebooked_and_the_list_is_restored(self):
        before = experiment.wff.STRATS
        self.assertEqual([], experiment.with_weekend_flat({}, 3.0, {}, "momentum"))
        self.assertIs(before, experiment.wff.STRATS)
