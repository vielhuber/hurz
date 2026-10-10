from datetime import date
from unittest import TestCase

import scripts.turn_of_month_book as experiment


class TurnOfMonthBookTest(TestCase):
    def test_trading_days_skip_weekends_and_holidays(self):
        days = experiment.trading_days(date(2026, 8, 28), date(2026, 9, 9), [date(2026, 9, 7)])
        self.assertEqual([date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
                          date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8)], days)

    def test_a_turn_runs_from_the_last_day_of_the_month_to_the_third_of_the_next(self):
        days = experiment.trading_days(date(2026, 8, 28), date(2026, 9, 9), [date(2026, 9, 7)])
        self.assertEqual([(date(2026, 8, 31), date(2026, 9, 3))], experiment.month_turns(days))

    def test_a_turn_without_three_following_days_is_left_out(self):
        days = experiment.trading_days(date(2026, 8, 28), date(2026, 9, 3), [])
        self.assertEqual([], experiment.month_turns(days))
