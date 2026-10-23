from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.four_hour_pins_short_leash as experiment
import scripts.four_hour_stream as fhs


class FourHourPinsShortLeashTest(TestCase):
    def test_a_4h_trade_is_closed_twelve_bars_after_its_order_and_the_default_leash_restored(self):
        stamps = pd.date_range("2026-10-12 00:00", periods=40, freq="4h").values
        close = np.linspace(100.0, 101.0, len(stamps))
        df = pd.DataFrame({"timestamp": stamps, "open": close, "high": close + 0.05,
                           "low": close - 0.05, "close": close})
        signal = type("Signal", (), {"index": 0, "direction": 1})()
        with patch.object(fhs, "get_strategy", lambda s: (lambda df, p: [signal] if s == "momentum" else [])), \
                patch.object(fhs, "gate", lambda s, df, i: type("Gate", (), {"blocked": False})()), \
                patch.object(fhs, "direction_blocked", lambda pair, d: False), \
                patch.object(fhs, "daily_terms", lambda df, e, pair, meta, floor: (100.0, 5.0, 0.0, 1.0)):
            trades = experiment.leashed_signals({"COPPER": df}, {}, 0.0)
            default = fhs.daily_signals({"COPPER": df}, {}, 0.0)
        self.assertEqual(1, len(trades))
        # wall-clock leash since section 440: the open of the bar `hold` bars after the order
        self.assertEqual(stamps[13], trades[0]["exit_ts"])
        self.assertAlmostEqual((close[13] - 100.0) / 5.0, trades[0]["r"])
        self.assertEqual(stamps[25], default[0]["exit_ts"])
