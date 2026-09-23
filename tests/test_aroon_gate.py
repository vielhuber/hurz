from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.aroon_gate import aroon, signals_with_aroon, prioritize


class AroonGateTest(TestCase):
    def test_rising_and_falling_extremes_reach_opposite_endpoints(self):
        frame = pd.DataFrame({"high": np.arange(40.0) + 2, "low": np.arange(40.0)})
        values = aroon(frame)
        self.assertTrue(np.isnan(values[:25]).all())
        np.testing.assert_allclose(np.full(15, 100.0), values[25:])
        np.testing.assert_allclose(np.full(15, -100.0), aroon(frame.iloc[::-1])[25:])

    def test_repeated_extreme_uses_most_recent_occurrence(self):
        frame = pd.DataFrame({"high": [101.0] * 26, "low": [99.0] * 26})
        frame.loc[[20, 25], "high"] = 110.0
        frame.loc[10, "low"] = 90.0
        self.assertEqual(60.0, aroon(frame)[25])

    def test_same_bar_extremes_and_flat_ranges_are_neutral(self):
        frame = pd.DataFrame({"high": [101.0] * 26, "low": [99.0] * 26})
        self.assertEqual(0.0, aroon(frame)[25])
        frame.loc[25, ["high", "low"]] = [110.0, 90.0]
        self.assertEqual(0.0, aroon(frame)[25])

    def test_future_changes_do_not_change_past_values(self):
        frame = pd.DataFrame({"high": np.arange(50.0) + 2, "low": np.arange(50.0)})
        expected = aroon(frame)[:35]
        frame.loc[35:, "high"] = 1000.0
        frame.loc[35:, "low"] = -1000.0
        np.testing.assert_allclose(expected, aroon(frame)[:35])

    def test_missing_bar_makes_window_undefined(self):
        frame = pd.DataFrame({"high": np.arange(26.0) + 2, "low": np.arange(26.0)})
        frame.loc[10, "high"] = np.nan
        self.assertTrue(np.isnan(aroon(frame)[25]))

    def test_direction_zero_and_missing_values_preserve_baseline_order(self):
        rows = [dict(ts=np.datetime64("2025-01-01"), strat="momentum", pair=str(index),
                     dir=direction, aroon=value)
                for index, (direction, value) in enumerate(
                    ((1, 4), (-1, -4), (1, -4), (-1, 4), (1, 0), (-1, 0), (1, np.nan)))]
        order = {("momentum", str(index)): index for index in range(len(rows))}
        self.assertEqual(rows, prioritize(rows[::-1], order, False))
        self.assertEqual(rows[:2], prioritize(rows[::-1], order, True))

    def test_filter_cannot_activate_ineligible_signals(self):
        row = dict(ts=np.datetime64("2025-01-01"), strat="momentum", pair="AAA", dir=1, aroon=100)
        self.assertEqual([], prioritize([row], {}, True))

    def test_feature_uses_completed_signal_bar_and_preserves_outcomes(self):
        frame = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=27, freq="h", tz="UTC"),
                              "high": np.arange(27.0) + 2, "low": np.arange(27.0)})
        frame.loc[26, "low"] = -100.0
        row = dict(ts=frame.timestamp.values[25], pair="AAA", usd=7.0, risk=3.0)
        with patch("scripts.aroon_gate.base_signals", return_value=[dict(row)]) as source:
            actual = signals_with_aroon({"AAA": frame}, 3.0, {})
        source.assert_called_once_with({"AAA": frame}, 3.0, {})
        self.assertEqual([{**row, "aroon": 100.0}], actual)
