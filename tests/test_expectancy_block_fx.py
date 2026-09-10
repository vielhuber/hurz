from __future__ import annotations

import unittest

from app.spot_trading.trading_blocks import (
    BLOCKED_PAIRS,
    EXPECTANCY_BLOCKED_PAIRS,
    SHORT_BLOCKED_PAIRS,
)


class ExpectancyBlockedFxTest(unittest.TestCase):
    """Section 192 blocked three FX pairs the entry guards and the
    selector both consult through BLOCKED_PAIRS."""

    def test_the_three_flagged_pairs_are_blocked(self) -> None:
        for pair in ("AUDUSD", "GBPCAD", "GBPUSD"):
            self.assertIn(pair, EXPECTANCY_BLOCKED_PAIRS)
            self.assertIn(pair, BLOCKED_PAIRS)

    def test_the_earlier_expectancy_block_is_kept(self) -> None:
        self.assertIn("AU200", BLOCKED_PAIRS)

    def test_neighbouring_fx_pairs_are_not_blocked(self) -> None:
        for pair in ("EURUSD", "NZDUSD", "EURAUD", "AUDNZD", "USDCHF"):
            self.assertNotIn(pair, BLOCKED_PAIRS)

    def test_the_block_is_directionless(self) -> None:
        # A full block, not a short block — both directions are refused.
        self.assertFalse(SHORT_BLOCKED_PAIRS & {"AUDUSD", "GBPCAD", "GBPUSD"})


if __name__ == "__main__":
    unittest.main()
