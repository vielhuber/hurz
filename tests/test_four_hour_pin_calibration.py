import json
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

import numpy as np

import scripts.four_hour_pin_calibration as experiment


class FourHourPinCalibrationTest(TestCase):
    def test_only_live_four_hour_pins_inside_the_universe_are_booked(self):
        combos = [{"strategy": "donchian_breakout_4h", "pair": "SILVER", "resolution": "4h"},
                  {"strategy": "turtle_breakout_4h", "pair": "WHEAT", "resolution": "4h"},
                  {"strategy": "momentum_4h", "pair": "COPPER", "resolution": "4h"},
                  {"strategy": "turtle_breakout_4h", "pair": "HK50", "resolution": "4h"},
                  {"strategy": "turtle_breakout", "pair": "GOLD", "resolution": "1h"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"combos": combos}, handle)
        try:
            with patch.object(experiment.pe, "PINS_PATH", handle.name), \
                    patch.object(experiment.pe, "VETOED", {("momentum_4h", "COPPER")}):
                pins = experiment.live_four_hour_pins({"SILVER", "COPPER", "HK50", "GOLD"},
                                                      {"turtle_breakout_4h"})
        finally:
            os.unlink(handle.name)
        self.assertEqual([("donchian_breakout_4h", "SILVER")], pins)

    def test_four_hour_trades_enter_and_exit_at_the_last_hourly_close(self):
        opened = np.datetime64("2026-01-01T08")
        trade = dict(strat="donchian_breakout_4h", pair="SILVER", ts=opened, exit_ts=opened + np.timedelta64(8, "h"))
        other = dict(trade, pair="GOLD")
        shifted = experiment.aligned([trade, other], {("donchian_breakout_4h", "SILVER")})
        self.assertEqual(1, len(shifted))
        self.assertEqual(np.datetime64("2026-01-01T11"), shifted[0]["ts"])
        self.assertEqual(np.datetime64("2026-01-01T19"), shifted[0]["exit_ts"])
