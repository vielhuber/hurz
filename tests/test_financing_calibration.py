from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.efficiency_weighted_selection as base


def frame(bars=40):
    stamps = pd.date_range("2026-01-01T00:00", periods=bars, freq="h")
    return pd.DataFrame({"timestamp": stamps, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})


class FinancingCalibrationTest(TestCase):
    def signals(self, charge, pair="EURUSD", direction=1):
        with patch.multiple(base, CHARGE_FINANCING=charge, STRATS=["turtle_breakout"],
                            get_strategy=lambda name: (lambda df, params: [SimpleNamespace(index=0, direction=direction)]),
                            gate=lambda name, df, index: SimpleNamespace(blocked=False),
                            direction_blocked=lambda pair, direction: False,
                            trade_terms=lambda df, index, pair, meta, floor: (100.0, 1.0, 0.0, 2.0),
                            book=lambda *args: (0.5, 30)):
            return base.all_signals({pair: frame()}, 3.0, {})

    def test_a_trade_across_one_rollover_pays_one_night(self):
        trade = self.signals(True)[0]
        self.assertAlmostEqual(0.5 - 0.005, trade["r"])
        self.assertAlmostEqual((0.5 - 0.005) * 2.0, trade["usd"])

    def test_the_charge_follows_class_and_direction(self):
        self.assertAlmostEqual(0.5 - 0.050, self.signals(True, "BTCUSD", 1)[0]["r"])
        self.assertAlmostEqual(0.5, self.signals(True, "BTCUSD", -1)[0]["r"])
        self.assertAlmostEqual(0.5 - 0.003, self.signals(True, "EURUSD", -1)[0]["r"])

    def test_the_gross_replay_is_unchanged_without_the_charge(self):
        self.assertAlmostEqual(0.5, self.signals(False)[0]["r"])

    def test_exit_times_follow_the_exit_bar(self):
        trade = self.signals(True)[0]
        self.assertEqual(np.datetime64("2026-01-02T06:00"), trade["exit_ts"])
