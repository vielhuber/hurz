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
                     "PALLADIUM", "CORN", "NATURALGAS"):
            with self.subTest(pair=pair):
                self.assertIn(pair, autotrade._COST_BLOCKED_PAIRS)

    def test_a_tradeable_instrument_is_not_listed(self):
        # WHEAT was audited at the same time and kept: 8.5 % mean cost
        # share against the 10 % ceiling.
        for pair in ("US500", "EURUSD", "BTCUSD", "GOLD", "WHEAT"):
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


class SelectorHonoursBlocklistTest(unittest.TestCase):
    """A pin bypasses the ranking's cost filter, so blocked instruments
    kept reappearing in the active file — CORN returned under two
    strategies on the first refresh after it was blocked."""

    def test_a_blocked_pair_is_dropped_even_when_pinned(self):
        from unittest.mock import patch
        from app.spot_trading import pair_selector

        pinned = [pair_selector.PairScore(
            platform="capital_com", strategy="donchian_breakout",
            resolution="1h", pair="CORN", n=50, win_rate=0.5,
            profit_factor=1.2, expectancy_R=0.1, sharpe=0.5, score=1.0,
            pinned=True,
        )]
        with patch.object(pair_selector, "_pinned_scores", return_value=pinned), \
                patch.object(pair_selector, "live_expectancy_veto", return_value={}), \
                patch.object(pair_selector, "strategy_expectancy_veto", return_value={}):
            import tempfile, os
            fd, path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            try:
                payload = pair_selector.persist_active_pairs(
                    [], top_n=5, out_path=path, platform="capital_com",
                )
            finally:
                os.unlink(path)
        self.assertEqual([], [p for p in payload["pairs"] if p["pair"] == "CORN"])
