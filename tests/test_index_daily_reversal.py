from unittest import TestCase

import numpy as np

import scripts.index_daily_reversal as experiment


class IndexDailyReversalTest(TestCase):
    def test_rsi2_reads_low_after_two_down_closes(self):
        rsi = experiment.rsi2(np.array([100.0, 101.0, 102.0, 101.0, 99.0]))
        self.assertTrue(np.isnan(rsi[1]))
        self.assertLess(rsi[4], 20.0)
        self.assertGreater(rsi[2], 90.0)

    def test_a_dip_in_an_uptrend_enters_and_exits_above_the_short_average(self):
        n = 230
        closes = np.linspace(100.0, 160.0, n)
        closes[210], closes[211] = 150.0, 148.0
        closes[212] = 158.0
        H = closes + 0.5; L = closes - 0.5
        trades = experiment.reversal_trades(closes.copy(), H, L, closes, np.arange(n), lambda e, atr: 5.0)
        self.assertEqual(210, trades[0][0])
        self.assertEqual(212, trades[0][1])
        self.assertAlmostEqual((158.0 - 150.0) / 5.0, trades[0][2])

    def test_no_entry_below_the_long_average(self):
        n = 230
        closes = np.linspace(160.0, 100.0, n)
        H = closes + 0.5; L = closes - 0.5
        self.assertEqual([], experiment.reversal_trades(closes.copy(), H, L, closes, np.arange(n),
                                                         lambda e, atr: 5.0))
