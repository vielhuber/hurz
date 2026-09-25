import json
import os
import tempfile
from unittest import TestCase
from unittest.mock import patch

import scripts.pin_eligibility as pe
from scripts.burst_cost_priority import active_order


def training(strategy, pair):
    return [dict(strat=strategy, pair=pair, r=value) for value in [1.0, -0.5] * 5]


class StrategyVetoReplayTest(TestCase):
    def test_lists_drop_every_combination_of_a_retired_strategy(self):
        rows = training("donchian_breakout", "AAA") + training("turtle_breakout", "AAA")
        with patch.object(pe, "VETOED_STRATEGIES", {"donchian_breakout"}):
            self.assertEqual({("turtle_breakout", "AAA"): 0}, active_order(rows, set(), set(), []))
            ranked, eligible = pe.lists(rows, set(), set())
        self.assertEqual({("turtle_breakout", "AAA")}, ranked)
        self.assertIn(("donchian_breakout", "AAA"), eligible)

    def test_without_a_retired_strategy_both_stay_listed(self):
        rows = training("donchian_breakout", "AAA") + training("turtle_breakout", "AAA")
        with patch.object(pe, "VETOED_STRATEGIES", set()):
            self.assertEqual(2, len(active_order(rows, set(), set(), [])))

    def test_load_pins_records_the_retired_strategies_and_drops_their_pins(self):
        combos = [{"strategy": "donchian_breakout", "pair": "AAA", "resolution": "1h"},
                  {"strategy": "turtle_breakout", "pair": "AAA", "resolution": "1h"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"combos": combos}, handle)
        try:
            with patch.object(pe, "PINS_PATH", handle.name), patch.object(pe, "VETOED_STRATEGIES", set()):
                pins, _ = pe.load_pins({"AAA"}, set(), {"donchian_breakout"})
                self.assertEqual({"donchian_breakout"}, pe.VETOED_STRATEGIES)
        finally:
            os.unlink(handle.name)
        self.assertEqual({("turtle_breakout", "AAA")}, pins)
