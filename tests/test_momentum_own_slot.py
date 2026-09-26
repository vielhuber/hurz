from unittest import TestCase
from unittest.mock import patch

import numpy as np

import scripts.momentum_own_slot as experiment


def trade(strat, pair, hour, exit_hour, direction=1, r=0.5):
    base = np.datetime64("2026-01-01T00")
    return dict(strat=strat, pair=pair, dir=direction, r=r,
                ts=base + np.timedelta64(hour, "h"), exit_ts=base + np.timedelta64(exit_hour, "h"))


class MomentumOwnSlotTest(TestCase):
    def admit(self, window, clusters=None):
        state, booked = {"open": {}, "pair": {}}, []
        active = {(t["strat"], t["pair"]) for t in window}
        with patch.object(experiment, "MAX_CONCURRENT", 2), \
                patch.object(experiment, "_CORRELATION_CLUSTERS", clusters or {}):
            experiment.admit_own_slot(window, active, state, booked)
        return [(t["strat"], t["pair"]) for t in booked]

    def test_momentum_enters_beside_a_full_breakout_book_but_only_once(self):
        window = [trade("turtle_breakout", "AAA", 0, 10), trade("turtle_breakout", "BBB", 1, 10),
                  trade("turtle_breakout", "CCC", 2, 10), trade("momentum", "DDD", 3, 10),
                  trade("momentum", "EEE", 4, 10)]
        self.assertEqual([("turtle_breakout", "AAA"), ("turtle_breakout", "BBB"), ("momentum", "DDD")],
                         self.admit(window))

    def test_one_position_per_instrument_and_cluster_caps_still_bind_momentum(self):
        window = [trade("turtle_breakout", "AAA", 0, 10), trade("momentum", "AAA", 1, 10)]
        self.assertEqual([("turtle_breakout", "AAA")], self.admit(window))
        clusters = {pair: "indices" for pair in ("AAA", "BBB", "CCC", "DDD")}
        window = [trade("turtle_breakout", "AAA", 0, 10), trade("turtle_breakout", "BBB", 1, 10),
                  trade("momentum", "CCC", 2, 10), trade("momentum", "DDD", 3, 10)]
        with patch.object(experiment, "_CLUSTER_DIR_CAP", 2):
            self.assertEqual([("turtle_breakout", "AAA"), ("turtle_breakout", "BBB")],
                             self.admit(window, clusters))

    def test_stop_out_cooldown_applies_after_a_loss(self):
        window = [trade("momentum", "AAA", 0, 2, r=-1.0), trade("momentum", "AAA", 4, 10),
                  trade("momentum", "AAA", 9, 12)]
        self.assertEqual([("momentum", "AAA"), ("momentum", "AAA")], self.admit(window))
