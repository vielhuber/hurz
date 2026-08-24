from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import OrderResult, PlatformAPIError, Position
from app.platforms.capital_com import CapitalComPlatform
from app.spot_trading import autotrade, journal, pair_selector


def stale_position_and_row():
    now = datetime.now(timezone.utc)
    position = Position(
        id="position-1", asset="AUDUSD", direction=1, size=1.0,
        entry_price=0.65, stop_loss=0.64, take_profit=0.67,
        opened_at=now - timedelta(hours=25),
        meta={
            "position": {
                "dealId": "position-1",
                "workingOrderId": "journal-1",
            },
            "market": {"marketStatus": "CLOSED"},
        },
    )
    row = {
        "id": 776, "pair": "AUDUSD", "strategy": "donchian_breakout",
        "direction": 1, "deal_id": "journal-1", "fill_price": 0.65,
        "entry_price": 0.65, "stop_loss": 0.64, "take_profit": 0.67,
        "size": 1.0, "bar_time": now - timedelta(hours=25),
        "created_at": now - timedelta(hours=25),
    }
    return position, row


class ClosedMarketPlatform:
    name = "capital_com"
    demo = True
    paper_trade_only = False

    def __init__(
        self,
        position: Position,
        stop_event: asyncio.Event,
        market_statuses=None,
    ) -> None:
        self.position = position
        self.stop_event = stop_event
        self.market_statuses = market_statuses or ["CLOSED"]
        self.position_fetches = 0
        self.close_calls = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def list_positions(self):
        status = self.market_statuses[min(
            self.position_fetches,
            len(self.market_statuses) - 1,
        )]
        self.position.meta["market"]["marketStatus"] = status
        self.position_fetches += 1
        if self.position_fetches >= len(self.market_statuses):
            self.stop_event.set()
        return [self.position]

    async def close_position(self, deal_id: str) -> OrderResult:
        self.close_calls.append(deal_id)
        return OrderResult(accepted=False, deal_id=deal_id, error="HTTP 400")


class RejectingClosePlatform(CapitalComPlatform):
    def __init__(self) -> None:
        super().__init__(
            credentials={
                "api_key": "test",
                "identifier": "test",
                "password": "test",
            },
        )

    async def _raw_request(self, method, path, **kwargs):
        raise PlatformAPIError(
            f"capital_com: {method} {path} returned 400",
            status=400,
            response_text='{"errorCode":"error.close.rejected"}',
        )


class StaleExitMarketStatusTest(IsolatedAsyncioTestCase):
    async def test_stale_exit_does_not_submit_close_when_market_is_closed(self):
        stop_event = asyncio.Event()
        position, row = stale_position_and_row()
        platform = ClosedMarketPlatform(position, stop_event)
        log_messages = []

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs", lambda *a, **kw: [{
                    "pair": "AUDUSD",
                    "platform": "capital_com",
                    "strategy": "donchian_breakout",
                    "resolution": "1h",
                }]), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: [row]), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=[]), \
                patch.object(autotrade, "_safe_log", log_messages.append), \
                patch.object(autotrade.subprocess, "Popen", lambda *a, **kw: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
            )

        self.assertEqual([], platform.close_calls)
        self.assertTrue(any(
            "marketStatus=CLOSED" in message for message in log_messages
        ))

    async def test_stale_exit_waits_then_submits_close_on_reopening(self):
        stop_event = asyncio.Event()
        position, row = stale_position_and_row()
        platform = ClosedMarketPlatform(
            position,
            stop_event,
            ["CLOSED", "CLOSED", "TRADEABLE"],
        )
        log_messages = []

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs", lambda *a, **kw: [{
                    "pair": "AUDUSD",
                    "platform": "capital_com",
                    "strategy": "donchian_breakout",
                    "resolution": "1h",
                }]), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: [row]), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=[]), \
                patch.object(autotrade, "_safe_log", log_messages.append), \
                patch.object(autotrade.subprocess, "Popen", lambda *a, **kw: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=0.001,
                stop_event=stop_event,
            )

        self.assertEqual(["position-1"], platform.close_calls)
        self.assertEqual(1, sum(
            "marketStatus=CLOSED" in message for message in log_messages
        ))

    async def test_market_closure_clears_failed_close_cooldown(self):
        stop_event = asyncio.Event()
        position, row = stale_position_and_row()
        platform = ClosedMarketPlatform(
            position,
            stop_event,
            ["TRADEABLE", "CLOSED", "TRADEABLE"],
        )

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs", lambda *a, **kw: [{
                    "pair": "AUDUSD",
                    "platform": "capital_com",
                    "strategy": "donchian_breakout",
                    "resolution": "1h",
                }]), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: [row]), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=[]), \
                patch.object(autotrade, "_safe_log", lambda message: None), \
                patch.object(autotrade.subprocess, "Popen", lambda *a, **kw: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=0.001,
                stop_event=stop_event,
            )

        self.assertEqual(["position-1", "position-1"], platform.close_calls)

    async def test_close_rejection_includes_broker_response_body(self):
        result = await RejectingClosePlatform().close_position("position-1")

        self.assertFalse(result.accepted)
        self.assertIn("returned 400", result.error)
        self.assertIn("error.close.rejected", result.error)


if __name__ == "__main__":
    import unittest
    unittest.main()
