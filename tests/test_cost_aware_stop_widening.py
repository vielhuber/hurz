from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.spot_trading.autotrade import TradeIntent, _widen_stop_and_target


def _intent(direction: int = 1) -> TradeIntent:
    # Entry 100, stop 1.0 away, target 1.5 away → R:R of 1.5.
    return TradeIntent(
        pair="DOTUSD", direction=direction,
        entry_price=100.0,
        stop_loss=99.0 if direction == 1 else 101.0,
        take_profit=101.5 if direction == 1 else 98.5,
        strategy="donchian_breakout", confidence=1.0,
        bar_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


class WidenStopAndTargetTest(unittest.TestCase):
    def test_long_keeps_risk_reward_intact(self):
        widened = _widen_stop_and_target(_intent(), 100.0, 2.0)

        self.assertEqual(98.0, widened.stop_loss)
        self.assertEqual(103.0, widened.take_profit)
        reward = abs(widened.take_profit - 100.0)
        risk = abs(100.0 - widened.stop_loss)
        self.assertAlmostEqual(1.5, reward / risk, places=9)

    def test_short_widens_in_the_other_direction(self):
        widened = _widen_stop_and_target(_intent(direction=-1), 100.0, 2.0)

        self.assertEqual(102.0, widened.stop_loss)
        self.assertEqual(97.0, widened.take_profit)

    def test_widening_halves_the_cost_share(self):
        cost = 0.30
        original = _intent()
        original_share = cost / abs(100.0 - original.stop_loss)

        widened = _widen_stop_and_target(original, 100.0, 2.0)
        widened_share = cost / abs(100.0 - widened.stop_loss)

        self.assertAlmostEqual(original_share / 2, widened_share, places=9)

    def test_degenerate_intent_is_rejected_rather_than_guessed(self):
        flat = TradeIntent(
            pair="DOTUSD", direction=1, entry_price=100.0,
            stop_loss=100.0, take_profit=100.0,
            strategy="donchian_breakout", confidence=1.0,
            bar_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

        self.assertIsNone(_widen_stop_and_target(flat, 100.0, 2.0))

    def test_zero_reward_is_rejected(self):
        no_reward = TradeIntent(
            pair="DOTUSD", direction=1, entry_price=100.0,
            stop_loss=99.0, take_profit=100.0,
            strategy="donchian_breakout", confidence=1.0,
            bar_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

        self.assertIsNone(_widen_stop_and_target(no_reward, 100.0, 2.0))


if __name__ == "__main__":
    unittest.main()
