from __future__ import annotations

import unittest

from app.spot_trading.pair_selector import _cost_blocks


SPREADS = {"APTUSD": 4.999, "DOTUSD": 0.510, "BTCUSD": 0.065, "US30": 0.004}
MIN_DIST = {"GOLD": {"min_dist_percent": 0.105}}


class CostBlocksTest(unittest.TestCase):
    def test_hopeless_spread_is_blocked(self):
        # 5% spread against a 1.05% venue minimum stays far over the
        # ceiling even at the 2x widening cap.
        self.assertTrue(_cost_blocks("capital_com", "APTUSD", {}, SPREADS))

    def test_alt_coin_at_the_venue_minimum_is_blocked(self):
        # 0.51% over a 2.1% widened stop is 24% — above the 10% ceiling.
        self.assertTrue(_cost_blocks("capital_com", "DOTUSD", {}, SPREADS))

    def test_cheap_instruments_pass(self):
        self.assertFalse(_cost_blocks("capital_com", "BTCUSD", {}, SPREADS))
        self.assertFalse(_cost_blocks("capital_com", "US30", {}, SPREADS))

    def test_instrument_specific_minimum_distance_is_used(self):
        # GOLD's minimum is 0.105%, a tenth of the default — so its tiny
        # spread is judged against a correspondingly tighter stop.
        spreads = {"GOLD": 0.006}

        self.assertFalse(_cost_blocks("capital_com", "GOLD", MIN_DIST, spreads))

    def test_missing_spread_data_never_blocks(self):
        self.assertFalse(_cost_blocks("capital_com", "UNKNOWN", {}, SPREADS))

    def test_other_platforms_are_untouched(self):
        self.assertFalse(_cost_blocks("kraken", "APTUSD", {}, SPREADS))


if __name__ == "__main__":
    unittest.main()
