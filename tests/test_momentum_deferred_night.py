from unittest import TestCase

import numpy as np

import scripts.momentum_deferred_night as experiment

TS = np.arange(np.datetime64("2026-10-06T22"), np.datetime64("2026-10-07T12"), np.timedelta64(1, "h"))


class MomentumDeferredNightTest(TestCase):
    def test_a_night_long_moves_to_the_first_bar_from_seven_utc(self):
        fast, slow = np.full(len(TS), 101.0), np.full(len(TS), 100.0)
        b = experiment.session_bar(TS, 0, fast, slow, 1)
        self.assertEqual(np.datetime64("2026-10-07T07"), TS[b])

    def test_a_cross_undone_by_the_morning_drops_the_signal(self):
        fast, slow = np.full(len(TS), 99.0), np.full(len(TS), 100.0)
        self.assertIsNone(experiment.session_bar(TS, 0, fast, slow, 1))

    def test_no_session_bar_before_the_data_ends_drops_the_signal(self):
        fast, slow = np.full(len(TS), 101.0), np.full(len(TS), 100.0)
        self.assertIsNone(experiment.session_bar(TS[:5], 0, fast, slow, 1))
