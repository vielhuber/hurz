from unittest import TestCase

import numpy as np

from scripts.burst_risk_priority import prioritize
from scripts.cluster_rotate_worst import admit


class BurstRiskPriorityTest(TestCase):
    def signal(self, pair, risk, hour=0):
        timestamp = np.datetime64("2025-01-01T00:00") + np.timedelta64(hour, "h")
        return dict(pair=pair, strat="donchian_breakout", risk=risk, ts=timestamp,
                    exit_ts=timestamp + np.timedelta64(24, "h"), dir=1,
                    r=0.5, usd=0.5 * risk)

    def test_baseline_keeps_list_order(self):
        first, second = self.signal("AAA", 1.0), self.signal("BBB", 3.0)
        order = {("donchian_breakout", "AAA"): 0, ("donchian_breakout", "BBB"): 1}
        self.assertEqual([first, second], prioritize([second, first], order, False))

    def test_candidate_orders_only_simultaneous_signals(self):
        low, high, later = self.signal("AAA", 1), self.signal("BBB", 2), self.signal("CCC", 3, 1)
        order = {("donchian_breakout", pair): index for index, pair in enumerate(("AAA", "BBB", "CCC"))}
        self.assertEqual([high, low, later], prioritize([later, low, high], order, True))

    def test_ties_ignore_future_outcomes(self):
        first, second = self.signal("AAA", 3), self.signal("BBB", 3)
        second.update(usd=1000, r=1000, exit_ts=second["exit_ts"] + np.timedelta64(5, "h"))
        order = {("donchian_breakout", "AAA"): 0, ("donchian_breakout", "BBB"): 1}
        self.assertEqual([first, second], prioritize([second, first], order, True))

    def test_inactive_signal_is_excluded_without_resizing_inputs(self):
        active, inactive = self.signal("AAA", 1), self.signal("BBB", 3)
        original = [dict(inactive), dict(active)]
        actual = prioritize([inactive, active], {("donchian_breakout", "AAA"): 0}, True)
        self.assertEqual([active], actual)
        self.assertEqual(original, [inactive, active])

    def test_cluster_cap_still_limits_admission_to_three(self):
        rows = [self.signal(pair, risk) for pair, risk in
                (("DE40", 1), ("US500", 2), ("US30", 2.5), ("US100", 3))]
        order = {(trade["strat"], trade["pair"]): index for index, trade in enumerate(rows)}
        booked = []
        admit(prioritize(rows, order, True), set(order), "live", None,
              {"open": {}, "pair": {}}, booked, [])
        self.assertEqual(["US100", "US30", "US500"], [trade["pair"] for trade in booked])
        self.assertEqual([3, 2.5, 2], [trade["risk"] for trade in booked])

    def test_total_position_cap_still_limits_admission_to_eight(self):
        rows = [self.signal(f"PAIR{number}", number / 3) for number in range(1, 10)]
        order = {(trade["strat"], trade["pair"]): index for index, trade in enumerate(rows)}
        booked = []
        admit(prioritize(rows, order, True), set(order), "live", None,
              {"open": {}, "pair": {}}, booked, [])
        self.assertEqual(8, len(booked))
        self.assertEqual([trade["pair"] for trade in rows[:0:-1]], [trade["pair"] for trade in booked])

    def test_stop_cooldown_and_existing_position_are_preserved(self):
        rows = [self.signal("AAA", 3, hour) for hour in (0, 1, 24, 29, 30)]
        rows[0]["r"] = -1
        order = {("donchian_breakout", "AAA"): 0}
        booked = []
        admit(prioritize(rows, order, True), set(order), "live", None,
              {"open": {}, "pair": {}}, booked, [])
        self.assertEqual([rows[0]["ts"], rows[-1]["ts"]], [trade["ts"] for trade in booked])
