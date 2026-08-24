from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import Position
from app.spot_trading import autotrade, journal, pair_selector
from app.utils import singletons


class AdoptablePlatform:
    """Broker with exactly one open position whose dealId the journal
    row does not know yet — the reconcile adoption case."""
    name = "capital_com"
    demo = True
    paper_trade_only = True

    def __init__(self, position: Position, stop_event: asyncio.Event) -> None:
        self.position = position
        self.stop_event = stop_event

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def list_positions(self):
        # One cycle is enough for the reconcile pass.
        self.stop_event.set()
        return [self.position]


class RecordingDatabase:
    def __init__(self) -> None:
        self.queries = []

    def query(self, query, params=None) -> None:
        self.queries.append((query, params))

    def select(self, query, params=None) -> list:
        return []


class ReconcileAdoptionTest(IsolatedAsyncioTestCase):
    async def test_adoption_persists_broker_entry_fill(self):
        stop_event = asyncio.Event()
        now = datetime.now(timezone.utc)
        position = Position(
            id="pos-real", asset="AUDUSD", direction=1, size=1.0,
            entry_price=0.65432, stop_loss=0.64, take_profit=0.67,
            opened_at=now,
            meta={"position": {"dealId": "pos-real", "level": 0.65432}},
        )
        row = {
            "id": 776, "pair": "AUDUSD", "strategy": "donchian_breakout",
            "direction": 1, "deal_id": "o_phantom", "fill_price": None,
            "entry_price": 0.65, "stop_loss": 0.64, "take_profit": 0.67,
            "size": 1.0, "bar_time": now, "created_at": now,
        }
        adopted = []

        with patch.object(autotrade, "get_platform",
                          lambda name: AdoptablePlatform(position, stop_event)), \
                patch.object(pair_selector, "load_active_pairs",
                             lambda *a, **kw: [{"pair": "AUDUSD",
                                                "platform": "capital_com",
                                                "strategy": "donchian_breakout",
                                                "resolution": "1h"}]), \
                patch.object(journal, "list_unresolved_open",
                             lambda platform=None: [row]), \
                patch.object(journal, "list_recent_issued_times",
                             return_value=[]), \
                patch.object(journal, "update_deal_id",
                             lambda journal_id, deal_id, **kw:
                             adopted.append((journal_id, deal_id, kw))), \
                patch.object(autotrade.subprocess, "Popen", lambda *a, **kw: None):
            await autotrade.run_loop(
                platform_name="capital_com",
                strategy_name="donchian_breakout",
                poll_seconds=1,
                stop_event=stop_event,
            )

        self.assertEqual(1, len(adopted))
        self.assertEqual((776, "pos-real"), adopted[0][:2])
        self.assertEqual(0.65432, adopted[0][2].get("fill_price"))


class UpdateDealIdTest(unittest.TestCase):
    def test_adopted_fill_price_is_written(self):
        database = RecordingDatabase()

        with patch.object(singletons, "database", database):
            journal.update_deal_id(776, "pos-real", fill_price=0.65432)

        query, params = database.queries[0]
        self.assertIn("fill_price", query)
        self.assertEqual((0.65432, "pos-real", 0.65432, 776), params)

    def test_recorded_fill_price_is_not_overwritten(self):
        database = RecordingDatabase()

        with patch.object(singletons, "database", database):
            journal.update_deal_id(776, "pos-real", fill_price=0.65432)

        query, _ = database.queries[0]
        self.assertIn("COALESCE(fill_price, %s)", query)


if __name__ == "__main__":
    unittest.main()
