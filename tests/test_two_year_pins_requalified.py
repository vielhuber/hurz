from unittest import TestCase

import numpy as np

import scripts.two_year_pins_requalified as experiment

START = np.datetime64("2025-01-01T00")


def trades(rs):
    out = []
    for i, r in enumerate(rs):
        ts = START + np.timedelta64(24 * i, "h")
        out.append({"strat": "momentum_4h", "pair": "COPPER", "ts": ts, "exit_ts": ts + np.timedelta64(8, "h"), "r": r})
    return out


class TwoYearPinsRequalifiedTest(TestCase):
    def test_a_pin_qualifies_only_with_enough_trades_and_the_list_floors(self):
        self.assertTrue(experiment.qualifies(trades([1.0, -0.5] * 5)))
        self.assertFalse(experiment.qualifies(trades([1.0, -0.5] * 4)))
        self.assertFalse(experiment.qualifies(trades([0.2, -1.0] * 5)))
