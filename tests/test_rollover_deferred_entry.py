from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.rollover_deferred_entry as experiment


def stamps(start, periods):
    return pd.date_range(start, periods=periods, freq="h").values


class EntryBarTest(TestCase):
    def test_a_signal_outside_the_window_is_entered_on_its_own_bar(self):
        ts = stamps("2026-01-05T10:00", 20)
        self.assertEqual(3, experiment.entry_bar(ts, 3))

    def test_a_late_signal_moves_to_the_bar_opening_at_21_utc(self):
        ts = stamps("2026-01-05T10:00", 20)
        self.assertEqual(11, experiment.entry_bar(ts, 8))  # 18:00 -> 21:00

    def test_a_late_signal_skips_a_closed_hour(self):
        ts = np.concatenate([stamps("2026-01-05T15:00", 6), stamps("2026-01-05T22:00", 5)])
        self.assertEqual(6, experiment.entry_bar(ts, 4))  # 19:00 -> 22:00

    def test_a_late_signal_without_a_bar_the_same_night_is_dropped(self):
        friday = stamps("2026-01-09T15:00", 6)
        sunday = stamps("2026-01-11T22:00", 5)
        self.assertIsNone(experiment.entry_bar(np.concatenate([friday, sunday]), 4))


class DeferredSignalsTest(TestCase):
    def test_the_deferred_trade_is_booked_from_the_later_bar_without_the_night(self):
        frame = pd.DataFrame({"timestamp": stamps("2026-01-05T10:00", 60), "open": 100.0,
                              "high": 100.2, "low": 99.8, "close": 100.0})
        strategy = lambda df, params: [SimpleNamespace(index=8, direction=1)]
        with patch.multiple(experiment,
                            STRATS=["donchian_breakout"],
                            get_strategy=lambda name: strategy,
                            gate=lambda name, df, index: SimpleNamespace(blocked=False),
                            trade_terms=lambda df, index, pair, meta, floor: (100.0, 1.0, 0.0, 2.0),
                            book=lambda O, H, L, C, e, d, entry, stop_d, cost_r, n: (0.5, e + 5)):
            trade = experiment.deferred_signals({"US500": frame}, 3.0, {})[0]
        self.assertTrue(trade["deferred"])
        self.assertEqual(np.datetime64("2026-01-05T21:00"), trade["ts"].astype("datetime64[m]"))
        self.assertAlmostEqual(0.5, trade["r"])
        self.assertAlmostEqual(1.0, trade["usd"])
