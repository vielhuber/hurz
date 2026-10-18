from unittest import TestCase

import numpy as np

import scripts.index_daily_breakout as experiment


def series(closes):
    C = np.array(closes, dtype=float)
    return np.arange(len(C)), C.copy(), C + 0.01, C - 0.01, C


class IndexDailyBreakoutTest(TestCase):
    def test_a_new_high_enters_long_and_a_ten_session_low_exits(self):
        closes = [100.0] * 20 + [101.0, 102.0, 103.0] + [102.0] * 10 + [95.0, 96.0]
        ts, O, H, L, C = series(closes)
        last = np.arange(len(C))
        trades = experiment.breakout_trades(ts, O, H, L, C, last, lambda e, d: (C[e], 50.0, 0.0))
        self.assertEqual(20, trades[0][0])
        self.assertEqual(1, trades[0][2])
        self.assertEqual(33, trades[0][1])
        self.assertAlmostEqual((95.0 - 101.0) / 50.0, trades[0][3])

    def test_a_gap_through_the_stop_exits_at_the_open(self):
        closes = [100.0] * 20 + [101.0, 99.0, 98.0]
        ts, O, H, L, C = series(closes)
        trades = experiment.breakout_trades(ts, O, H, L, C, np.arange(len(C)), lambda e, d: (C[e], 1.5, 0.1))
        self.assertEqual((20, 21, 1), trades[0][:3])
        self.assertAlmostEqual((99.0 - 101.0) / 1.5 - 0.1, trades[0][3])

    def test_no_signal_inside_the_channel(self):
        ts, O, H, L, C = series([100.0, 101.0] * 15)
        self.assertEqual([], experiment.breakout_trades(ts, O, H, L, C, np.arange(len(C)),
                                                         lambda e, d: (C[e], 1.0, 0.0)))
