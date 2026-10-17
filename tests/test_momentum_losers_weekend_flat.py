from unittest import TestCase

import numpy as np

import scripts.momentum_losers_weekend_flat as experiment


def bars(closes):
    ts = np.array([np.datetime64("2026-10-16T19"), np.datetime64("2026-10-16T20"),
                   np.datetime64("2026-10-18T22"), np.datetime64("2026-10-18T23")]).astype("datetime64[ns]")
    C = np.array(closes, dtype=float)
    return C.copy(), C + 0.1, C - 0.1, C, ts


class MomentumLosersWeekendFlatTest(TestCase):
    def test_a_losing_long_is_closed_before_the_weekend_gap(self):
        O, H, L, C, ts = bars([100.0, 99.5, 99.0, 99.0])
        r, exit_bar, forced = experiment.book_losers_flat(O, H, L, C, ts, 0, 1, 100.0, 2.0, 0.0, 4)
        self.assertEqual((1, True), (exit_bar, forced))
        self.assertAlmostEqual(-0.25, r)

    def test_a_winning_long_is_held_over_the_weekend(self):
        O, H, L, C, ts = bars([100.0, 100.5, 101.0, 101.0])
        _, exit_bar, forced = experiment.book_losers_flat(O, H, L, C, ts, 0, 1, 100.0, 2.0, 0.0, 4)
        self.assertFalse(forced)
        self.assertNotEqual(1, exit_bar)

    def test_a_losing_short_is_closed_too(self):
        O, H, L, C, ts = bars([100.0, 100.5, 101.0, 101.0])
        r, exit_bar, forced = experiment.book_losers_flat(O, H, L, C, ts, 0, -1, 100.0, 2.0, 0.0, 4)
        self.assertEqual((1, True), (exit_bar, forced))
        self.assertAlmostEqual(-0.25, r)
