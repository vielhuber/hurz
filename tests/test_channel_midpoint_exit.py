from unittest import TestCase

import numpy as np
import pandas as pd

from scripts.channel_midpoint_exit import channel_balance
from scripts.directional_exit import book_directional


class ChannelMidpointExitTest(TestCase):
    def test_channel_uses_previous_twenty_completed_bars(self):
        frame = pd.DataFrame({"high": np.arange(30.0) + 2,
                              "low": np.arange(30.0), "close": np.arange(30.0) + 1})
        balance = channel_balance(frame)
        self.assertTrue(np.isnan(balance[:20]).all())
        self.assertEqual(21 - (21 + 0) / 2, balance[20])
        self.assertEqual(22 - (22 + 1) / 2, balance[21])

    def test_current_high_low_do_not_move_previous_channel(self):
        frame = pd.DataFrame({"high": [102.0] * 26, "low": [98.0] * 26, "close": [101.0] * 26})
        frame.loc[20, ["high", "low"]] = [200.0, 0.0]
        self.assertEqual(1.0, channel_balance(frame)[20])

    def test_future_bars_cannot_change_past_balance(self):
        frame = pd.DataFrame({"high": np.arange(40.0) + 2,
                              "low": np.arange(40.0), "close": np.arange(40.0) + 1})
        np.testing.assert_allclose(channel_balance(frame)[:30], channel_balance(frame.iloc[:30]))

    def test_reflection_reverses_balance(self):
        frame = pd.DataFrame({"high": np.arange(30.0) + 2,
                              "low": np.arange(30.0), "close": np.arange(30.0) + 1})
        reflected = pd.DataFrame({"high": -frame["low"], "low": -frame["high"], "close": -frame["close"]})
        np.testing.assert_allclose(-channel_balance(frame), channel_balance(reflected))

    def test_actual_channel_cross_exits_both_directions(self):
        for direction in (1, -1):
            frame = pd.DataFrame({"open": [100.0] * 50, "high": [101.0] * 50,
                                  "low": [99.0] * 50, "close": [100 + direction * 0.5] * 50})
            frame.loc[23:, "close"] = 100 - direction * 0.5
            arrays = [frame[column].values for column in ("open", "high", "low", "close")]
            actual = book_directional(*arrays, channel_balance(frame), 20,
                                      direction, 100 + direction * 0.5, 2, 0.02, True)
            self.assertEqual((-0.52, 23, True), actual)
