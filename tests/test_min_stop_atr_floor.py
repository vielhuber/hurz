from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import Bar
from app.spot_trading import autotrade
from app.spot_trading.autotrade import (
    _DEFAULT_MIN_STOP_ATR_MULTIPLE,
    _min_stop_atr_multiple,
)


class WideBarPlatform:
    """Bars volatile enough that 2 x ATR clears the venue minimum on its
    own — the least-pinned band section 190 refuses."""

    async def fetch_history(self, *args, **kwargs):
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        bars = []
        for i in range(120):
            close = 100.0 + (2.0 if i % 2 else -2.0)
            bars.append(Bar(
                timestamp=start + timedelta(hours=i),
                open=close, high=close + 2.0,
                low=close - 2.0, close=close, volume=1000.0,
            ))
        return bars

    async def min_stop_distance(self, *args, **kwargs):
        return 0.0


def _signal(df, params):
    return [SimpleNamespace(index=len(df) - 1, direction=1, confidence=1.0)]


class MinStopAtrFloorTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("HURZ_MIN_STOP_ATR_MULTIPLE", None)

    def test_default_is_three_atr(self) -> None:
        self.assertEqual(3.0, _DEFAULT_MIN_STOP_ATR_MULTIPLE)
        self.assertEqual(3.0, _min_stop_atr_multiple())

    def test_env_override_is_honoured(self) -> None:
        os.environ["HURZ_MIN_STOP_ATR_MULTIPLE"] = "2.5"

        self.assertEqual(2.5, _min_stop_atr_multiple())

    def test_zero_disables_the_floor(self) -> None:
        os.environ["HURZ_MIN_STOP_ATR_MULTIPLE"] = "0"

        self.assertEqual(0.0, _min_stop_atr_multiple())

    def test_unparseable_value_falls_back_to_the_default(self) -> None:
        os.environ["HURZ_MIN_STOP_ATR_MULTIPLE"] = "wide"

        self.assertEqual(_DEFAULT_MIN_STOP_ATR_MULTIPLE,
                         _min_stop_atr_multiple())

    def test_negative_value_cannot_invert_the_floor(self) -> None:
        os.environ["HURZ_MIN_STOP_ATR_MULTIPLE"] = "-4"

        self.assertEqual(0.0, _min_stop_atr_multiple())


class AtrFloorRejectionTest(IsolatedAsyncioTestCase):
    async def _evaluate(self, on_rejected_intent=None):
        gate = SimpleNamespace(blocked=False, adx=42.0, reason="")
        with patch.object(autotrade, "get_strategy", return_value=_signal), \
                patch("app.spot_trading.regime.gate", return_value=gate):
            return await autotrade.evaluate_pair(
                WideBarPlatform(), "TESTUSD",
                strategy_name="donchian_breakout", resolution="1h",
                stop_atr=2.0, rr=1.5, lookback_bars=240,
                on_rejected_intent=on_rejected_intent,
            )

    async def test_a_two_atr_stop_is_refused_by_the_three_atr_floor(self):
        rejected = []
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0"}):
            self.assertIsNone(await self._evaluate(
                on_rejected_intent=lambda intent, reason: rejected.append(
                    (intent, reason)
                ),
            ))

        self.assertEqual(1, len(rejected))
        self.assertIn("ATR floor", rejected[0][1])

    async def test_the_same_signal_survives_once_the_floor_is_disabled(self):
        with patch.dict(os.environ, {"HURZ_MIN_STOP_FRACTION": "0",
                                     "HURZ_MIN_STOP_ATR_MULTIPLE": "0"}):
            intent = await self._evaluate()

        self.assertIsNotNone(intent)


if __name__ == "__main__":
    unittest.main()
