from unittest import TestCase

import numpy as np

import scripts.index_session_momentum as experiment


def hourly(start, hours):
    return np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(hours, "h"),
                     np.timedelta64(1, "h")).astype("datetime64[ns]")


class IndexSessionMomentumTest(TestCase):
    def test_new_york_session_in_summer_and_winter_utc(self):
        zone, opens, closes = experiment.SESSIONS["US30"]
        summer = hourly("2026-07-15T00", 24)
        self.assertEqual([(14, 18)], experiment.session_bars(summer, zone, opens, closes))
        winter = hourly("2026-01-14T00", 24)
        self.assertEqual([(15, 19)], experiment.session_bars(winter, zone, opens, closes))

    def test_half_hour_close_keeps_the_last_full_bar(self):
        zone, opens, closes = experiment.SESSIONS["DE40"]
        # 09:00-17:30 Frankfurt in summer: first bar 07:00 UTC, last full bar 14:00 UTC.
        self.assertEqual([(7, 13)], experiment.session_bars(hourly("2026-07-15T00", 24), zone, opens, closes))

    def test_sessions_with_a_missing_bar_and_weekends_are_skipped(self):
        zone, opens, closes = experiment.SESSIONS["UK100"]
        timestamps = np.delete(hourly("2026-07-15T00", 24), 10)
        self.assertEqual([], experiment.session_bars(timestamps, zone, opens, closes))
        self.assertEqual([], experiment.session_bars(hourly("2026-07-18T00", 48), zone, opens, closes))

    def test_last_bar_books_stop_target_or_close(self):
        O = np.array([100.0, 100.0]); C = np.array([100.0, 100.5])
        self.assertAlmostEqual(-1.0, experiment.last_bar(O, np.array([0, 100.6]), np.array([0, 98.9]), C,
                                                         0, 1, 100.0, 1.0, 0.0))
        self.assertAlmostEqual(1.5, experiment.last_bar(O, np.array([0, 101.6]), np.array([0, 99.5]), C,
                                                        0, 1, 100.0, 1.0, 0.0))
        self.assertAlmostEqual(-0.5 - 0.1, experiment.last_bar(O, np.array([0, 100.6]), np.array([0, 99.5]), C,
                                                               0, -1, 100.0, 1.0, 0.1))
