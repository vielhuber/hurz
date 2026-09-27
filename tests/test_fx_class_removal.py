from unittest import TestCase

import scripts.efficiency_weighted_selection as base
from scripts.fx_class_removal import FX, without_fx


class FxClassRemovalTest(TestCase):
    def test_the_class_is_every_fx_pair_of_the_replay(self):
        self.assertTrue(FX <= set(base.PAIRS))
        self.assertEqual({"BTCUSD", "ETHUSD", "DE40", "US500", "US30", "FR40", "UK100", "EU50",
                          "US100", "HK50", "J225", "OIL_CRUDE", "OIL_BRENT", "GOLD", "SILVER",
                          "COPPER"}, set(base.PAIRS) - FX)

    def test_without_fx_keeps_every_other_instrument(self):
        frames = {"EURUSD": 1, "GBPJPY": 2, "GOLD": 3, "US500": 4}
        self.assertEqual({"GOLD": 3, "US500": 4}, without_fx(frames))
