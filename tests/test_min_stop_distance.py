from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import Bar
from app.spot_trading import autotrade


class TightStopPlatform:
    """Bars whose ATR is tiny relative to price, so the derived stop
    lands well inside the cost floor."""

    def __init__(self, price: float = 100.0) -> None:
        self.price = price

    async def fetch_history(self, *args, **kwargs):
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        bars = []
        for i in range(120):
            # A 0.02 range on a price of 100 gives an ATR around 0.02,
            # i.e. a 0.02% stop — far below the 1% floor.
            close = self.price + (0.01 if i % 2 else -0.01)
            bars.append(Bar(
                timestamp=start + timedelta(hours=i),
                open=close, high=close + 0.01,
                low=close - 0.01, close=close, volume=1000.0,
            ))
        return bars

    async def min_stop_distance(self, *args, **kwargs):
        return 0.0


def _signal(df, params):
    return [SimpleNamespace(index=len(df) - 1, direction=1, confidence=1.0)]


class MinStopFractionTest(unittest.TestCase):
    def test_default_floor_is_one_percent(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0.01, autotrade._min_stop_fraction())

    def test_environment_override_wins(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0.02"}):
            self.assertEqual(0.02, autotrade._min_stop_fraction())

    def test_floor_can_be_disabled(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0"}):
            self.assertEqual(0.0, autotrade._min_stop_fraction())

    def test_unparsable_value_falls_back_to_default(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "wide"}):
            self.assertEqual(0.01, autotrade._min_stop_fraction())


class StopFloorRejectionTest(IsolatedAsyncioTestCase):
    async def _evaluate(self) -> object:
        passing_gate = SimpleNamespace(blocked=False, adx=42.0, reason="")
        with patch.object(autotrade, "get_strategy", return_value=_signal), \
                patch("app.spot_trading.regime.gate", return_value=passing_gate):
            return await autotrade.evaluate_pair(
                TightStopPlatform(), "TESTUSD",
                strategy_name="donchian_breakout", resolution="1h",
                stop_atr=1.0, rr=1.5, lookback_bars=240,
            )

    async def test_signal_with_a_stop_inside_the_cost_floor_is_dropped(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0.01"}):
            self.assertIsNone(await self._evaluate())

    async def test_same_signal_survives_once_the_floor_is_disabled(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0"}):
            intent = await self._evaluate()

        self.assertIsNotNone(intent)
        # Confirms the drop above came from the floor and not from the
        # bars failing to produce a signal at all.
        self.assertLess(
            abs(intent.entry_price - intent.stop_loss) / intent.entry_price,
            0.01,
        )


if __name__ == "__main__":
    unittest.main()
