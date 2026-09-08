from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.spot_trading import autotrade
from app.spot_trading.strategy_parameters import VENUE_MIN_STOP_FRACTION
from tests.test_min_stop_distance import TightStopPlatform, _signal


class GoldLikePlatform(TightStopPlatform):
    """GOLD's venue minimum is 0.1 % of price; every other instrument's is 1 %."""

    async def min_stop_distance(self, asset, *, ref_price):
        return ref_price * 0.001


class VenueMinimumWideningTest(IsolatedAsyncioTestCase):
    """Live widened stops only to what the venue demanded, so GOLD's 2-ATR
    stop stayed under the 1 % cost floor and was refused, while every
    backtest widened it to 1.05 % and measured it positive twice."""

    async def _evaluate(self, platform, **kwargs):
        gate = SimpleNamespace(blocked=False, adx=42.0, reason="")
        with patch.object(autotrade, "get_strategy", return_value=_signal), \
                patch("app.spot_trading.regime.gate", return_value=gate):
            return await autotrade.evaluate_pair(
                platform, "GOLD", strategy_name="donchian_breakout",
                resolution="1h", stop_atr=1.0, rr=1.5, lookback_bars=240,
                apply_venue_min=True, **kwargs,
            )

    async def test_a_sub_floor_stop_is_widened_to_the_shared_minimum(self):
        rejected = []
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0.01"}):
            intent = await self._evaluate(
                GoldLikePlatform(),
                on_rejected_intent=lambda intent, reason: rejected.append(reason),
            )
        self.assertIsNotNone(intent)
        self.assertEqual([], rejected)
        distance = abs(intent.entry_price - intent.stop_loss) / intent.entry_price
        self.assertAlmostEqual(VENUE_MIN_STOP_FRACTION, distance, places=6)

    async def test_the_target_keeps_the_reward_ratio_after_widening(self):
        intent = await self._evaluate(GoldLikePlatform())
        stop = abs(intent.entry_price - intent.stop_loss)
        target = abs(intent.take_profit - intent.entry_price)
        self.assertAlmostEqual(1.5, target / stop, places=6)


if __name__ == "__main__":
    unittest.main()
