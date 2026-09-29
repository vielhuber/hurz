from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.weekend_flat_financed as experiment


def weekend_frame():
    # Friday 2026-01-09 10:00-20:00 UTC, then the market reopens Sunday 22:00 UTC.
    friday = pd.date_range("2026-01-09T10:00", periods=11, freq="h")
    sunday = pd.date_range("2026-01-11T22:00", periods=30, freq="h")
    stamps = friday.append(sunday)
    return pd.DataFrame({"timestamp": stamps, "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0})


class WeekendFlatFinancedTest(TestCase):
    def signals(self, frame, pair="US500", direction=1):
        strategy = lambda df, params: [SimpleNamespace(index=5, direction=direction)]
        with patch.multiple(experiment,
                            STRATS=["donchian_breakout"],
                            get_strategy=lambda name: strategy,
                            gate=lambda name, df, index: SimpleNamespace(blocked=False),
                            trade_terms=lambda df, index, pair, meta, floor: (100.0, 1.0, 0.0, 2.0)):
            return experiment.flat_signals({pair: frame}, 3.0, {})

    def test_an_open_position_is_closed_at_the_last_bar_before_the_weekend(self):
        trade = self.signals(weekend_frame())[0]
        self.assertTrue(trade["forced"])
        self.assertEqual(np.datetime64("2026-01-09T20:00"), trade["exit_ts"].astype("datetime64[m]"))

    def test_the_forced_exit_pays_only_the_friday_night(self):
        trade = self.signals(weekend_frame())[0]
        one_night = 0.0222624 / 100.0 * 100.0 / 1.0
        self.assertAlmostEqual(-one_night, trade["r"])
        self.assertAlmostEqual(-one_night * 2.0, trade["usd"])

    def test_a_market_without_a_gap_keeps_the_leash(self):
        stamps = pd.date_range("2026-01-09T10:00", periods=60, freq="h")
        frame = pd.DataFrame({"timestamp": stamps, "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0})
        trade = self.signals(frame, pair="BTCUSD")[0]
        self.assertFalse(trade["forced"])
        self.assertEqual(stamps[5 + 24], pd.Timestamp(trade["exit_ts"]))
