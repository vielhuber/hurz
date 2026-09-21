from unittest import TestCase

import numpy as np

from scripts.burst_adx_priority import prioritize
from scripts.cluster_rotate_worst import admit


class BurstAdxPriorityTest(TestCase):
    def signal(self, pair, adx, hour=0):
        timestamp = np.datetime64("2025-01-01T00:00") + np.timedelta64(hour, "h")
        return dict(pair=pair, strat="donchian_breakout", adx=adx, ts=timestamp,
                    exit_ts=timestamp + np.timedelta64(24, "h"), dir=1,
                    r=0.5, usd=1.0, risk=2.0)

    def test_baseline_uses_list_order(self):
        low, high = self.signal("AAA", 31), self.signal("BBB", 49)
        order = {("donchian_breakout", "AAA"): 0, ("donchian_breakout", "BBB"): 1}
        self.assertEqual([low, high], prioritize([high, low], order, False))

    def test_candidate_uses_highest_adx_only_within_same_bar(self):
        low, high, later = self.signal("AAA", 31), self.signal("BBB", 40), self.signal("CCC", 49, 1)
        order = {("donchian_breakout", pair): i for i, pair in enumerate(("AAA", "BBB", "CCC"))}
        self.assertEqual([high, low, later], prioritize([later, low, high], order, True))

    def test_ties_keep_list_order_and_ignore_future_pnl(self):
        first, second = self.signal("AAA", 40), self.signal("BBB", 40)
        second["usd"] = 1000
        order = {("donchian_breakout", "AAA"): 0, ("donchian_breakout", "BBB"): 1}
        self.assertEqual([first, second], prioritize([second, first], order, True))

    def test_candidate_cannot_add_inactive_signals(self):
        active, inactive = self.signal("AAA", 31), self.signal("BBB", 49)
        self.assertEqual([active], prioritize([inactive, active], {("donchian_breakout", "AAA"): 0}, True))

    def test_cluster_cap_still_refuses_fourth_same_direction_position(self):
        signals = [self.signal(pair, adx) for pair, adx in
                   (("DE40", 31), ("US500", 35), ("US30", 40), ("US100", 49))]
        order = {(trade["strat"], trade["pair"]): i for i, trade in enumerate(signals)}
        booked = []
        admit(prioritize(signals, order, True), set(order), "live", None,
              {"open": {}, "pair": {}}, booked, [])
        self.assertEqual(["US100", "US30", "US500"], [trade["pair"] for trade in booked])
