from datetime import date
from unittest import TestCase

import scripts.european_pre_holiday_book as experiment


class EuropeanPreHolidayBookTest(TestCase):
    def test_each_index_reads_its_own_exchange_calendar(self):
        calendars = experiment.european_calendars()
        self.assertIn(date(2025, 12, 24), calendars["DE40"])
        self.assertIs(calendars["DE40"], calendars["EU50"])
        self.assertIn(date(2025, 5, 1), calendars["FR40"])
        self.assertIn(date(2025, 8, 25), calendars["UK100"])
        self.assertNotIn(date(2025, 8, 25), calendars["DE40"])

