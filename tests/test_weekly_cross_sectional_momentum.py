from unittest import TestCase

import numpy as np
import pandas as pd

import scripts.weekly_cross_sectional_momentum as experiment


class WeeklyCrossSectionalMomentumTest(TestCase):
    def test_the_two_strongest_go_long_and_the_two_weakest_short(self):
        ranked = {"A": (0, 0.05), "B": (0, -0.03), "C": (0, 0.01), "D": (0, 0.09), "E": (0, -0.08)}
        self.assertEqual([("A", 1), ("D", 1), ("E", -1), ("B", -1)], experiment.picks(ranked))

    def test_fewer_than_four_instruments_give_no_picks(self):
        self.assertEqual([], experiment.picks({"A": (0, 0.1), "B": (0, 0.2), "C": (0, 0.3)}))

    def test_the_return_is_taken_at_the_friday_close_against_20_closes_earlier(self):
        stamps = pd.date_range("2026-01-01T20:00", periods=40, freq="D")
        closes = np.arange(100.0, 140.0)
        weeks = experiment.friday_returns({"US500": pd.DataFrame({"timestamp": stamps, "close": closes})})
        friday = int(np.flatnonzero(stamps.weekday == 4)[-1])
        index, change = weeks[np.datetime64(stamps[friday], "W")]["US500"]
        self.assertEqual(friday, index)
        self.assertAlmostEqual(closes[friday] / closes[friday - 20] - 1.0, change)
