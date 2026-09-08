from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.spot_trading import autotrade
from app.spot_trading.trading_blocks import (
    BLOCKED_PAIRS, SHORT_BLOCKED_PAIRS, direction_blocked,
)


class ShortBlockedPairsTest(IsolatedAsyncioTestCase):
    """Commodity shorts lost on the router-passed path in both disjoint
    walk-forward samples while the longs did not; the short side is
    refused for entries, the long side and the instrument stay active."""

    def test_commodities_are_short_blocked_only(self):
        for pair in ("OIL_CRUDE", "OIL_BRENT", "GOLD", "SILVER", "COPPER"):
            self.assertIn(pair, SHORT_BLOCKED_PAIRS)
            self.assertNotIn(pair, BLOCKED_PAIRS)
            self.assertTrue(direction_blocked(pair, -1))
            self.assertFalse(direction_blocked(pair, +1))
        self.assertFalse(direction_blocked("US500", -1))

    async def test_execute_intent_refuses_commodity_short(self):
        intent = SimpleNamespace(pair="SILVER", direction=-1, strategy="donchian_breakout")
        result = await autotrade.execute_intent(SimpleNamespace(), intent, 1.0)
        self.assertFalse(result.accepted)
        self.assertIn("short-blocked", result.error)

    async def test_execute_intent_keeps_commodity_long(self):
        intent = SimpleNamespace(pair="SILVER", direction=+1, strategy="donchian_breakout")
        with self.assertRaises(AttributeError):
            await autotrade.execute_intent(SimpleNamespace(), intent, 1.0)


if __name__ == "__main__":
    unittest.main()
