from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.platforms import Bar
from app.spot_trading.autotrade import _completed_bars


def _bars(*hours):
    return [Bar(timestamp=datetime(2026, 9, 8, h, tzinfo=timezone.utc),
                open=1.0, high=1.0, low=1.0, close=1.0) for h in hours]


class CompletedBarsTest(unittest.TestCase):
    """The venue serves the forming candle as the last row; the live loop
    was entering on it a median 31 minutes before it closed."""

    def test_the_forming_bar_is_dropped(self):
        bars = _completed_bars(_bars(1, 2, 3), "1h", datetime(2026, 9, 8, 3, 21, tzinfo=timezone.utc))
        self.assertEqual([1, 2], [b.timestamp.hour for b in bars])

    def test_a_bar_is_kept_once_its_hour_has_ended(self):
        bars = _completed_bars(_bars(1, 2, 3), "1h", datetime(2026, 9, 8, 4, 0, 30, tzinfo=timezone.utc))
        self.assertEqual([1, 2, 3], [b.timestamp.hour for b in bars])

    def test_naive_timestamps_are_read_as_utc(self):
        naive = [Bar(timestamp=datetime(2026, 9, 8, 3), open=1.0, high=1.0, low=1.0, close=1.0)]
        self.assertEqual([], _completed_bars(naive, "1h", datetime(2026, 9, 8, 3, 30, tzinfo=timezone.utc)))

    def test_four_hour_bars_use_their_own_length(self):
        bars = [Bar(timestamp=datetime(2026, 9, 8, 0, tzinfo=timezone.utc), open=1.0, high=1.0, low=1.0, close=1.0)]
        self.assertEqual([], _completed_bars(bars, "4h", datetime(2026, 9, 8, 3, 59, tzinfo=timezone.utc)))
        self.assertEqual(1, len(_completed_bars(bars, "4h", datetime(2026, 9, 8, 4, 0, tzinfo=timezone.utc))))


if __name__ == "__main__":
    unittest.main()
