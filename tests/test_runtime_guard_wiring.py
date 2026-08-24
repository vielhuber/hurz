from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import OrderResult, PreparedOrder
from app.spot_trading import autotrade, journal, pair_selector
from app.spot_trading.autotrade import TradeIntent
from app.spot_trading.risk_guard import DailyLoss


class GuardPlatform:
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


class RuntimeGuardWiringTest(IsolatedAsyncioTestCase):
    async def _run(
        self,
        pairs,
        *,
        max_concurrent=None,
        recent_issued=None,
        daily_loss=None,
        history_available=True,
    ):
        stop_event = asyncio.Event()
        platform = GuardPlatform(stop_event)
        bar_time = datetime.now(timezone.utc)

        async def evaluate_pair(_platform, pair, *, strategy_name, **kwargs):
            return TradeIntent(
                pair=pair,
                direction=1,
                entry_price=100.0,
                stop_loss=99.0,
                take_profit=101.5,
                strategy=strategy_name,
                confidence=1.0,
                bar_time=bar_time,
            )

        active = [{
            "pair": pair,
            "platform": "capital_com",
            "strategy": "donchian_breakout",
            "resolution": "1h",
        } for pair in pairs]
        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs",
                             lambda *args, **kwargs: active), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: []), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=(recent_issued or [])
                             if history_available else None), \
                patch.object(journal, "record") as record, \
                patch.object(autotrade, "evaluate_pair", evaluate_pair), \
                patch("app.spot_trading.risk_guard.daily_loss",
                      return_value=daily_loss or DailyLoss(0.0, 6.0, False, 0)), \
                patch.object(autotrade.subprocess, "Popen",
                             lambda *args, **kwargs: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
                max_concurrent=max_concurrent,
            )
        return platform, record

    async def test_concurrent_cap_counts_orders_opened_in_the_same_cycle(self):
        platform, record = await self._run(
            ["CORN", "WHEAT"],
            max_concurrent=1,
        )

        self.assertEqual(1, len(platform.orders))
        self.assertEqual(2, record.call_count)
        self.assertIn("max_concurrent=1", record.call_args.args[1].error)

    async def test_cluster_cap_journals_the_blocked_signal(self):
        platform, record = await self._run([
            "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD",
        ])

        self.assertEqual(3, len(platform.orders))
        self.assertEqual(4, record.call_count)
        self.assertIn("cluster 'crypto'", record.call_args.args[1].error)

    async def test_default_concurrent_cap_is_active(self):
        platform, record = await self._run([
            f"TEST{number}" for number in range(9)
        ])

        self.assertEqual(8, len(platform.orders))
        self.assertEqual(9, record.call_count)
        self.assertIn("max_concurrent=8", record.call_args.args[1].error)

    async def test_daily_cap_survives_a_process_restart(self):
        now = datetime.now(timezone.utc)
        platform, record = await self._run(
            ["CORN"],
            recent_issued=[now] * 100,
        )

        self.assertEqual([], platform.orders)
        self.assertEqual(1, record.call_count)
        self.assertIn("daily cap of 100", record.call_args.args[1].error)

    async def test_unreadable_daily_cap_history_blocks_and_records(self):
        platform, record = await self._run(
            ["CORN"],
            history_available=False,
        )

        self.assertEqual([], platform.orders)
        self.assertEqual(1, record.call_count)
        self.assertIn("daily-cap history unavailable", record.call_args.args[1].error)

    async def test_unreadable_daily_loss_journal_blocks_and_records(self):
        platform, record = await self._run(
            ["CORN"],
            daily_loss=DailyLoss(
                0.0,
                6.0,
                True,
                0,
                "journal unavailable",
            ),
        )

        self.assertEqual([], platform.orders)
        self.assertEqual(1, record.call_count)
        self.assertIn(
            "daily loss guard unavailable",
            record.call_args.args[1].error,
        )

    async def test_evaluation_rejection_does_not_claim_the_signal_bar(self):
        stop_event = asyncio.Event()
        platform = GuardPlatform(stop_event)
        bar_time = datetime.now(timezone.utc)
        active = [
            {
                "pair": "CORN",
                "platform": "capital_com",
                "strategy": strategy,
                "resolution": "1h",
            }
            for strategy in ("momentum", "donchian_breakout")
        ]

        async def evaluate_pair(
            _platform,
            pair,
            *,
            strategy_name,
            on_rejected_intent,
            **kwargs,
        ):
            intent = TradeIntent(
                pair=pair,
                direction=1,
                entry_price=100.0,
                stop_loss=99.0,
                take_profit=101.5,
                strategy=strategy_name,
                confidence=1.0,
                bar_time=bar_time,
            )
            if strategy_name == "momentum":
                on_rejected_intent(intent, "skipped: regime filter")
                return None
            return intent

        with patch.object(autotrade, "get_platform", lambda name: platform), \
                patch.object(pair_selector, "load_active_pairs",
                             return_value=active), \
                patch.object(journal, "list_unresolved_open", return_value=[]), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=[]), \
                patch.object(journal, "record") as record, \
                patch.object(autotrade, "evaluate_pair", evaluate_pair), \
                patch("app.spot_trading.risk_guard.daily_loss",
                      return_value=DailyLoss(0.0, 6.0, False, 0)), \
                patch.object(autotrade.subprocess, "Popen",
                             lambda *args, **kwargs: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
            )

        self.assertEqual(1, len(platform.orders))
        self.assertEqual(2, record.call_count)

    def test_usd_chf_is_covered_by_the_usd_cluster(self):
        self.assertEqual(
            "usd_fx",
            autotrade._CORRELATION_CLUSTERS["USDCHF"],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
