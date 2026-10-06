from unittest import TestCase

import numpy as np

import scripts.momentum_list_calibration as experiment

CUT = np.datetime64("2026-10-05T00")


def trade(pair, hours_before, held, r, strategy="momentum"):
    ts = CUT - np.timedelta64(hours_before, "h")
    return {"strat": strategy, "pair": pair, "ts": ts, "exit_ts": ts + np.timedelta64(held, "h"), "r": r}


class MomentumListCalibrationTest(TestCase):
    def test_inputs_count_one_position_at_a_time_like_the_selector(self):
        signals = [trade("US30", 100, 10, 1.0), trade("US30", 95, 2, -1.0), trade("US30", 50, 5, -0.5)]
        stats, _ = experiment.replay_inputs(signals, CUT, "momentum")
        self.assertEqual((2, 0.25, 2.0), stats["US30"])

    def test_trades_still_open_at_the_cut_or_older_than_a_year_are_left_out(self):
        signals = [trade("US30", 2, 5, 1.0), trade("US30", 366 * 24, 5, 1.0), trade("GOLD", 10, 5, 0.4, "turtle_breakout")]
        stats, window = experiment.replay_inputs(signals, CUT, "momentum")
        self.assertEqual({}, stats)
        self.assertEqual(1, len(window))

    def test_the_bot_list_holds_the_hourly_rows_of_one_strategy(self):
        active = {"pairs": [{"strategy": "momentum", "resolution": "1h", "pair": "US30"},
                            {"strategy": "momentum_4h", "resolution": "4h", "pair": "COPPER"},
                            {"strategy": "momentum", "resolution": "4h", "pair": "GOLD"}]}
        self.assertEqual({("momentum", "US30")}, experiment.bot_list(active, "momentum"))
