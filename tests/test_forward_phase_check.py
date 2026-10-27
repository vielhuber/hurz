from datetime import date
import unittest

import numpy as np

from scripts import forward_phase_check as check


class ForwardPhaseCheckTest(unittest.TestCase):
    def test_days_without_a_close_count_zero_and_late_closes_are_outside(self):
        series = check.daily_series([(date(2026, 10, 14), 0.87), (date(2026, 10, 14), 0.1), (date(2026, 11, 1), 5.0)],
                                    date(2026, 10, 13), date(2026, 10, 15))
        self.assertEqual([0.0, 0.97, 0.0], [round(v, 2) for v in series])

    def test_the_verdict_needs_the_minimum_and_an_interval_above_zero(self):
        self.assertEqual("minimum met", check.verdict(1.2, 0.1, 2.0))
        self.assertEqual("consistent with the reference", check.verdict(1.2, -0.5, 2.0))
        self.assertEqual("below the reference", check.verdict(-0.3, -0.6, 0.01))

    def test_a_window_too_short_or_without_a_close_is_not_judged(self):
        self.assertEqual("too short", check.verdict(-0.3, -0.6, 0.01, days=7))
        self.assertEqual("too short", check.verdict(0.0, 0.0, 0.0, days=30, closes=0))

    def test_a_short_window_still_gets_an_interval(self):
        low, high = check.block_interval(np.array([0.0, 1.0, -0.5]), 1, draws=200)
        self.assertLessEqual(low, high)


if __name__ == "__main__":
    unittest.main()
