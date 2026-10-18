from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import scripts.four_hour_pins_weekend_flat as experiment


class FourHourPinsWeekendFlatTest(TestCase):
    def test_a_4h_trade_is_closed_at_the_last_bar_before_the_weekend(self):
        before = pd.date_range("2026-10-15 00:00", "2026-10-16 20:00", freq="4h")
        after = pd.date_range("2026-10-18 20:00", periods=30, freq="4h")
        stamps = np.concatenate([before.values, after.values])
        close = np.linspace(100.0, 101.0, len(stamps))
        df = pd.DataFrame({"timestamp": stamps, "open": close, "high": close + 0.05,
                           "low": close - 0.05, "close": close})
        signal = type("Signal", (), {"index": 0, "direction": 1})()
        with patch.object(experiment, "get_strategy", lambda s: (lambda df, p: [signal] if s == "momentum" else [])), \
                patch.object(experiment, "gate", lambda s, df, i: type("Gate", (), {"blocked": False})()), \
                patch.object(experiment, "direction_blocked", lambda pair, d: False), \
                patch.object(experiment, "daily_terms", lambda df, e, pair, meta, floor: (100.0, 5.0, 0.0, 1.0)):
            trades = experiment.flat_four_hour_signals({"COPPER": df}, {}, 0.0)
        self.assertEqual(1, len(trades))
        self.assertTrue(trades[0]["forced"])
        self.assertEqual(np.datetime64("2026-10-16T20"), trades[0]["exit_ts"])
        self.assertEqual("momentum_4h", trades[0]["strat"])
