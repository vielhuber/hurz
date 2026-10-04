from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.daily_tsmom_book as experiment


def frame(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01T20:00", periods=len(closes), freq="D"),
                         "open": closes, "high": closes, "low": closes, "close": closes})


def rise_then_fall():
    return [100 + i for i in range(30)] + [129 - m for m in range(1, 31)]


class DailyTsmomBookTest(TestCase):
    def signals(self, df, stop_d):
        with patch.multiple(experiment,
                            trade_terms=lambda d, e, pair, meta, floor: (float(d["close"][e]), stop_d, 0.0, 2.0),
                            night_charge=lambda pair, direction, entry, stop_d: 0.0):
            return experiment.tsmom_signals({"US500": df}, 3.0, {})

    def test_the_20_day_sign_is_held_until_it_flips(self):
        trade, = self.signals(frame(rise_then_fall()), 100.0)
        self.assertEqual(1, trade["dir"])
        self.assertEqual(pd.Timestamp("2026-01-21T20:00"), pd.Timestamp(trade["ts"]))
        # Day 39 closes level with day 19: an unchanged sign no longer agrees.
        self.assertEqual(pd.Timestamp("2026-02-09T20:00"), pd.Timestamp(trade["exit_ts"]))
        self.assertAlmostEqual((119 - 120) / 100.0, trade["r"])

    def test_a_stop_inside_the_holding_closes_at_minus_one_and_the_next_close_decides_again(self):
        df = frame(rise_then_fall())
        df.loc[21, "low"] = 118.5
        first, second = self.signals(df, 1.0)[:2]
        self.assertAlmostEqual(-1.0, first["r"])
        self.assertEqual(pd.Timestamp("2026-01-22T20:00"), pd.Timestamp(first["exit_ts"]))
        self.assertEqual(pd.Timestamp("2026-01-22T20:00"), pd.Timestamp(second["ts"]))
