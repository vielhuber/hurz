from unittest import TestCase
from unittest.mock import patch

import numpy as np

from scripts import rank_holding_time as experiment


class HoldingTimeRankingTest(TestCase):
    def trades(self, pair, hours, returns=None):
        start = np.datetime64("2025-01-01T00:00")
        return [dict(strat="donchian_breakout", pair=pair, r=value,
                     ts=start, exit_ts=start + np.timedelta64(hours, "h"))
                for value in (returns or [1.5, -1.0] * 5)]

    def test_equal_duration_matches_existing_ranking(self):
        trades = self.trades("AAA", 24) + self.trades("ZZZ", 24)
        with patch.object(experiment.pe, "LIVE_N", 1):
            expected, _ = experiment.pe.lists(trades, set(), set())
            self.assertEqual(expected, experiment.rank_by_holding_time(trades, set(), set()))

    def test_shorter_occupation_wins_at_equal_expectancy(self):
        trades = self.trades("AAA", 6) + self.trades("ZZZ", 24)
        with patch.object(experiment.pe, "LIVE_N", 1):
            self.assertEqual({("donchian_breakout", "AAA")},
                             experiment.rank_by_holding_time(trades, set(), set()))

    def test_eligibility_reservations_and_vetoes_are_preserved(self):
        trades = (self.trades("AAA", 1) + self.trades("BBB", 1)
                  + self.trades("CCC", 1, [-1.0] * 10) + self.trades("DDD", 24))
        with patch.object(experiment.pe, "VETOED", {("donchian_breakout", "BBB")}):
            self.assertEqual({("donchian_breakout", "DDD")},
                             experiment.rank_by_holding_time(trades, set(), {"AAA"}))

    def test_calendar_series_includes_idle_days_and_excludes_end(self):
        days = np.arange(np.datetime64("2025-01-01"), np.datetime64("2025-01-04"))
        trades = [dict(exit_ts=np.datetime64("2025-01-02T10:00"), usd=3.0),
                  dict(exit_ts=np.datetime64("2025-01-04T00:00"), usd=99.0)]
        np.testing.assert_array_equal([0.0, 3.0, 0.0], experiment.daily_series(trades, days))
