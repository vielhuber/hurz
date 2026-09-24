from unittest import TestCase
from unittest.mock import patch

import numpy as np

from scripts import rank_without_profit_factor as experiment


class RankWithoutProfitFactorTest(TestCase):
    def training(self, pair, returns):
        return [dict(strat="momentum", pair=pair, r=value) for value in returns]

    def test_baseline_is_unchanged_and_candidate_removes_only_multiplier(self):
        rows = (self.training("AAA", [0.6] * 8 + [-0.4] * 2)
                + self.training("BBB", [1.5] * 5 + [-0.5] * 5))
        orders = experiment.active_orders(rows, set(), set(), [])
        self.assertEqual({("momentum", "AAA"): (0, 1), ("momentum", "BBB"): (1, 0)}, orders)
        baseline = experiment.baseline_order(rows, set(), set(), [])
        self.assertEqual(baseline, {key: positions[0] for key, positions in orders.items()})

    def test_eligibility_vetoes_and_reservations_still_apply(self):
        rows = (self.training("SHORT", [1.0] * 9)
                + self.training("LOSS", [0.1, -1.0] * 5)
                + self.training("VETO", [1.0] * 10)
                + self.training("RESERVED", [1.0] * 10)
                + self.training("ACTIVE", [1.0, -0.5] * 5))
        with patch.object(experiment.pe, "VETOED", {("momentum", "VETO")}):
            orders = experiment.active_orders(rows, set(), {"RESERVED"}, [])
        self.assertEqual({("momentum", "ACTIVE"): (0, 0)}, orders)

    def test_weak_but_eligible_combinations_are_not_newly_filtered(self):
        rows = self.training("AAA", [0.9, -1.0] * 5)
        self.assertEqual({("momentum", "AAA"): (0, 0)},
                         experiment.active_orders(rows, set(), set(), []))

    def test_pins_append_in_file_order_and_ranked_pins_keep_their_place(self):
        rows = self.training("AAA", [1.0, -0.5] * 5)
        pins = {("momentum", pair) for pair in ("AAA", "BBB", "CCC")}
        pin_order = [("momentum", pair) for pair in ("CCC", "AAA", "BBB")]
        self.assertEqual({("momentum", "AAA"): (0, 0), ("momentum", "CCC"): (1, 1),
                          ("momentum", "BBB"): (2, 2)},
                         experiment.active_orders(rows, pins, {"AAA"}, pin_order))

    def test_each_arm_respects_its_own_top_n(self):
        rows = (self.training("AAA", [0.6] * 8 + [-0.4] * 2)
                + self.training("BBB", [1.5] * 5 + [-0.5] * 5))
        with patch.object(experiment.pe, "LIVE_N", 1):
            orders = experiment.active_orders(rows, set(), set(), [])
        signals = [dict(strat="momentum", pair=pair, ts=np.datetime64("2025-01-01"))
                   for pair in ("AAA", "BBB", "INACTIVE")]
        self.assertEqual([signals[0]], experiment.prioritize(signals, orders, False))
        self.assertEqual([signals[1]], experiment.prioritize(signals, orders, True))

    def test_equal_scores_keep_existing_tie_break(self):
        rows = self.training("AAA", [1.0, -0.5] * 5) + self.training("BBB", [1.0, -0.5] * 5)
        self.assertEqual({("momentum", "BBB"): (0, 0), ("momentum", "AAA"): (1, 1)},
                         experiment.active_orders(rows, set(), set(), []))

    def test_priorities_never_move_later_signals_ahead_or_change_outcomes(self):
        rows = [dict(strat="momentum", pair=pair, ts=np.datetime64(timestamp), usd=value)
                for pair, timestamp, value in (("AAA", "2025-01-01", -1),
                                                ("BBB", "2025-01-02", 10))]
        original = [dict(row) for row in rows]
        orders = {("momentum", "AAA"): (0, 1), ("momentum", "BBB"): (1, 0)}
        self.assertEqual(rows, experiment.prioritize(rows[::-1], orders, True))
        self.assertEqual(original, rows)
