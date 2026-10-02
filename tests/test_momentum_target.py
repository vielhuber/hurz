from unittest import TestCase
from unittest.mock import patch

import scripts.efficiency_weighted_selection as ews
import scripts.momentum_target as experiment


def fake_signals(frames, floor, meta):
    return [{"strat": strategy, "ts": index, "rr": ews.RR} for index, strategy in enumerate(ews.STRATS)]


class MomentumTargetTest(TestCase):
    def test_only_the_named_strategy_is_priced_at_the_new_target(self):
        with patch.object(ews, "all_signals", fake_signals):
            rows = experiment.with_target({}, 3.0, {}, "momentum", 2.5)
        self.assertEqual({"donchian_breakout": 1.5, "turtle_breakout": 1.5, "momentum": 2.5},
                         {row["strat"]: row["rr"] for row in rows})

    def test_the_shared_target_is_restored_afterwards(self):
        with patch.object(ews, "all_signals", fake_signals):
            experiment.with_target({}, 3.0, {}, "momentum", 2.5)
        self.assertEqual(1.5, ews.RR)
        self.assertEqual(["donchian_breakout", "momentum", "turtle_breakout"], ews.STRATS)
