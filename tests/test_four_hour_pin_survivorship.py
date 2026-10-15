from unittest import TestCase

import numpy as np

import scripts.four_hour_pin_survivorship as experiment


class FourHourPinSurvivorshipTest(TestCase):
    def test_trades_split_by_exit_day_at_the_boundary(self):
        trades = [{"exit_ts": np.datetime64("2026-07-09T20"), "usd": -1.0},
                  {"exit_ts": np.datetime64("2026-07-10T01"), "usd": 2.0},
                  {"exit_ts": np.datetime64("2026-08-01T00"), "usd": 0.5}]
        self.assertEqual((-1.0, 2.5), experiment.split(trades, np.datetime64("2026-07-10")))
