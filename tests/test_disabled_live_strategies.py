from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase

from app.spot_trading.autotrade import TradeIntent, evaluate_pair, execute_intent


class UnexpectedPlatformCall:
    async def fetch_history(self, *args, **kwargs):
        raise AssertionError("disabled strategy fetched market data")

    async def place_order(self, *args, **kwargs):
        raise AssertionError("disabled strategy placed an order")


class DisabledLiveStrategyTest(IsolatedAsyncioTestCase):
    async def test_v3_is_blocked_before_live_evaluation(self):
        intent = await evaluate_pair(
            UnexpectedPlatformCall(),
            "DOTUSD",
            strategy_name="donchian_breakout_v3",
            resolution="1h",
            stop_atr=1.0,
            rr=3.5,
            lookback_bars=240,
            apply_venue_min=True,
        )

        self.assertIsNone(intent)

    async def test_v3_is_rejected_before_order_submission(self):
        intent = TradeIntent(
            pair="DOTUSD",
            direction=1,
            entry_price=1.0,
            stop_loss=0.9,
            take_profit=1.35,
            strategy="donchian_breakout_v3",
            confidence=1.0,
            bar_time=datetime.now(timezone.utc),
        )

        result = await execute_intent(UnexpectedPlatformCall(), intent, 1.0)

        self.assertFalse(result.accepted)
        self.assertEqual("disabled live strategy: donchian_breakout_v3", result.error)
