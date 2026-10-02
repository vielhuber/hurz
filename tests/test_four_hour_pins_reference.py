from unittest import TestCase
from unittest.mock import patch

import numpy as np

import scripts.four_hour_pins_reference as experiment


def trade(entry_ts, exit_ts):
    return {"ts": np.datetime64(entry_ts), "exit_ts": np.datetime64(exit_ts), "pair": "COPPER", "dir": 1,
            "strat": "momentum_4h", "r": 0.5, "usd": 1.5, "risk": 3.0, "entry": 4.0, "stop_d": 0.1,
            "cost_r": 0.02}


class FourHourPinsReferenceTest(TestCase):
    def financed(self, trades):
        with patch.object(experiment, "night_charge", lambda pair, direction, entry, stop_d: 0.04):
            return experiment.financed(trades)

    def test_a_4h_trade_held_over_two_rollovers_pays_two_nights(self):
        # Last hourly bar of the 4h bar at 15:00 closes 16:00; exit bar closes 16:00 two days later.
        held, = self.financed([trade("2026-01-05T15:00", "2026-01-07T15:00")])
        self.assertAlmostEqual(0.5 - 2 * 0.04, held["r"])
        self.assertAlmostEqual(held["r"] * 3.0, held["usd"])

    def test_a_4h_trade_closed_before_the_rollover_pays_nothing(self):
        held, = self.financed([trade("2026-01-05T11:00", "2026-01-05T19:00")])
        self.assertAlmostEqual(0.5, held["r"])
