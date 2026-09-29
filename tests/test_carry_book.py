from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.carry_book as experiment


def frame(bars=80):
    stamps = pd.date_range("2026-01-05T00:00", periods=bars, freq="h")
    return pd.DataFrame({"timestamp": stamps, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})


class CarryBookTest(TestCase):
    def signals(self, frames):
        with patch.multiple(experiment,
                            trade_terms=lambda df, index, pair, meta, floor: (100.0, 1.0, 0.0, 2.0),
                            book=lambda O, H, L, C, e, d, entry, stop_d, cost_r, n: (0.5, e + 5)):
            return experiment.carry_signals(frames, 3.0, {})

    def test_one_entry_a_day_at_the_19_utc_bar_in_the_credited_direction(self):
        trades = self.signals({"USDJPY": frame()})
        self.assertEqual(3, len(trades))
        self.assertTrue(all(t["dir"] == 1 and t["strat"] == "carry" for t in trades))
        self.assertTrue(all(int(t["ts"].astype("datetime64[h]").astype(np.int64) % 24) == 19 for t in trades))

    def test_the_night_is_credited_at_the_trade_notional(self):
        trade = self.signals({"USDJPY": frame()})[0]
        credit = 0.00557 / 100.0 * 100.0 / 1.0
        self.assertAlmostEqual(0.5 + credit, trade["r"])
        self.assertAlmostEqual((0.5 + credit) * 2.0, trade["usd"])

    def test_short_blocked_sides_and_unlisted_instruments_are_skipped(self):
        self.assertEqual([], self.signals({"GOLD": frame(), "US500": frame()}))
