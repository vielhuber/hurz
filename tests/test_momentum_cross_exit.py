from unittest import TestCase

import numpy as np

import scripts.momentum_cross_exit as experiment


class MomentumCrossExitTest(TestCase):
    def book(self, close, fast, slow, d=1):
        close = np.asarray(close, dtype=float)
        n = len(close)
        return experiment.book_cross(close, close, close, close, np.asarray(fast, float), np.asarray(slow, float),
                                     0, d, 100.0, 2.0, 0.05, n)

    def test_a_long_closes_at_the_first_close_below_the_slow_ema(self):
        r, bar, crossed = self.book([100, 101, 100.5, 99.5, 99], [1, 1, 1, -1, -1], [0, 0, 0, 0, 0])
        self.assertEqual(3, bar)
        self.assertTrue(crossed)
        self.assertAlmostEqual((99.5 - 100) / 2.0 - 0.05, r)

    def test_a_gap_through_the_stop_keeps_priority_on_the_crossing_bar(self):
        r, bar, crossed = self.book([100, 101, 97.5, 97], [1, 1, -1, -1], [0, 0, 0, 0])
        self.assertEqual((2, False), (bar, crossed))
        self.assertAlmostEqual((97.5 - 100) / 2.0 - 0.05, r)

    def test_a_short_holds_while_the_fast_ema_stays_below(self):
        closes = [100] * 30
        r, bar, crossed = self.book(closes, [-1] * 30, [0] * 30, d=-1)
        self.assertEqual((24, False), (bar, crossed))
