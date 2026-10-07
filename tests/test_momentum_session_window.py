from unittest import TestCase

import numpy as np

import scripts.momentum_session_window as experiment


class MomentumSessionWindowTest(TestCase):
    def test_the_window_runs_from_seven_to_before_twenty_one_utc(self):
        hours = [np.datetime64(f"2026-10-07T{h:02d}") for h in (6, 7, 20, 21)]
        self.assertEqual([False, True, True, False], [experiment.in_window(ts) for ts in hours])

    def test_only_the_chosen_strategy_loses_its_night_signals(self):
        night = np.datetime64("2026-10-07T03")
        signals = [{"strat": "momentum", "ts": night}, {"strat": "turtle_breakout", "ts": night},
                   {"strat": "momentum", "ts": np.datetime64("2026-10-07T15")}]
        self.assertEqual(signals[1:], experiment.windowed(signals, "momentum"))
