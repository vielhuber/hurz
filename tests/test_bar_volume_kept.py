from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.platforms import Bar
from app.spot_trading import autotrade
from scripts import spot_backtest


def _bars():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [Bar(timestamp=start + timedelta(hours=i), open=100.0, high=101.0,
                low=99.0, close=100.5, volume=1000.0 + i) for i in range(5)]


class BarVolumeTest(unittest.TestCase):
    """app/strategies/base.py documents the frame as carrying volume, but
    both converters dropped it — so a volume-based strategy would have
    raised KeyError and none was ever written."""

    def test_the_backtest_frame_carries_volume(self):
        df = spot_backtest._bars_to_df(_bars())
        self.assertIn("volume", df.columns)
        self.assertEqual(1000.0, df.iloc[0]["volume"])

    def test_the_live_frame_carries_volume(self):
        df = autotrade._bars_to_df(_bars())
        self.assertIn("volume", df.columns)
        self.assertEqual(1004.0, df.iloc[4]["volume"])

    def test_both_converters_agree_on_columns(self):
        self.assertEqual(
            sorted(spot_backtest._bars_to_df(_bars()).columns),
            sorted(autotrade._bars_to_df(_bars()).columns),
        )


if __name__ == "__main__":
    unittest.main()
