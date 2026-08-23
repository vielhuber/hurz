from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.platforms import OrderConstraints
from app.platforms.capital_com import CapitalComPlatform
from app.spot_trading.position_sizing import calculate_position_size
from scripts.spot_backtest import _simulate_trades


class RulesPlatform(CapitalComPlatform):
    def __init__(self, rules: dict) -> None:
        super().__init__(
            credentials={
                "api_key": "test",
                "identifier": "test",
                "password": "test",
            },
            paper_trade_only=False,
        )
        self.rules = rules
        self.posts = []

    async def _get_dealing_rules(self, epic):
        return self.rules

    async def _raw_request(self, method, path, **kwargs):
        self.posts.append((method, path, kwargs))
        return {"dealReference": None}


class MarketRulesPlatform(CapitalComPlatform):
    async def _raw_request(self, method, path, **kwargs):
        return {
            "dealingRules": {
                "minDealSize": {"value": 1},
                "maxDealSize": {"value": 9984},
                "minSizeIncrement": {"value": 1},
            },
            "snapshot": {"bid": 1.68, "offer": 1.69},
        }


class PositionSizingTest(unittest.TestCase):
    def test_size_targets_configured_dollar_risk(self) -> None:
        result = calculate_position_size(
            entry_price=100.0,
            stop_loss=98.0,
            target_risk=3.0,
            notional_cap=250.0,
            size_increment=0.1,
        )

        self.assertFalse(result.skipped)
        self.assertEqual(1.5, result.size)
        self.assertEqual(3.0, result.planned_risk)

    def test_notional_cap_limits_size(self) -> None:
        result = calculate_position_size(
            entry_price=100.0,
            stop_loss=99.0,
            target_risk=3.0,
            notional_cap=250.0,
            size_increment=0.1,
        )

        self.assertFalse(result.skipped)
        self.assertEqual(2.5, result.size)
        self.assertEqual(250.0, result.notional)

    def test_size_is_rounded_down_to_broker_increment(self) -> None:
        result = calculate_position_size(
            entry_price=10.0,
            stop_loss=9.0,
            target_risk=3.0,
            notional_cap=250.0,
            size_increment=0.4,
        )

        self.assertFalse(result.skipped)
        self.assertEqual(2.8, result.size)
        self.assertLessEqual(result.planned_risk, 3.0)

    def test_max_deal_size_is_an_additional_cap(self) -> None:
        result = calculate_position_size(
            entry_price=10.0,
            stop_loss=9.0,
            target_risk=3.0,
            notional_cap=250.0,
            size_increment=0.1,
            max_size=2.5,
        )

        self.assertFalse(result.skipped)
        self.assertEqual(2.5, result.size)

    def test_minimum_size_collision_skips_instead_of_increasing_size(self) -> None:
        result = calculate_position_size(
            entry_price=25299.0,
            stop_loss=25000.0,
            target_risk=3.0,
            notional_cap=250.0,
            min_size=0.01,
            size_increment=0.01,
        )

        self.assertTrue(result.skipped)
        self.assertIsNone(result.size)
        self.assertIn("minimum size", result.reason or "")

    def test_atom_trade_uses_broker_reference_price_before_fill(self) -> None:
        result = calculate_position_size(
            entry_price=1.6892,
            stop_loss=1.65831429,
            target_risk=3.0,
            notional_cap=250.0,
            min_size=1.0,
            size_increment=1.0,
            max_size=9984.0,
        )

        self.assertEqual(97.0, result.size)
        self.assertAlmostEqual(2.99591387, result.planned_risk or 0.0)

    def test_backtest_uses_shared_sizing_and_dollar_pnl(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        frame = pd.DataFrame([
            {
                "timestamp": timestamp,
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "atr_14": 1.0,
            },
            {
                "timestamp": timestamp,
                "open": 100.0,
                "high": 100.5,
                "low": 99.0,
                "close": 99.0,
                "atr_14": 1.0,
            },
        ])

        outcomes = _simulate_trades(
            "TEST",
            frame,
            [SimpleNamespace(index=0, direction=1)],
            rr=1.5,
            stop_atr_mult=1.0,
            max_hold_bars=1,
            target_risk=3.0,
            notional_cap=250.0,
            constraints=OrderConstraints(size_increment=1.0),
        )

        self.assertEqual(1, len(outcomes))
        self.assertEqual(2.0, outcomes[0].size)
        self.assertEqual(2.0, outcomes[0].planned_risk)
        self.assertEqual(-2.0, outcomes[0].realized_pnl)


class CapitalComSizingGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_order_constraints_include_max_deal_size(self) -> None:
        platform = MarketRulesPlatform()

        constraints = await platform.order_constraints("ATOMUSD")

        self.assertEqual(1.0, constraints.min_size)
        self.assertEqual(1.0, constraints.size_increment)
        self.assertEqual(9984.0, constraints.max_size)

    async def test_place_order_does_not_raise_size_to_broker_minimum(self) -> None:
        platform = RulesPlatform({
            "min_size": 0.01,
            "max_size": 10.0,
            "size_increment": 0.01,
        })

        result = await platform.place_order(
            asset="HK50",
            direction=1,
            size=0.0098,
            stop_loss=25000.0,
            take_profit=26000.0,
        )

        self.assertFalse(result.accepted)
        self.assertIn("will not be increased", result.error or "")
        self.assertEqual([], platform.posts)

    async def test_place_order_caps_and_rounds_down_max_deal_size(self) -> None:
        platform = RulesPlatform({
            "min_size": 1.0,
            "max_size": 2.5,
            "size_increment": 1.0,
        })

        result = await platform.place_order(
            asset="TEST",
            direction=1,
            size=4.0,
            stop_loss=9.0,
            take_profit=12.0,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(2.0, result.size)
        self.assertEqual("POST", platform.posts[0][0])
        self.assertEqual(2.0, platform.posts[0][2]["body"]["size"])


if __name__ == "__main__":
    unittest.main()
