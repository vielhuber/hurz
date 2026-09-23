from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.choppiness_gate import choppiness, signals_with_choppiness, prioritize


class ChoppinessGateTest(TestCase):
    def test_repeated_same_range_has_choppiness_100(self):
        frame = pd.DataFrame({"high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20})
        values = choppiness(frame)
        self.assertTrue(np.isnan(values[:13]).all())
        np.testing.assert_allclose(np.full(7, 100.0), values[13:])

    def test_nonoverlapping_trend_has_choppiness_zero(self):
        frame = pd.DataFrame({"high": np.arange(20.0) + 1, "low": np.arange(20.0),
                              "close": np.arange(20.0) + 1})
        np.testing.assert_allclose(np.zeros(7), choppiness(frame)[13:])

    def test_gap_is_included_in_true_range(self):
        frame = pd.DataFrame({"high": [101.0] * 13 + [111.0], "low": [99.0] * 13 + [109.0],
                              "close": [100.0] * 13 + [110.0]})
        self.assertAlmostEqual(100 * np.log10(37 / 12) / np.log10(14), choppiness(frame)[13])

    def test_flat_range_is_undefined(self):
        frame = pd.DataFrame({column: [100.0] * 20 for column in ("high", "low", "close")})
        self.assertTrue(np.isnan(choppiness(frame)).all())

    def test_future_changes_do_not_change_past_values(self):
        frame = pd.DataFrame({"high": np.arange(40.0) + 2, "low": np.arange(40.0),
                              "close": np.arange(40.0) + 1})
        np.testing.assert_allclose(choppiness(frame)[:25], choppiness(frame.iloc[:25]))

    def test_boundary_and_missing_values_preserve_baseline_and_order(self):
        rows = [dict(ts=np.datetime64("2025-01-01"), strat="momentum", pair=str(index), choppiness=value)
                for index, value in enumerate((61.8, 61.81, np.nan, 30))]
        order = {("momentum", str(index)): index for index in range(4)}
        self.assertEqual(rows, prioritize(rows[::-1], order, False))
        self.assertEqual([rows[0], rows[3]], prioritize(rows[::-1], order, True))

    def test_filter_cannot_activate_ineligible_signals(self):
        row = dict(ts=np.datetime64("2025-01-01"), strat="momentum", pair="AAA", choppiness=10)
        self.assertEqual([], prioritize([row], {}, True))

    def test_feature_attaches_to_signal_bar_without_altering_outcomes(self):
        frame = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=20, freq="h"),
                              "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20})
        row = dict(ts=frame.timestamp.iloc[13].to_datetime64(), pair="AAA", usd=7.0, risk=3.0)
        with patch("scripts.choppiness_gate.base_signals", return_value=[dict(row)]) as source:
            actual = signals_with_choppiness({"AAA": frame}, 3.0, {})
        source.assert_called_once_with({"AAA": frame}, 3.0, {})
        self.assertEqual([{**row, "choppiness": 100.0}], actual)

    def test_timezone_aware_history_matches_numpy_signal_timestamp(self):
        frame = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=20, freq="h", tz="UTC"),
                              "high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20})
        row = dict(ts=frame.timestamp.values[13], pair="AAA")
        with patch("scripts.choppiness_gate.base_signals", return_value=[dict(row)]):
            self.assertEqual([{**row, "choppiness": 100.0}], signals_with_choppiness({"AAA": frame}, 3.0, {}))
