from datetime import datetime, timezone
from unittest import TestCase

from app.spot_trading.session_close import is_daily_close, last_bar_hour

# Capital.com openingHours as served on 2026-10-04.
US500 = {"mon": ["00:00 - 21:00", "21:05 - 00:00"], "fri": ["00:00 - 21:00"], "sat": [],
         "sun": ["22:00 - 00:00"], "zone": "UTC"}
EURUSD = {"thu": ["00:00 - 20:59:50", "21:05 - 00:00"], "fri": ["00:00 - 20:59:50"], "zone": "UTC"}
BTCUSD = {"sat": ["00:00 - 05:00", "07:00 - 21:00", "21:05 - 00:00"], "zone": "UTC"}


class SessionCloseTest(TestCase):
    def test_a_day_trading_into_midnight_closes_with_the_23_utc_bar(self):
        self.assertEqual(23, last_bar_hour(US500, "mon"))
        self.assertEqual(23, last_bar_hour(US500, "sun"))
        self.assertEqual(23, last_bar_hour(BTCUSD, "sat"))

    def test_friday_closes_with_the_bar_that_ends_at_the_session_end(self):
        self.assertEqual(20, last_bar_hour(US500, "fri"))
        self.assertEqual(20, last_bar_hour(EURUSD, "fri"))

    def test_a_closed_day_has_no_daily_close(self):
        self.assertIsNone(last_bar_hour(US500, "sat"))
        self.assertIsNone(last_bar_hour(US500, "tue"))

    def test_a_bar_is_the_daily_close_only_at_that_hour(self):
        friday = datetime(2026, 10, 2, 20, tzinfo=timezone.utc)
        self.assertTrue(is_daily_close(friday, US500))
        self.assertFalse(is_daily_close(friday.replace(hour=19), US500))
        self.assertFalse(is_daily_close(datetime(2026, 10, 1, 20, tzinfo=timezone.utc), EURUSD))
