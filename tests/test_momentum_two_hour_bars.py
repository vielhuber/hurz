from unittest import TestCase

import numpy as np
import pandas as pd

import scripts.momentum_two_hour_bars as experiment


class MomentumTwoHourBarsTest(TestCase):
    def test_hourly_bars_resample_into_utc_anchored_two_hour_bars(self):
        hourly = pd.DataFrame({
            "timestamp": pd.date_range("2026-10-07 00:00", periods=4, freq="h").values,
            "open": [1.0, 2.0, 3.0, 4.0], "high": [5.0, 6.0, 7.0, 9.0],
            "low": [0.5, 1.5, 2.5, 0.1], "close": [2.0, 3.0, 4.0, 5.0]})
        bars = experiment.two_hour_frames({"US30": hourly})["US30"]
        self.assertEqual([np.datetime64("2026-10-07T00"), np.datetime64("2026-10-07T02")],
                         [np.datetime64(t, "h") for t in bars["timestamp"].values])
        self.assertEqual([1.0, 3.0], bars["open"].tolist())
        self.assertEqual([6.0, 9.0], bars["high"].tolist())
        self.assertEqual([0.5, 0.1], bars["low"].tolist())
        self.assertEqual([3.0, 5.0], bars["close"].tolist())

    def test_the_leash_is_restored_after_pricing(self):
        before = experiment.ews.HOLD
        self.assertEqual([], experiment.two_hour_signals({}, 3.0, {}, "momentum"))
        self.assertEqual(before, experiment.ews.HOLD)
