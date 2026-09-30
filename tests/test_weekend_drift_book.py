from unittest import TestCase
from unittest.mock import patch

import pandas as pd

import scripts.weekend_drift_book as experiment


def frame(closes_before_gap, monday_bar=(100.0, 101.0, 99.0, 100.5)):
    """Two trading weeks of hourly bars, each ending in a weekend gap, then Monday's first bar."""
    week_one = pd.date_range("2026-01-05T00:00", periods=100, freq="h")
    week_two = pd.date_range("2026-01-12T00:00", periods=100, freq="h")
    monday = pd.DatetimeIndex([pd.Timestamp("2026-01-19T00:00")])
    rows = [(100.0, 100.0, 100.0, 100.0)] * 200 + [monday_bar]
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["timestamp"] = week_one.append(week_two).append(monday)
    df.loc[99, "close"], df.loc[199, "close"] = closes_before_gap
    return df


class WeekendDriftBookTest(TestCase):
    def signals(self, frames):
        with patch.multiple(experiment,
                            trade_terms=lambda df, e, pair, meta, floor: (float(df["close"][e]), 2.0, 0.1, 3.0),
                            night_charge=lambda pair, direction, entry, stop_d: 0.02):
            return experiment.drift_signals(frames, 3.0, {})

    def test_the_weeks_direction_is_held_until_the_first_bar_after_the_gap(self):
        trade, = self.signals({"US500": frame((100.0, 100.0 + 1e-9))})
        self.assertEqual(1, trade["dir"])
        self.assertEqual(pd.Timestamp("2026-01-19T00:00"), pd.Timestamp(trade["exit_ts"]))
        self.assertAlmostEqual(0.25 - 0.1 - 3 * 0.02, trade["r"], places=6)
        self.assertAlmostEqual(trade["r"] * 3.0, trade["usd"])

    def test_a_gap_through_the_stop_is_booked_at_the_open(self):
        trade, = self.signals({"US500": frame((100.0, 100.0 + 1e-9), (97.0, 97.5, 96.0, 97.0))})
        self.assertAlmostEqual(-1.5 - 0.1 - 3 * 0.02, trade["r"], places=6)

    def test_a_falling_week_goes_short_unless_the_instrument_blocks_shorts(self):
        trade, = self.signals({"DE40": frame((100.0, 99.0))})
        self.assertEqual(-1, trade["dir"])
        with patch.object(experiment, "direction_blocked", lambda pair, direction: direction < 0):
            self.assertEqual([], self.signals({"DE40": frame((100.0, 99.0))}))

    def test_only_the_index_class_trades(self):
        self.assertEqual([], self.signals({"GOLD": frame((100.0, 101.0)), "EURUSD": frame((100.0, 101.0))}))
