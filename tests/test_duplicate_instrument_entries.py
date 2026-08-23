from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import OrderResult, PreparedOrder
from app.spot_trading import autotrade, journal, pair_selector
from app.spot_trading.autotrade import TradeIntent


class EmptyPositionPlatform:
    name = "capital_com"
    demo = True
    paper_trade_only = False

    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
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
        return PreparedOrder(reference_price, stop_loss, take_profit)

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return OrderResult(
            accepted=True,
            deal_id=f"deal-{len(self.orders)}",
            asset=kwargs["asset"],
            direction=kwargs["direction"],
            size=kwargs["size"],
        )


class DuplicateInstrumentEntryTest(IsolatedAsyncioTestCase):
    async def test_second_strategy_cannot_issue_same_pair_bar(self):
        stop_event = asyncio.Event()
        platform = EmptyPositionPlatform(stop_event)
        bar_time = datetime.now(timezone.utc)
        active_pairs = [
            {
                "pair": "BTCUSD",
                "platform": "capital_com",
                "strategy": "donchian_breakout",
                "resolution": "1h",
            },
            {
                "pair": "BTCUSD",
                "platform": "capital_com",
                "strategy": "turtle_breakout",
                "resolution": "1h",
            },
        ]

        async def evaluate_pair(*args, strategy_name, **kwargs):
            return TradeIntent(
                pair="BTCUSD",
                direction=1,
                entry_price=75000.0,
                stop_loss=74000.0,
                take_profit=76500.0,
                strategy=strategy_name,
                confidence=1.0,
                bar_time=bar_time,
            )

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs",
                             lambda *args, **kwargs: active_pairs), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: []), \
                patch.object(journal, "record"), \
                patch.object(autotrade, "evaluate_pair", evaluate_pair), \
                patch.object(autotrade.subprocess, "Popen",
                             lambda *args, **kwargs: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
            )

        self.assertEqual(1, len(platform.orders))


if __name__ == "__main__":
    import unittest

    unittest.main()
