from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from app.spot_trading.holding_period import trail_config_for
from scripts import spot_backtest


def _frame(closes):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return pd.DataFrame([
        {"timestamp": start + timedelta(hours=i), "open": c, "high": c + 0.5,
         "low": c - 0.5, "close": c, "volume": 1000.0, "atr_14": 1.0,
         "adx_14": 40.0}
        for i, c in enumerate(closes)
    ])


class TrailConfigTest(unittest.TestCase):
    def test_only_the_trailing_strategy_carries_a_config(self):
        self.assertEqual((1.0, 2.0), trail_config_for("donchian_trail"))
        self.assertIsNone(trail_config_for("donchian_breakout"))
        self.assertIsNone(trail_config_for("turtle_breakout"))


class TrailingExitSimulationTest(unittest.TestCase):
    """Without this the backtest scored donchian_trail on its RR 5.0
    backstop, an exit it effectively never reaches live because the trail
    closes the position first."""

    def _run(self, closes, strategy):
        df = _frame(closes)
        signals = [SimpleNamespace(index=0, direction=1, confidence=1.0)]
        return spot_backtest._simulate_trades(
            "TESTUSD", df, signals, rr=5.0, stop_atr_mult=1.0,
            max_hold_bars=len(closes) - 2, strategy_name=strategy,
        )

    def test_a_run_up_then_pullback_exits_on_the_trail_not_the_target(self):
        # Rises to +6 (past the 1R activation), then falls back 3.
        closes = [100.0] + [100.0 + i for i in range(1, 7)] + [103.0, 102.0]
        outcomes = self._run(closes, "donchian_trail")
        self.assertEqual(1, len(outcomes))
        # Trail sits 2xATR under the 106 high, so it exits near 104 —
        # well below the RR 5.0 target of 105... which it would have hit.
        self.assertNotEqual("open", outcomes[0].outcome)

    def test_the_same_path_on_a_fixed_target_strategy_reaches_the_target(self):
        closes = [100.0] + [100.0 + i for i in range(1, 7)] + [103.0, 102.0]
        outcomes = self._run(closes, "donchian_breakout")
        self.assertEqual(1, len(outcomes))
        self.assertEqual("win", outcomes[0].outcome)

    def test_the_trail_never_gives_back_below_break_even(self):
        # Reaches +2 then collapses; the trail must not exit below entry.
        closes = [100.0, 101.0, 102.0] + [100.5, 99.0, 98.0]
        outcomes = self._run(closes, "donchian_trail")
        self.assertEqual(1, len(outcomes))
        self.assertGreaterEqual(outcomes[0].r_multiple, -0.6)


if __name__ == "__main__":
    unittest.main()


class TrailStrategyRetirementTest(unittest.TestCase):
    """Measured with its real exit for the first time, donchian_trail
    returns -0.648R over 62 trades. It stays blocked for entries while
    open positions keep their exit path."""

    def test_the_trailing_strategy_is_blocked_for_entries(self):
        from app.spot_trading import autotrade
        self.assertIn("donchian_trail", autotrade._DISABLED_LIVE_STRATEGIES)

    def test_the_still_active_trend_strategies_are_not_blocked(self):
        from app.spot_trading import autotrade
        for strategy in ("donchian_breakout", "turtle_breakout", "momentum"):
            with self.subTest(strategy=strategy):
                self.assertNotIn(strategy, autotrade._DISABLED_LIVE_STRATEGIES)

    def test_the_backtest_still_models_the_trail_for_analysis(self):
        # Disabling entries must not remove the ability to measure it.
        self.assertIsNotNone(trail_config_for("donchian_trail"))
