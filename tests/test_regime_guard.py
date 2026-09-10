from __future__ import annotations

import unittest

from app.spot_trading.regime import decide


class RegimeGuardTest(unittest.TestCase):
    def test_missing_adx_blocks_classified_strategies(self) -> None:
        decision = decide("momentum", None)

        self.assertTrue(decision.blocked)
        self.assertEqual("unknown", decision.regime)

    def test_overextended_adx_blocks_trend_entries(self) -> None:
        decision = decide("donchian_breakout", 55.0)

        self.assertTrue(decision.blocked)
        self.assertEqual("overextended", decision.regime)

    def test_trend_band_below_the_ceiling_still_trades(self) -> None:
        decision = decide("donchian_breakout", 49.9)

        self.assertFalse(decision.blocked)
        self.assertEqual("strong-trend", decision.regime)

    def test_ceiling_does_not_touch_mean_reversion(self) -> None:
        decision = decide("bollinger_rev", 55.0)

        self.assertTrue(decision.blocked)
        self.assertEqual("trend", decision.regime)


if __name__ == "__main__":
    unittest.main()
