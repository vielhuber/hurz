from unittest import TestCase

import scripts.ethusd_pin_ranked as experiment


class EthusdPinRankedTest(TestCase):
    def test_only_the_named_pin_is_removed(self):
        pins = {("momentum", "ETHUSD"), ("donchian_breakout", "BTCUSD")}
        order = [("donchian_breakout", "BTCUSD"), ("momentum", "ETHUSD"), ("turtle_breakout", "GOLD")]
        left, left_order = experiment.unpinned(pins, order, ("momentum", "ETHUSD"))
        self.assertEqual({("donchian_breakout", "BTCUSD")}, left)
        self.assertEqual([("donchian_breakout", "BTCUSD"), ("turtle_breakout", "GOLD")], left_order)
