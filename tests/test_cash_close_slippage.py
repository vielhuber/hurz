from datetime import datetime
from unittest import TestCase

import scripts.cash_close_slippage as experiment


class CashCloseSlippageTest(TestCase):
    def test_the_us30_bar_ending_at_the_new_york_close_is_a_cash_close_entry(self):
        self.assertTrue(experiment.at_cash_close("US30", "momentum", datetime(2026, 10, 16, 19)))
        self.assertFalse(experiment.at_cash_close("US30", "momentum", datetime(2026, 10, 16, 17)))
        # in winter New York closes at 21:00 UTC
        self.assertTrue(experiment.at_cash_close("US500", "momentum", datetime(2026, 1, 15, 20)))

    def test_the_bar_containing_a_half_hour_close_counts_and_4h_bars_use_their_end(self):
        self.assertTrue(experiment.at_cash_close("DE40", "momentum", datetime(2026, 10, 16, 15)))
        self.assertTrue(experiment.at_cash_close("HK50", "turtle_breakout_4h", datetime(2026, 10, 16, 4)))
        self.assertFalse(experiment.at_cash_close("EURUSD", "momentum", datetime(2026, 10, 16, 19)))

    def test_a_short_filled_below_its_signal_price_slips_against_the_trade(self):
        self.assertAlmostEqual(-39.5 / 545.94855,
                               experiment.slippage_r(-1, 51995.1, 51955.6, 52541.04855), places=6)
