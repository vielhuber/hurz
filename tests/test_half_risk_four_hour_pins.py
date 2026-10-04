from unittest import TestCase
from unittest.mock import patch

import scripts.four_hour_stream as fhs
import scripts.half_risk_four_hour_pins as experiment


class HalfRiskFourHourPinsTest(TestCase):
    def test_the_4h_trades_are_sized_at_the_given_risk_and_the_default_is_restored(self):
        seen = []
        with patch.object(fhs, "daily_frames", lambda frames: frames), \
             patch.object(fhs, "daily_signals", lambda frames, meta, floor: seen.append(fhs.DEFAULT_TARGET_RISK_USD) or []), \
             patch.object(experiment, "financed", lambda trades: trades):
            experiment.four_hour_trades({"COPPER": None}, {}, 3.0, [("momentum_4h", "COPPER")], 1.5)
        self.assertEqual([1.5], seen)
        self.assertEqual(3.0, fhs.DEFAULT_TARGET_RISK_USD)
