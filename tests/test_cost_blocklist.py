from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.spot_trading import autotrade


class CostBlockedPairsTest(IsolatedAsyncioTestCase):
    """The dynamic cost filter reads a broker quote, and a missing quote
    scored as zero cost — which let 33 trades open on these instruments
    in August 2026 for -74.55 USD. A named list cannot be switched off
    by absent data."""

    def test_the_audited_instruments_are_all_listed(self):
        for pair in ("APTUSD", "AAVEUSD", "ATOMUSD", "ADAUSD", "LTCUSD",
                     "DOTUSD", "XRPUSD", "LINKUSD", "SOLUSD", "AVAXUSD",
                     "PALLADIUM"):
            with self.subTest(pair=pair):
                self.assertIn(pair, autotrade._COST_BLOCKED_PAIRS)

    def test_a_tradeable_instrument_is_not_listed(self):
        for pair in ("US500", "EURUSD", "BTCUSD", "GOLD"):
            with self.subTest(pair=pair):
                self.assertNotIn(pair, autotrade._COST_BLOCKED_PAIRS)

    async def test_evaluate_pair_refuses_a_blocked_instrument(self):
        # No platform call should be needed to reject it.
        result = await autotrade.evaluate_pair(
            object(), "DOTUSD", strategy_name="donchian_breakout",
            resolution="1h", stop_atr=1.0, rr=1.5, lookback_bars=240,
        )
        self.assertIsNone(result)

    async def test_execute_intent_refuses_a_blocked_instrument(self):
        intent = SimpleNamespace(
            pair="PALLADIUM", direction=1, strategy="keltner_breakout",
            stop_loss=1.0, take_profit=2.0,
        )
        result = await autotrade.execute_intent(object(), intent, 1.0)
        self.assertFalse(result.accepted)
        self.assertIn("cost-blocked", result.error)


if __name__ == "__main__":
    unittest.main()
