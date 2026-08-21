from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app.spot_trading.regime import trend_floor
from app.spot_trading.strategy_parameters import risk_reward_for
from app.spot_trading.walk_forward import compute_segment_stability
from app.strategies import get_strategy
from scripts import spot_backtest


class StrategyParameterTest(unittest.TestCase):
    def test_donchian_aliases_use_canonical_risk_reward(self) -> None:
        self.assertEqual(1.5, risk_reward_for("donchian_breakout"))
        self.assertEqual(2.5, risk_reward_for("donchian_breakout_v2"))
        self.assertEqual(3.5, risk_reward_for("donchian_breakout_v3"))

    def test_donchian_variants_use_live_adx_floors(self) -> None:
        environment = {
            "HURZ_REGIME_ADX_TREND": "30",
            "HURZ_REGIME_ADX_TREND_CORE": "35",
        }
        with patch.dict(os.environ, environment, clear=False):
            self.assertEqual(35.0, trend_floor("donchian_breakout"))
            self.assertEqual(30.0, trend_floor("donchian_breakout_v2"))
            self.assertEqual(30.0, trend_floor("donchian_breakout_v3"))

    def test_v3_remains_resolvable_for_backtests(self) -> None:
        self.assertIs(get_strategy("donchian_breakout"), get_strategy(
            "donchian_breakout_v3",
        ))

    def test_backtest_cli_resolves_v3_canonical_risk_reward(self) -> None:
        argv = [
            "spot_backtest.py",
            "--platform", "capital_com",
            "--strategy", "donchian_breakout_v3",
        ]
        with patch.object(sys, "argv", argv):
            args = spot_backtest._parse_args()
        self.assertEqual(3.5, args.rr)

    def test_strategy_override_wins_over_cli_default(self) -> None:
        argv = [
            "spot_backtest.py",
            "--strategy", "donchian_breakout_v3",
            "--rr", "1.5",
        ]
        with patch.object(sys, "argv", argv):
            args = spot_backtest._parse_args()
        self.assertEqual(3.5, args.rr)

    def test_stability_uses_v3_live_adx_floor(self) -> None:
        data_frame = pd.DataFrame({
            "close": [100.0, 104.0, 104.0],
            "high": [100.0, 104.0, 104.0],
            "low": [100.0, 100.0, 104.0],
            "atr_14": [1.0, 1.0, 1.0],
            "adx_14": [29.0, 29.0, 29.0],
        })

        def strategy(segment: pd.DataFrame, params: dict) -> list:
            return [SimpleNamespace(index=0, direction=1)]

        result = compute_segment_stability(
            data_frame,
            strategy,
            segments=1,
            rr=3.5,
            min_segment_bars=2,
            strategy_name="donchian_breakout_v3",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
