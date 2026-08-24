from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pandas as pd

from app.platforms import OrderResult, PreparedOrder
from app.platforms.capital_com import CapitalComPlatform
from app.spot_trading import autotrade, journal, pair_selector
from app.spot_trading.autotrade import TradeIntent
from scripts.spot_backtest import _simulate_trades


class HighSpreadPlatform:
    name = "capital_com"
    demo = True
    paper_trade_only = False

    def __init__(self, stop_event: asyncio.Event,
                 round_trip_cost: float = 0.25) -> None:
        self.stop_event = stop_event
        self.round_trip_cost = round_trip_cost
        self.orders = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def list_positions(self):
        self.stop_event.set()
        return []

    async def min_stop_distance(self, pair, ref_price):
        return 0.0

    async def prepare_order(
        self, *, asset, direction, reference_price, stop_loss, take_profit,
    ):
        return PreparedOrder(
            reference_price,
            stop_loss,
            take_profit,
            round_trip_cost=self.round_trip_cost,
        )

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return OrderResult(accepted=True)


class QuotedCapitalPlatform(CapitalComPlatform):
    async def _get_dealing_rules(self, epic):
        return {
            "bid": 99.9,
            "offer": 100.1,
            "min_size": 0.0,
            "max_size": 0.0,
            "size_increment": 0.0,
        }


class TradeCostFilterTest(TestCase):
    def test_backtest_skips_trade_above_cost_limit(self) -> None:
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
                "high": 102.0,
                "low": 99.5,
                "close": 101.5,
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
            fee_rate=0.00125,
        )

        self.assertEqual([], outcomes)


class LiveTradeCostFilterTest(IsolatedAsyncioTestCase):
    async def test_capital_order_exposes_full_spread_as_round_trip_cost(self) -> None:
        prepared = await QuotedCapitalPlatform().prepare_order(
            asset="TEST",
            direction=1,
            reference_price=100.0,
            stop_loss=99.0,
            take_profit=101.5,
        )

        self.assertAlmostEqual(0.2, prepared.round_trip_cost)

    async def _run(self, platform, stop_event):
        intent = TradeIntent(
            pair="TEST",
            direction=1,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit=101.5,
            strategy="donchian_breakout",
            confidence=1.0,
            bar_time=datetime.now(timezone.utc),
        )

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs", lambda *args, **kwargs: [{
                    "pair": "TEST",
                    "platform": "capital_com",
                    "strategy": "donchian_breakout",
                    "resolution": "1h",
                }]), \
                patch.object(journal, "list_unresolved_open", lambda platform=None: []), \
                patch.object(journal, "record") as record, \
                patch.object(autotrade, "evaluate_pair", return_value=intent), \
                patch.object(autotrade.subprocess, "Popen", lambda *args, **kwargs: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
            )
        return record

    async def test_moderate_cost_widens_the_stop_instead_of_skipping(self) -> None:
        stop_event = asyncio.Event()
        # 0.15 cost on a 1.0 stop is 15% — over the limit, but a stop of
        # 1.5 brings it to exactly 10%, inside the 2x widening cap.
        platform = HighSpreadPlatform(stop_event, round_trip_cost=0.15)

        await self._run(platform, stop_event)

        self.assertEqual(1, len(platform.orders))
        order = platform.orders[0]
        self.assertAlmostEqual(98.5, order["stop_loss"])
        # R:R of 1.5 survives the widening.
        self.assertAlmostEqual(102.25, order["take_profit"])

    async def test_unreachable_cost_still_skips(self) -> None:
        stop_event = asyncio.Event()
        # 0.25 cost would need a stop of 2.5 — beyond the 2x cap, so the
        # trade is dropped rather than handed a stop it never asked for.
        platform = HighSpreadPlatform(stop_event, round_trip_cost=0.25)

        record = await self._run(platform, stop_event)

        self.assertEqual([], platform.orders)
        self.assertEqual(1, record.call_count)
        self.assertIn("round-trip cost", record.call_args.args[1].error or "")


if __name__ == "__main__":
    import unittest

    unittest.main()


class AuditedCostFallbackTest(TestCase):
    """A closed market returns no quote, so the broker reports a spread
    of zero — and a zero waves the trade straight past the cost filter.
    The audited spread keeps the filter alive in that case."""

    def setUp(self) -> None:
        autotrade._SPREAD_PERCENT_CACHE = None

    def tearDown(self) -> None:
        autotrade._SPREAD_PERCENT_CACHE = None

    def test_audited_spread_is_converted_to_price_units(self):
        autotrade._SPREAD_PERCENT_CACHE = {"APTUSD": 5.0}

        self.assertAlmostEqual(
            0.1, autotrade._audited_round_trip_cost("APTUSD", 2.0),
        )

    def test_unknown_instrument_returns_zero(self):
        autotrade._SPREAD_PERCENT_CACHE = {"APTUSD": 5.0}

        self.assertEqual(
            0.0, autotrade._audited_round_trip_cost("UNKNOWN", 100.0),
        )

    def test_missing_audit_file_does_not_raise(self):
        with patch.object(autotrade, "_SPREAD_PERCENT_PATH", "/nonexistent"):
            self.assertEqual(
                0.0, autotrade._audited_round_trip_cost("APTUSD", 2.0),
            )

    def test_zero_reference_price_returns_zero(self):
        autotrade._SPREAD_PERCENT_CACHE = {"APTUSD": 5.0}

        self.assertEqual(
            0.0, autotrade._audited_round_trip_cost("APTUSD", 0.0),
        )
