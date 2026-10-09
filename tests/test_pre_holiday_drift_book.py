from datetime import date
from unittest import TestCase

import numpy as np

import scripts.pre_holiday_drift_book as experiment


class PreHolidayDriftBookTest(TestCase):
    def test_a_monday_holiday_trades_from_thursday_into_friday(self):
        self.assertEqual([(date(2026, 9, 3), date(2026, 9, 4))], experiment.sessions([date(2026, 9, 7)]))

    def test_adjacent_holidays_share_one_pre_holiday_session(self):
        self.assertEqual([(date(2026, 12, 22), date(2026, 12, 23))],
                         experiment.sessions([date(2026, 12, 24), date(2026, 12, 25)]))

    def test_the_decision_bar_is_the_twenty_utc_bar_or_the_last_before_it_that_day(self):
        ts = np.array(["2026-09-03T18", "2026-09-03T19", "2026-09-03T21", "2026-09-04T20"], dtype="datetime64[h]")
        self.assertEqual(1, experiment.decision_bar(ts, date(2026, 9, 3)))
        self.assertEqual(3, experiment.decision_bar(ts, date(2026, 9, 4)))
        self.assertIsNone(experiment.decision_bar(ts, date(2026, 9, 5)))
