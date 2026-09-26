from unittest import TestCase
from unittest.mock import patch

import scripts.pin_eligibility as pe
from scripts.momentum_everywhere import with_everywhere


class MomentumEverywhereTest(TestCase):
    def test_momentum_joins_every_instrument_after_the_existing_order(self):
        order = {("turtle_breakout", "AAA"): 0, ("momentum", "BBB"): 1}
        with patch.object(pe, "VETOED", set()), patch.object(pe, "VETOED_STRATEGIES", set()):
            extended = with_everywhere(order, {"AAA", "BBB", "CCC"}, set(), set())
        self.assertEqual({("turtle_breakout", "AAA"): 0, ("momentum", "BBB"): 1,
                          ("momentum", "AAA"): 2, ("momentum", "CCC"): 3}, extended)

    def test_vetoes_and_reserved_instruments_are_respected(self):
        with patch.object(pe, "VETOED", {("momentum", "AAA")}), patch.object(pe, "VETOED_STRATEGIES", set()):
            extended = with_everywhere({}, {"AAA", "BBB", "CCC"}, set(), {"BBB"})
        self.assertEqual({("momentum", "CCC"): 0}, extended)
        with patch.object(pe, "VETOED", set()), patch.object(pe, "VETOED_STRATEGIES", {"momentum"}):
            self.assertEqual({}, with_everywhere({}, {"AAA"}, set(), set()))

    def test_a_pinned_momentum_combination_keeps_its_reserved_instrument(self):
        pins = {("momentum", "BBB")}
        with patch.object(pe, "VETOED", set()), patch.object(pe, "VETOED_STRATEGIES", set()):
            extended = with_everywhere({}, {"BBB"}, pins, {"BBB"})
        self.assertEqual({("momentum", "BBB"): 0}, extended)
