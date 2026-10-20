from unittest import TestCase

import numpy as np

import scripts.weekend_gap_fade as experiment


def week():
    before = np.arange(np.datetime64("2026-10-16T18"), np.datetime64("2026-10-16T21"), np.timedelta64(1, "h"))
    after = np.arange(np.datetime64("2026-10-18T22"), np.datetime64("2026-10-19T01"), np.timedelta64(1, "h"))
    return np.concatenate([before, after]).astype("datetime64[ns]")


class WeekendGapFadeTest(TestCase):
    def test_a_wide_gap_up_is_faded_short(self):
        ts = week()
        O = np.array([100, 100, 100, 101, 100.5, 100.5]); C = np.array([100, 100, 100, 100.6, 100.5, 100.5])
        self.assertEqual([(3, -1)], experiment.reopen_bars(ts, O, C, np.full(6, 1.0)))

    def test_a_small_gap_is_ignored(self):
        ts = week()
        O = np.array([100, 100, 100, 100.3, 100, 100.0]); C = np.full(6, 100.0)
        self.assertEqual([], experiment.reopen_bars(ts, O, C, np.full(6, 1.0)))

    def test_the_fade_books_stop_first_then_close(self):
        O = np.array([0, 101.0]); C = np.array([0, 100.6])
        self.assertAlmostEqual(0.4 - 0.1, experiment.fade(O, np.array([0, 101.2]), np.array([0, 100.5]), C,
                                                          1, -1, 1.0, 0.1))
        self.assertAlmostEqual(-1.0, experiment.fade(O, np.array([0, 102.1]), np.array([0, 99.0]), C,
                                                     1, -1, 1.0, 0.0))
