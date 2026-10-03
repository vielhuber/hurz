from unittest import TestCase
from unittest.mock import patch

import scripts.efficiency_weighted_selection as ews
import scripts.momentum_leash as experiment


def fake_signals(frames, floor, meta):
    return [{"strat": strategy, "ts": index, "hold": ews.HOLD} for index, strategy in enumerate(ews.STRATS)]


class MomentumLeashTest(TestCase):
    def test_only_the_named_strategy_is_priced_at_the_new_leash(self):
        with patch.object(ews, "all_signals", fake_signals):
            rows = experiment.with_leash({}, 3.0, {}, "momentum", 48)
        self.assertEqual({"donchian_breakout": 24, "turtle_breakout": 24, "momentum": 48},
                         {row["strat"]: row["hold"] for row in rows})

    def test_the_shared_leash_is_restored_afterwards(self):
        with patch.object(ews, "all_signals", fake_signals):
            experiment.with_leash({}, 3.0, {}, "momentum", 48)
        self.assertEqual(24, ews.HOLD)
        self.assertEqual(["donchian_breakout", "momentum", "turtle_breakout"], ews.STRATS)
