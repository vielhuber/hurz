from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.spot_trading import autotrade
from app.spot_trading.trading_blocks import (
    BLOCKED_PAIRS, COST_BLOCKED_PAIRS, EXPECTANCY_BLOCKED_PAIRS,
)


class ExpectancyBlockedPairsTest(IsolatedAsyncioTestCase):
    """AU200 lost on the router-passed path in both disjoint walk-forward
    samples and on every live trade; it is blocked on expectancy, which
    is a different criterion from the cost audit and must not be mixed
    into that list."""

    def test_au200_is_blocked_on_expectancy_not_cost(self):
        self.assertIn("AU200", EXPECTANCY_BLOCKED_PAIRS)
        self.assertNotIn("AU200", COST_BLOCKED_PAIRS)

    def test_both_lists_feed_the_guards(self):
        self.assertTrue(COST_BLOCKED_PAIRS <= BLOCKED_PAIRS)
        self.assertTrue(EXPECTANCY_BLOCKED_PAIRS <= BLOCKED_PAIRS)
        self.assertIs(autotrade._BLOCKED_PAIRS, BLOCKED_PAIRS)

    async def test_evaluate_pair_refuses_au200(self):
        platform = SimpleNamespace(name="capital_com")
        intent = await autotrade.evaluate_pair(
            platform, "AU200", strategy_name="donchian_breakout",
            resolution="1h", stop_atr=2.0, rr=1.5, lookback_bars=240,
        )
        self.assertIsNone(intent)

    async def test_execute_intent_refuses_au200(self):
        intent = SimpleNamespace(pair="AU200", direction=1, strategy="donchian_breakout")
        result = await autotrade.execute_intent(SimpleNamespace(), intent, 1.0)
        self.assertFalse(result.accepted)
        self.assertIn("expectancy-blocked", result.error)


if __name__ == "__main__":
    unittest.main()
