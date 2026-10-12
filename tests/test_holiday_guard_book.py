from unittest import TestCase

import numpy as np

import scripts.holiday_guard_book as experiment


def signal(ts):
    return {"ts": np.datetime64(ts, "h"), "strat": "momentum", "pair": "US30"}


class HolidayGuardBookTest(TestCase):
    def test_signals_on_a_guarded_holiday_in_berlin_time_are_refused(self):
        columbus_day = signal("2026-10-12T10")
        evening_before_in_utc = signal("2026-10-11T22")
        ordinary_day = signal("2026-10-13T10")
        self.assertEqual([ordinary_day],
                         experiment.guarded([columbus_day, evening_before_in_utc, ordinary_day]))
        self.assertEqual([], experiment.guarded([]))
